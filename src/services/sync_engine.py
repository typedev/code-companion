"""Pure sync engine: 3-way merge of a project's Claude data with the sync repo.

No git, no network — all logic operates on the local filesystem and a checked-out
repo working tree, so it is fully unit-testable and HOME-override friendly.

The merge base is *this machine's last-synced hashes* (from ``SyncStateStore``).
A local file is "dirty" iff its current hash differs from that base. This is what
makes concurrent edits to *different* projects on two machines safe.

Payload layout inside the sync repo, per project::

    projects/<id>/memory/*.md, MEMORY.md
    projects/<id>/sessions/*.jsonl
    projects/<id>/claude-config.json      # whitelisted ~/.claude.json slice

Rel-paths used as the merge unit are ``memory/<name>``, ``sessions/<name>`` and
``claude-config.json``.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_REL = "claude-config.json"
MEMORY_PREFIX = "memory/"
SESSIONS_PREFIX = "sessions/"


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #

def hash_bytes(data: bytes) -> str:
    """sha256 hex of raw bytes (never mtime)."""
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    """sha256 hex of a file's bytes."""
    return hash_bytes(path.read_bytes())


def sanitize_jsonl(data: bytes) -> bytes:
    """Drop a trailing incomplete JSONL line (a session file mid-append).

    Only the last line is inspected; if it is non-empty and not valid JSON it is
    truncated. Complete files are returned unchanged.
    """
    if not data:
        return data
    # Keep a trailing newline semantics: split preserving that the last element
    # after a final "\n" is empty.
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        return data  # ends cleanly with newline
    last = lines[-1]
    try:
        json.loads(last)
        return data  # last line is a complete record without trailing newline
    except json.JSONDecodeError:
        trimmed = "\n".join(lines[:-1])
        if trimmed and not trimmed.endswith("\n"):
            trimmed += "\n"
        return trimmed.encode("utf-8")


# --------------------------------------------------------------------------- #
# Pure 3-way decisions (h_* are sha256 strings or None if the file is absent)
# --------------------------------------------------------------------------- #

def decide_export(h_local: str | None, h_base: str | None, h_repo: str | None) -> str:
    """Decide what to do with one file when exporting local -> repo.

    Returns "write" | "skip" | "conflict". Additive: deletions never propagate.
    """
    if h_local is None:
        return "skip"                       # never export a local deletion
    if h_local == h_repo:
        return "skip"                       # already in sync (e.g. just adopted on import)
    if h_base is None:                      # first contact for this file
        return "write" if h_repo is None else "skip"  # seed new; never clobber
    if h_local == h_base:                   # not dirty locally
        return "skip"                       # keep whatever pull brought
    # locally dirty:
    if h_repo is None or h_repo == h_base:
        return "write"                      # clean publish
    return "conflict"                       # both sides moved


def decide_import(h_local: str | None, h_base: str | None, h_repo: str | None) -> str:
    """Decide what to do with one repo file when importing repo -> local.

    Returns "materialize" | "keep_local" | "skip" | "conflict". Additive: a local
    file absent from the payload is never deleted.
    """
    if h_repo is None:
        return "skip"                       # nothing in the repo for this rel
    if h_local is None:                     # local absent
        if h_base is not None and h_base == h_repo:
            return "skip"                   # honoured intentional local delete
        return "materialize"                # first-contact fill or remote-new / resurrect
    if h_local == h_repo:
        return "skip"                       # already equal
    if h_local == h_base:
        return "materialize"                # remote-only change
    if h_repo == h_base:
        return "keep_local"                 # local-only change (export publishes it)
    return "conflict"                       # both changed


def merge_session_pair(local_bytes: bytes, repo_bytes: bytes) -> bytes:
    """Sessions are append-only: the longer byte string is the superset."""
    return local_bytes if len(local_bytes) >= len(repo_bytes) else repo_bytes


# --------------------------------------------------------------------------- #
# .claude.json slice
# --------------------------------------------------------------------------- #

