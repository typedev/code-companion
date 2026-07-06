"""Git status service for the Project Manager dashboard.

Provides two tiers of per-project status:

* **Local** (cheap, offline): whether the folder is a repo, has a remote, has
  uncommitted changes, and how many commits are unpushed. Computed via plain
  ``git`` subprocess calls so it is safe to run in a background thread (pygit2
  is avoided in worker threads to sidestep GIL/thread-safety concerns, matching
  ``project_window._update_git_badge``).
* **Remote** (network + GitHub auth): commits behind the upstream (after a real
  ``git fetch``), plus open PR and issue counts. Cached to disk so the
  "last updated" label survives window reopen.

All public methods perform blocking work and MUST be called off the GTK main
thread (see ``project_manager`` for the threading pattern).
"""

import json
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config_path import get_config_dir
from .issues_service import IssuesService
from ..utils import git_auth

# Never let git block on an interactive credential / known-hosts prompt.
_GIT_ENV = git_auth.build_git_env()  # LC_ALL=C + GIT_TERMINAL_PROMPT=0 (roadmap 3.4)


@dataclass
class LocalStatus:
    """Cheap, offline git markers for a project."""

    has_repo: bool = False
    has_remote: bool = False
    dirty: bool = False
    ahead: int = 0


@dataclass
class RemoteStatus:
    """Network-derived markers for a project (all optional).

    ``None`` means "unknown / not applicable" (offline, no token, or not a
    GitHub repo) and should render as an absent badge rather than zero.
    """

    behind: int | None = None
    pr_count: int | None = None
    issue_count: int | None = None
    refreshed_at: datetime | None = None


def _run_git(args: list[str], cwd: str, timeout: int = 10) -> str | None:
    """Run a git command, returning stripped stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_GIT_ENV,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


class ProjectStatusService:
    """Singleton computing/caching per-project git status for Project Manager."""

    _instance: "ProjectStatusService | None" = None

    def __init__(self):
        self.config_dir = get_config_dir()
        self.cache_file = self.config_dir / "project_status_cache.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # Guards _cache + the JSON file: refresh runs on multiple worker threads
        # while the main thread reads cached values (roadmap 2.7).
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = self._load_cache()

    @classmethod
    def get_instance(cls) -> "ProjectStatusService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Local status (offline, thread-safe subprocess)
    # ------------------------------------------------------------------
    def get_local_status(self, path: str) -> LocalStatus:
        """Compute cheap, offline git markers for ``path``."""
        cwd = str(path)
        if not Path(cwd).is_dir():
            return LocalStatus()

        git_dir = _run_git(["rev-parse", "--git-dir"], cwd)
        if git_dir is None:
            return LocalStatus(has_repo=False)

        status = LocalStatus(has_repo=True)

        remotes = _run_git(["remote"], cwd)
        status.has_remote = bool(remotes)

        porcelain = _run_git(["status", "--porcelain"], cwd)
        status.dirty = bool(porcelain)

        # Unpushed commits relative to the tracked upstream (0 if none).
        ahead = _run_git(["rev-list", "--count", "@{upstream}..HEAD"], cwd)
        if ahead is not None and ahead.isdigit():
            status.ahead = int(ahead)

        return status

    # ------------------------------------------------------------------
    # Remote status (network + GitHub auth)
    # ------------------------------------------------------------------
    def refresh_remote_status(
        self, path: str, credentials: tuple[str, str] | None = None
    ) -> RemoteStatus:
        """Fetch and query GitHub for behind/PR/issue counts, then cache.

        Raises ``AuthenticationRequired`` (from the GitHub layer) so the caller
        can drive the credentials dialog. ``git fetch`` failures are swallowed
        (behind falls back to whatever the local upstream ref knows).
        """
        cwd = str(path)
        result = RemoteStatus(refreshed_at=datetime.now(timezone.utc))

        # Best-effort fetch so "behind" reflects the real remote tip.
        _run_git(["fetch", "--quiet"], cwd, timeout=30)

        behind = _run_git(["rev-list", "--count", "HEAD..@{upstream}"], cwd)
        if behind is not None and behind.isdigit():
            result.behind = int(behind)

        # PR / issue counts only for GitHub origins with a token.
        issues = IssuesService(cwd)
        if issues.is_github_repo():
            prs = issues.list_pull_requests("open", credentials=credentials)
            result.pr_count = len(prs)
            open_issues = issues.list_issues("open", credentials=credentials)
            result.issue_count = len(open_issues)

        self._store(cwd, result)
        return result

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def get_cached_remote(self, path: str) -> RemoteStatus | None:
        """Return the last cached remote status for ``path``, if any."""
        with self._lock:
            entry = self._cache.get(str(Path(path).resolve()))
            entry = dict(entry) if entry else None
        if not entry:
            return None
        refreshed = entry.get("refreshed_at")
        return RemoteStatus(
            behind=entry.get("behind"),
            pr_count=entry.get("pr_count"),
            issue_count=entry.get("issue_count"),
            refreshed_at=self._parse_dt(refreshed) if refreshed else None,
        )

    def _store(self, path: str, status: RemoteStatus):
        """Persist a remote status to the in-memory + on-disk cache (thread-safe)."""
        entry = {
            "behind": status.behind,
            "pr_count": status.pr_count,
            "issue_count": status.issue_count,
            "refreshed_at": status.refreshed_at.isoformat()
            if status.refreshed_at
            else None,
        }
        with self._lock:
            self._cache[str(Path(path).resolve())] = entry
            self._save_cache()

    def _load_cache(self) -> dict[str, dict]:
        if not self.cache_file.exists():
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except OSError:
            pass

    @staticmethod
    def _parse_dt(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
