"""SyncService — orchestrates a full cross-machine Sync run.

Singleton mirroring ``ProjectStatusService``: blocking work meant to run on a
worker thread, results cached to disk for instant repaint. Drives the whole
step 0-8 sequence from docs/plan-sync-across-machines.md under a single
``SyncLock``.
"""

import json
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..models.sync import (
    SCHEMA_VERSION,
    BackupEntry,
    ProjectSyncStatus,
    SyncResult,
    SyncState,
)
from ..utils import claude_paths, git_auth
from ..utils.git_worktree import is_linked_worktree
from ..utils.project_identity import (
    ProjectIdentity,
    origin_url,
    resolve_project_identity,
)
from . import message_store
from . import session_summary_service
from . import sync_engine as E
from .config_path import get_config_dir
from .git_service import AuthenticationRequired
from .settings_service import SettingsService
from .sync_engine import LocalProjectView, decide_export, decide_import
from .sync_lock import SyncLock
from .sync_recovery import recover
from .sync_repo import RebaseConflict, SyncRepo

_GLOBAL_ID = "__global__"


def _safe_dirname(name: str) -> str:
    """A filesystem-safe folder name for a restored project."""
    import re

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return slug or "project"


class SyncService:
    """Singleton orchestrator for cross-machine sync."""

    _instance: "SyncService | None" = None

    def __init__(self):
        self.config_dir = get_config_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.clone_path = self.config_dir / "sync"
        self.snapshots_dir = self.config_dir / "sync-snapshots"
        self.status_cache_file = self.config_dir / "sync_status_cache.json"
        self.settings = SettingsService.get_instance()
        # Import lazily to avoid a hard import cycle at module load.
        from .sync_state_store import SyncStateStore

        self.state = SyncStateStore(self.config_dir / "sync_state.json")
        self._status_cache: dict[str, dict] = self._load_status_cache()

    @classmethod
    def get_instance(cls) -> "SyncService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ #
    # config
    # ------------------------------------------------------------------ #

    def is_configured(self) -> bool:
        return bool(
            self.settings.get("sync.enabled") and self.settings.get("sync.repo_url")
        )

    @property
    def _fields(self) -> list[str]:
        return list(self.settings.get("sync.claude_json_fields", []))

    # ------------------------------------------------------------------ #
    # status cache (for instant repaint before a run)
    # ------------------------------------------------------------------ #

    def get_cached_status(self, local_path: str) -> ProjectSyncStatus | None:
        entry = self._status_cache.get(str(Path(local_path).resolve()))
        if not entry:
            return None
        try:
            state = SyncState(entry.get("state", "not_configured"))
        except ValueError:
            state = SyncState.NOT_CONFIGURED
        return ProjectSyncStatus(
            project_id=entry.get("project_id", ""),
            local_path=str(Path(local_path).resolve()),
            state=state,
            detail=entry.get("detail", ""),
            conflict_files=list(entry.get("conflict_files", [])),
            refreshed_at=self._parse_dt(entry.get("refreshed_at")),
        )

    def _store_status(self, status: ProjectSyncStatus) -> None:
        self._status_cache[str(Path(status.local_path).resolve())] = {
            "project_id": status.project_id,
            "state": status.state.value,
            "detail": status.detail,
            "conflict_files": status.conflict_files,
            "refreshed_at": (status.refreshed_at or datetime.now(timezone.utc)).isoformat(),
        }

    def _load_status_cache(self) -> dict[str, dict]:
        if not self.status_cache_file.exists():
            return {}
        try:
            data = json.loads(self.status_cache_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_status_cache(self) -> None:
        try:
            tmp = self.status_cache_file.with_name(self.status_cache_file.name + ".tmp")
            tmp.write_text(json.dumps(self._status_cache, indent=2), encoding="utf-8")
            tmp.replace(self.status_cache_file)
        except OSError:
            pass

    @staticmethod
    def _parse_dt(value):
        try:
            return datetime.fromisoformat(value) if value else None
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    # the run
    # ------------------------------------------------------------------ #

    def sync(
        self,
        project_paths: list[str],
        credentials: tuple[str, str] | None = None,
        progress=None,
    ) -> SyncResult:
        """Run a full bidirectional sync. Blocking — call off the GTK thread.

        Raises ``AuthenticationRequired`` so the caller can drive the creds dialog.
        """
        result = SyncResult()
        lock = SyncLock(self.clone_path)
        if not lock.acquire():
            result.error = "busy"
            for path in project_paths:
                self._emit(result, progress, self._status(path, "", SyncState.PAUSED))
            return result
        try:
            self._run(project_paths, credentials, result, progress)
        except AuthenticationRequired:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as ERROR, never crash the UI
            result.error = str(exc)
            result.global_ok = False
        finally:
            self._save_status_cache()
            lock.release()
        return result

    def _run(self, project_paths, credentials, result, progress):
        repo = SyncRepo(self.clone_path, self.settings.get("sync.repo_url"))

        # 0. clone if missing + heal a crashed prior run.
        if not repo.exists_locally():
            repo.clone(credentials)
        recover(repo, self.settings.get("sync.last_good_commit") or None)

        # 1. integrate remote.
        repo.pull_rebase(credentials)

        # 2. schema guard.
        manifest = self._read_manifest(repo)
        if manifest.get("schemaVersion", 0) > SCHEMA_VERSION:
            for path in project_paths:
                self._emit(
                    result, progress,
                    self._status(path, "", SyncState.ERROR, detail="sync repo is newer; update the app"),
                )
            result.global_ok = False
            return

        # Resolve identities; non-syncable projects are reported and skipped.
        syncable: list[tuple[str, ProjectIdentity]] = []
        for path in project_paths:
            # A linked worktree shares its parent's identity — syncing it would
            # collide with the parent over one slot. It's synced via the parent.
            if is_linked_worktree(path):
                self._emit(result, progress, self._status(
                    path, "", SyncState.NOT_CONFIGURED,
                    detail="linked worktree (synced via its parent)"))
                continue
            ident = resolve_project_identity(path)
            if ident is None:
                self._emit(result, progress, self._status(path, "", SyncState.NOT_CONFIGURED))
            else:
                syncable.append((str(Path(path).resolve()), ident))

        run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        snap_root = self.snapshots_dir / run_stamp

        # 3 + 4. per-project import (inbound) then export (outbound).
        per_project_reports: dict[str, tuple[str, ProjectIdentity, object, object]] = {}
        for local_path, ident in syncable:
            view = self._view(local_path, ident)
            repo_proj = repo.worktree() / "projects" / ident.project_id
            base = self.state.get_base(ident.project_id)

            imp = E.import_project(view, repo_proj, base, snap_root / ident.project_id)
            exp = E.export_project(view, repo_proj, base)
            self._write_meta(repo_proj, ident)
            per_project_reports[ident.project_id] = (local_path, ident, imp, exp)

        # Global layer (plans + settings).
        global_conflicts = self._sync_global(repo, snap_root)

        # Backup mode also records the project registry for clean-OS restore.
        if self.settings.get("sync.mode") == "backup":
            self._export_registry(repo, syncable)

        # Ensure manifest is present/current.
        self._write_manifest(repo)

        # 5. commit.
        host = socket.gethostname()
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        changed = repo.commit_all(f"sync {host} {stamp}")

        # 6. integrate + push.
        push_blocked_ids: set[str] = set()
        if changed:
            try:
                repo.pull_rebase(credentials)
            except RebaseConflict as conflict:
                repo.abort_rebase()
                push_blocked_ids = self._ids_from_paths(conflict.paths)
            else:
                repo.push(credentials)
        elif repo.has_unpushed_commits():
            repo.push(credentials)

        # 7 + 8. per-project status, base advance, manifest / last_good_commit.
        for pid, (local_path, ident, imp, exp) in per_project_reports.items():
            conflicts = list(imp.conflicts) + list(exp.conflicts)
            if pid in push_blocked_ids:
                conflicts.append("(push blocked by remote change)")
            state, detail = self._classify(imp, exp, conflicts)
            status = self._status(
                local_path, pid, state, detail=detail, conflict_files=conflicts,
                snapshot=imp.snapshot_path,
            )
            self._emit(result, progress, status)
            self._advance_base(ident, local_path, repo, conflicts)

        if global_conflicts:
            result.global_ok = False
        if not push_blocked_ids:
            head = repo.head_hash()
            if head:
                self.settings.set("sync.last_good_commit", head)

    # ------------------------------------------------------------------ #
    # per-project helpers
    # ------------------------------------------------------------------ #

    def _view(self, local_path: str, ident: ProjectIdentity) -> LocalProjectView:
        return LocalProjectView(
            local_abs_path=local_path,
            project_dir=claude_paths.project_dir(local_path),
            memory_dir=claude_paths.project_memory_dir(local_path),
            claude_json_path=claude_paths.claude_json(),
            claude_json_fields=self._fields,
        )

    def _advance_base(self, ident, local_path, repo, conflicts) -> None:
        """New base = files where local and repo now agree (conflicts keep old base)."""
        view = self._view(local_path, ident)
        repo_proj = repo.worktree() / "projects" / ident.project_id
        local_now = view.local_hashes()
        repo_now = E.repo_hashes(repo_proj)
        new_base = dict(self.state.get_base(ident.project_id))
        for rel in set(local_now) | set(repo_now):
            hl, hr = local_now.get(rel), repo_now.get(rel)
            if hl is not None and hl == hr:
                new_base[rel] = hl
        self.state.set_base(ident.project_id, new_base)

    @staticmethod
    def _classify(imp, exp, conflicts) -> tuple[SyncState, str]:
        if conflicts:
            return SyncState.CONFLICT, f"{len(conflicts)} conflict(s)"
        if exp.written:
            return SyncState.AHEAD, f"pushed {len(exp.written)} file(s)"
        if imp.materialized:
            return SyncState.BEHIND, f"updated {len(imp.materialized)} file(s)"
        return SyncState.SYNCED, ""

    @staticmethod
    def _ids_from_paths(paths: list[str]) -> set[str]:
        ids = set()
        for p in paths:
            parts = p.split("/")
            if len(parts) >= 2 and parts[0] == "projects":
                ids.add(parts[1])
        return ids

    def _write_meta(self, repo_proj: Path, ident: ProjectIdentity) -> None:
        repo_proj.mkdir(parents=True, exist_ok=True)
        meta = {
            "project_id": ident.project_id,
            "id_source": ident.id_source,
            "canonical_remote": ident.canonical_remote,
        }
        (repo_proj / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ #
    # global layer (plans + settings)
    # ------------------------------------------------------------------ #

    def _sync_global(self, repo: SyncRepo, snap_root: Path) -> list[str]:
        global_dir = repo.worktree() / "global"
        plans_local = claude_paths.plans_dir()
        settings_local = claude_paths.settings_json()
        summaries_local = session_summary_service.summaries_dir()
        messages_local = message_store.messages_dir()

        def local_bytes() -> dict[str, bytes]:
            out: dict[str, bytes] = {}
            if plans_local.exists():
                for p in sorted(plans_local.glob("*.md")):
                    out["plans/" + p.name] = p.read_bytes()
            if summaries_local.exists():
                for p in sorted(summaries_local.glob("*.md")):
                    out["session-summaries/" + p.name] = p.read_bytes()
            if messages_local.exists():
                # Nested: messages/<thread_id>/<event_id>.json (append-only, immutable).
                for p in sorted(messages_local.rglob("*.json")):
                    rel = p.relative_to(messages_local).as_posix()
                    out["messages/" + rel] = p.read_bytes()
            if settings_local.exists():
                out["settings.json"] = settings_local.read_bytes()
            return out

        def repo_bytes() -> dict[str, bytes]:
            out: dict[str, bytes] = {}
            rp = global_dir / "plans"
            if rp.exists():
                for p in sorted(rp.glob("*.md")):
                    out["plans/" + p.name] = p.read_bytes()
            rs = global_dir / "session-summaries"
            if rs.exists():
                for p in sorted(rs.glob("*.md")):
                    out["session-summaries/" + p.name] = p.read_bytes()
            rm = global_dir / "messages"
            if rm.exists():
                for p in sorted(rm.rglob("*.json")):
                    rel = p.relative_to(rm).as_posix()
                    out["messages/" + rel] = p.read_bytes()
            sj = global_dir / "settings.json"
            if sj.exists():
                out["settings.json"] = sj.read_bytes()
            return out

        def local_target(rel: str) -> Path:
            if rel.startswith("plans/"):
                return plans_local / rel[len("plans/"):]
            if rel.startswith("session-summaries/"):
                return summaries_local / rel[len("session-summaries/"):]
            if rel.startswith("messages/"):
                return messages_local / rel[len("messages/"):]
            return settings_local  # settings.json

        def repo_target(rel: str) -> Path:
            return global_dir / rel

        local = local_bytes()
        repo_now = repo_bytes()
        base = self.state.get_base(_GLOBAL_ID)
        conflicts: list[str] = []

        # snapshot current local global state.
        snap = snap_root / _GLOBAL_ID
        for rel, data in local.items():
            dest = snap / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        all_rels = set(local) | set(repo_now) | set(base)
        # import (repo -> local)
        for rel in sorted(all_rels):
            hl = E.hash_bytes(local[rel]) if rel in local else None
            hr = E.hash_bytes(repo_now[rel]) if rel in repo_now else None
            action = decide_import(hl, base.get(rel), hr)
            if action == "materialize" and rel in repo_now:
                if E.validate_file(rel, repo_now[rel]):
                    t = local_target(rel)
                    t.parent.mkdir(parents=True, exist_ok=True)
                    tmp = t.with_name(t.name + ".cc-sync.tmp")
                    tmp.write_bytes(repo_now[rel])
                    tmp.replace(t)
            elif action == "conflict":
                stash = snap / (rel + ".remote")
                stash.parent.mkdir(parents=True, exist_ok=True)
                if rel in repo_now:
                    stash.write_bytes(repo_now[rel])
                conflicts.append("global/" + rel)

        # re-read local after import for export decisions.
        local = local_bytes()
        for rel in sorted(set(local) | set(repo_now) | set(base)):
            hl = E.hash_bytes(local[rel]) if rel in local else None
            hr = E.hash_bytes(repo_now[rel]) if rel in repo_now else None
            action = decide_export(hl, base.get(rel), hr)
            if action == "write" and rel in local:
                t = repo_target(rel)
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(local[rel])
            elif action == "conflict":
                conflicts.append("global/" + rel)

        # advance global base (agreed files).
        local = local_bytes()
        repo_now = repo_bytes()
        new_base = dict(base)
        for rel in set(local) | set(repo_now):
            hl = E.hash_bytes(local[rel]) if rel in local else None
            hr = E.hash_bytes(repo_now[rel]) if rel in repo_now else None
            if hl is not None and hl == hr:
                new_base[rel] = hl
        self.state.set_base(_GLOBAL_ID, new_base)
        return conflicts

    # ------------------------------------------------------------------ #
    # backup mode (registry export + restore)
    # ------------------------------------------------------------------ #

    def _export_registry(self, repo: SyncRepo, syncable) -> None:
        """Write global/registry.json — a portable map of all synced projects.

        Additive union by project_id: this machine's entries are updated, other
        machines' entries are preserved. Enables restoring the project list on a
        clean OS.
        """
        from .project_registry import ProjectRegistry

        reg = ProjectRegistry()
        path = repo.worktree() / "global" / "registry.json"
        existing: dict[str, dict] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for e in data.get("projects", []):
                    if e.get("project_id"):
                        existing[e["project_id"]] = e
            except (json.JSONDecodeError, OSError):
                pass
        for local_path, ident in syncable:
            existing[ident.project_id] = {
                "project_id": ident.project_id,
                "name": reg.get_name(local_path),
                "remote_url": origin_url(local_path),
                "canonical_remote": ident.canonical_remote,
                "source_path": local_path,
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"projects": sorted(existing.values(), key=lambda e: e.get("project_id", ""))}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def backup_manifest(self) -> list[BackupEntry]:
        """Read the backup registry from the local clone (empty if none)."""
        path = self.clone_path / "global" / "registry.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [
            BackupEntry(
                project_id=e.get("project_id", ""),
                name=e.get("name", ""),
                remote_url=e.get("remote_url"),
                canonical_remote=e.get("canonical_remote"),
                source_path=e.get("source_path", ""),
            )
            for e in data.get("projects", [])
            if e.get("project_id")
        ]

    def list_restorable(self, registered_paths: list[str]) -> list[BackupEntry]:
        """Backup entries whose project is not registered on this machine."""
        known: set[str] = set()
        for p in registered_paths:
            if is_linked_worktree(p):
                continue  # worktree shares the parent's id; the parent covers it
            ident = resolve_project_identity(p)
            if ident:
                known.add(ident.project_id)
        return [e for e in self.backup_manifest() if e.project_id not in known]

    def restore_project(
        self, entry: BackupEntry, base_dir: str, credentials: tuple[str, str] | None = None
    ) -> str:
        """Clone a backed-up project's git repo under base_dir and register it.

        Returns the new local path. A subsequent Sync materializes its history
        and memory. Raises AuthenticationRequired / RuntimeError.
        """
        if not entry.remote_url:
            raise RuntimeError(f"No remote URL recorded for '{entry.name}'")
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
        dir_name = _safe_dirname(entry.name or entry.project_id)
        target = base / dir_name
        n = 2
        while target.exists():
            target = base / f"{dir_name}-{n}"
            n += 1

        env, askpass = git_auth.build_auth_env(entry.remote_url, credentials, base)
        try:
            result = subprocess.run(
                ["git", "clone", entry.remote_url, str(target)],
                cwd=str(base),
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
        finally:
            if askpass:
                try:
                    Path(askpass).unlink()
                except OSError:
                    pass
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "").strip()
            if git_auth.is_auth_error(error):
                raise AuthenticationRequired(error, entry.remote_url)
            raise RuntimeError(error or "Clone failed")

        from .project_registry import ProjectRegistry

        ProjectRegistry().register_project(str(target), entry.name)
        if credentials:
            git_auth.store_credentials(entry.remote_url, credentials, target)
        return str(target)

    # ------------------------------------------------------------------ #
    # manifest
    # ------------------------------------------------------------------ #

    def _read_manifest(self, repo: SyncRepo) -> dict:
        path = repo.worktree() / "manifest.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_manifest(self, repo: SyncRepo) -> None:
        path = repo.worktree() / "manifest.json"
        app_version = self.settings.get("app.version", "")
        path.write_text(
            json.dumps({"schemaVersion": SCHEMA_VERSION, "appVersion": app_version}, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    # status plumbing
    # ------------------------------------------------------------------ #

    def _status(self, local_path, project_id, state, detail="", conflict_files=None, snapshot=None):
        return ProjectSyncStatus(
            project_id=project_id,
            local_path=str(Path(local_path).resolve()),
            state=state,
            detail=detail,
            conflict_files=conflict_files or [],
            snapshot_path=snapshot,
            refreshed_at=datetime.now(timezone.utc),
        )

    def _emit(self, result: SyncResult, progress, status: ProjectSyncStatus) -> None:
        result.per_project[status.local_path] = status
        self._store_status(status)
        if progress:
            try:
                progress(status)
            except Exception:  # noqa: BLE001 — progress UI must never break the run
                pass