def _load_claude_json(claude_json_path: Path) -> dict:
    if not claude_json_path.exists():
        return {}
    try:
        data = json.loads(claude_json_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def extract_claude_json_slice(
    claude_json_path: Path, local_abs_path: str, fields: list[str]
) -> dict:
    """Return the whitelisted subset of ~/.claude.json[projects][<abs>]."""
    data = _load_claude_json(claude_json_path)
    project = data.get("projects", {}).get(local_abs_path, {})
    return {f: project[f] for f in fields if f in project}


def canonical_slice_bytes(slice_dict: dict) -> bytes:
    """Deterministic serialization so both machines hash identical slices equally."""
    return json.dumps(slice_dict, sort_keys=True, indent=2).encode("utf-8")


def apply_claude_json_slice(
    claude_json_path: Path, local_abs_path: str, slice_dict: dict, fields: list[str]
) -> None:
    """Surgically patch ~/.claude.json[projects][<abs>] with whitelisted fields.

    Loads, patches only the whitelisted keys, and atomically rewrites. Never a
    whole-file overwrite; unknown/other fields and other projects are untouched.
    """
    data = _load_claude_json(claude_json_path)
    if not isinstance(data, dict):
        data = {}
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(local_abs_path, {})
    for f in fields:
        if f in slice_dict:
            entry[f] = slice_dict[f]
    tmp = claude_json_path.with_name(claude_json_path.name + ".cc-sync.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(claude_json_path)


# --------------------------------------------------------------------------- #
# Local project view (I/O adapter — abstracts ~/.claude for testability)
# --------------------------------------------------------------------------- #

@dataclass
class LocalProjectView:
    """Maps sync rel-paths to concrete local locations for one project."""

    local_abs_path: str
    project_dir: Path       # ~/.claude/projects/<encoded>
    memory_dir: Path        # project_dir/memory
    claude_json_path: Path  # ~/.claude.json
    claude_json_fields: list[str] = field(default_factory=list)

    # --- reads ---

    def read_local_bytes(self, rel: str) -> bytes | None:
        if rel == CONFIG_REL:
            slice_dict = extract_claude_json_slice(
                self.claude_json_path, self.local_abs_path, self.claude_json_fields
            )
            return canonical_slice_bytes(slice_dict) if slice_dict else None
        path = self._local_path(rel)
        if path is None or not path.exists():
            return None
        data = path.read_bytes()
        if rel.startswith(SESSIONS_PREFIX):
            data = sanitize_jsonl(data)
        return data

    def local_hashes(self) -> dict[str, str]:
        """Hashes of all present local files, keyed by rel-path."""
        result: dict[str, str] = {}
        for rel in self._iter_local_rels():
            data = self.read_local_bytes(rel)
            if data is not None:
                result[rel] = hash_bytes(data)
        return result

    def _iter_local_rels(self):
        if self.memory_dir.exists():
            for p in sorted(self.memory_dir.rglob("*")):
                if p.is_file():
                    yield MEMORY_PREFIX + p.relative_to(self.memory_dir).as_posix()
        if self.project_dir.exists():
            for p in sorted(self.project_dir.glob("*.jsonl")):
                yield SESSIONS_PREFIX + p.name
        if self.read_local_bytes(CONFIG_REL) is not None:
            yield CONFIG_REL

    def _local_path(self, rel: str) -> Path | None:
        if rel.startswith(MEMORY_PREFIX):
            return self.memory_dir / rel[len(MEMORY_PREFIX):]
        if rel.startswith(SESSIONS_PREFIX):
            return self.project_dir / rel[len(SESSIONS_PREFIX):]
        return None  # CONFIG_REL is virtual

    # --- writes ---

    def write_local(self, rel: str, data: bytes) -> None:
        if rel == CONFIG_REL:
            try:
                slice_dict = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            apply_claude_json_slice(
                self.claude_json_path, self.local_abs_path, slice_dict,
                self.claude_json_fields,
            )
            return
        path = self._local_path(rel)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".cc-sync.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic

    def snapshot(self, snapshot_dir: Path) -> None:
        """Copy the current local target subtree into snapshot_dir (machine-local)."""
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for rel in self._iter_local_rels():
            data = self.read_local_bytes(rel)
            if data is None:
                continue
            dest = snapshot_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)


# --------------------------------------------------------------------------- #
# Repo project directory helpers
# --------------------------------------------------------------------------- #

def repo_hashes(repo_project_dir: Path) -> dict[str, str]:
    """Public alias: hashes of all payload files in a repo project dir."""
    return _repo_hashes(repo_project_dir)


def _repo_hashes(repo_project_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not repo_project_dir.exists():
        return result
    mem = repo_project_dir / "memory"
    if mem.exists():
        for p in sorted(mem.rglob("*")):
            if p.is_file():
                result[MEMORY_PREFIX + p.relative_to(mem).as_posix()] = hash_file(p)
    sess = repo_project_dir / "sessions"
    if sess.exists():
        for p in sorted(sess.glob("*.jsonl")):
            result[SESSIONS_PREFIX + p.name] = hash_file(p)
    cfg = repo_project_dir / CONFIG_REL
    if cfg.exists():
        result[CONFIG_REL] = hash_file(cfg)
    return result


def _repo_read(repo_project_dir: Path, rel: str) -> bytes | None:
    p = repo_project_dir / rel
    return p.read_bytes() if p.exists() else None


def _repo_write(repo_project_dir: Path, rel: str, data: bytes) -> None:
    dest = repo_project_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

@dataclass
class ExportReport:
    written: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    materialized: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    snapshot_path: str | None = None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def export_project(
    view: LocalProjectView, repo_project_dir: Path, base: dict[str, str]
) -> ExportReport:
    """Write locally-dirty files into the repo working tree (dirty-only)."""
    report = ExportReport()
    repo_project_dir.mkdir(parents=True, exist_ok=True)
    local = view.local_hashes()
    repo = _repo_hashes(repo_project_dir)

    all_rels = set(local) | set(repo) | set(base)
    for rel in sorted(all_rels):
        h_local = local.get(rel)
        h_base = base.get(rel)
        h_repo = repo.get(rel)

        # Sessions: append-only union — publish local if it is a superset.
        if rel.startswith(SESSIONS_PREFIX):
            if h_local is None:
                continue
            if h_repo is None:
                _repo_write(repo_project_dir, rel, view.read_local_bytes(rel) or b"")
                report.written.append(rel)
            elif h_local != h_repo:
                merged = merge_session_pair(
                    view.read_local_bytes(rel) or b"",
                    _repo_read(repo_project_dir, rel) or b"",
                )
                if hash_bytes(merged) != h_repo:
                    _repo_write(repo_project_dir, rel, merged)
                    report.written.append(rel)
            continue

        action = decide_export(h_local, h_base, h_repo)
        if action == "write":
            _repo_write(repo_project_dir, rel, view.read_local_bytes(rel) or b"")
            report.written.append(rel)
        elif action == "conflict":
            report.conflicts.append(rel)
    return report


def import_project(
    view: LocalProjectView,
    repo_project_dir: Path,
    base: dict[str, str],
    snapshot_dir: Path,
) -> ImportReport:
    """Safely apply repo files to local: snapshot -> validate -> atomic write.

    Additive (never deletes local files). On a both-sides change, keeps local and
    stashes the repo copy in the snapshot as ``<name>.remote`` for manual merge.
    """
    report = ImportReport()
    local = view.local_hashes()
    repo = _repo_hashes(repo_project_dir)

    # Nothing to import if the repo has no payload for this project.
    if not repo:
        return report

    # 1. snapshot current local state (the escape hatch).
    view.snapshot(snapshot_dir)
    report.snapshot_path = str(snapshot_dir)

    all_rels = set(local) | set(repo) | set(base)
    for rel in sorted(all_rels):
        h_local = local.get(rel)
        h_base = base.get(rel)
        h_repo = repo.get(rel)

        # Sessions: union — materialize any repo session that is a superset.
        if rel.startswith(SESSIONS_PREFIX):
            if h_repo is None:
                continue
            repo_bytes = _repo_read(repo_project_dir, rel) or b""
            if h_local is None:
                view.write_local(rel, repo_bytes)
                report.materialized.append(rel)
            elif h_local != h_repo:
                local_bytes = view.read_local_bytes(rel) or b""
                merged = merge_session_pair(local_bytes, repo_bytes)
                if hash_bytes(merged) != h_local:
                    view.write_local(rel, merged)
                    report.materialized.append(rel)
            continue

        action = decide_import(h_local, h_base, h_repo)
        if action == "materialize":
            data = _repo_read(repo_project_dir, rel)
            if data is not None and validate_file(rel, data):
                view.write_local(rel, data)
                report.materialized.append(rel)
        elif action == "conflict":
            # Keep local; stash the repo version alongside the snapshot.
            repo_bytes = _repo_read(repo_project_dir, rel)
            if repo_bytes is not None:
                stash = snapshot_dir / (rel + ".remote")
                stash.parent.mkdir(parents=True, exist_ok=True)
                stash.write_bytes(repo_bytes)
            report.conflicts.append(rel)
    return report


def validate_file(rel: str, data: bytes) -> bool:
    """Validate a payload file before materializing it locally."""
    if rel.endswith(".json"):
        try:
            json.loads(data.decode("utf-8"))
            return True
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
    return True  # *.md / *.jsonl accepted (jsonl tolerated as-is)
