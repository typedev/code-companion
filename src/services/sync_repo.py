"""Thin git-CLI wrapper for the single sync clone.

Reuses the GIT_ASKPASS auth mechanism from ``git_auth`` (shared with GitService)
and raises the same ``AuthenticationRequired`` exception so the Project Manager's
existing credentials dialog flow can drive it. Adds ``RebaseConflict`` for the
same-file/both-dirty case.
"""

import subprocess
from pathlib import Path

from ..utils import git_auth
from .git_service import AuthenticationRequired

# Fallback commit identity for the sync repo (bookkeeping commits, not the user's).
_SYNC_USER_NAME = "Code Companion Sync"
_SYNC_USER_EMAIL = "sync@code-companion.local"


class RebaseConflict(Exception):
    """Raised when pull --rebase hits a real conflict; carries the paths."""

    def __init__(self, paths: list[str]):
        super().__init__("Rebase conflict: " + ", ".join(paths))
        self.paths = paths


class SyncRepo:
    """Operate on the local sync clone at ``local_path`` tracking ``remote_url``."""

    def __init__(self, local_path: str | Path, remote_url: str):
        self.local_path = Path(local_path)
        self.remote_url = remote_url

    # ------------------------------------------------------------------ #
    # low-level runners
    # ------------------------------------------------------------------ #

    def _run(self, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """Run a plain git command inside the clone (no network/auth)."""
        return subprocess.run(
            ["git", *args],
            cwd=str(self.local_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _run_auth(
        self,
        args: list[str],
        credentials: tuple[str, str] | None,
        cwd: Path | None = None,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess:
        """Run a git command that talks to the remote, with credentials injected."""
        env, askpass = git_auth.build_auth_env(self.remote_url, credentials, cwd or self.local_path)
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(cwd or self.local_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        finally:
            if askpass:
                try:
                    Path(askpass).unlink()
                except OSError:
                    pass
        return result

    @staticmethod
    def _err(result: subprocess.CompletedProcess) -> str:
        return (result.stderr or "").strip() or (result.stdout or "").strip()

    # ------------------------------------------------------------------ #
    # state
    # ------------------------------------------------------------------ #

    def exists_locally(self) -> bool:
        return (self.local_path / ".git").exists()

    def worktree(self) -> Path:
        return self.local_path

    def head_hash(self) -> str:
        result = self._run(["rev-parse", "HEAD"])
        return result.stdout.strip() if result.returncode == 0 else ""

    def is_mid_rebase(self) -> bool:
        git_dir = self.local_path / ".git"
        return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()

    def _conflicted_paths(self) -> list[str]:
        result = self._run(["diff", "--name-only", "--diff-filter=U"])
        if result.returncode != 0:
            return []
        return [ln for ln in result.stdout.splitlines() if ln.strip()]

    def has_unpushed_commits(self) -> bool:
        # If upstream is configured, compare; otherwise any commit counts as unpushed.
        upstream = self._run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if upstream.returncode == 0:
            out = self._run(["log", "@{u}..HEAD", "--oneline"])
            return bool(out.stdout.strip())
        head = self._run(["rev-parse", "HEAD"])
        return head.returncode == 0 and bool(head.stdout.strip())

    def _current_branch(self) -> str:
        result = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        return result.stdout.strip() if result.returncode == 0 else "main"

    # ------------------------------------------------------------------ #
    # operations
    # ------------------------------------------------------------------ #

    def clone(self, credentials: tuple[str, str] | None = None) -> None:
        """Clone the remote into local_path (parent must be writable)."""
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._run_auth(
            ["clone", self.remote_url, str(self.local_path)],
            credentials,
            cwd=self.local_path.parent,
            timeout=300,
        )
        if result.returncode != 0:
            error = self._err(result)
            if git_auth.is_auth_error(error):
                raise AuthenticationRequired(error, self.remote_url)
            raise RuntimeError(error or "Clone failed")
        self._ensure_identity()
        # Empty remote -> pin a deterministic branch so every machine agrees.
        if not self.head_hash():
            self._run(["checkout", "-B", "main"])
        if credentials:
            git_auth.store_credentials(self.remote_url, credentials, self.local_path)

    def _ensure_identity(self) -> None:
        """Set a local commit identity if the environment has none."""
        for key, val in (("user.name", _SYNC_USER_NAME), ("user.email", _SYNC_USER_EMAIL)):
            existing = self._run(["config", key])
            if existing.returncode != 0 or not existing.stdout.strip():
                self._run(["config", key, val])

    def pull_rebase(self, credentials: tuple[str, str] | None = None) -> None:
        """Fetch and rebase onto origin/<branch>.

        Rebasing onto the explicit remote-tracking ref (rather than ``git pull``)
        avoids branch.<name>.merge config mismatches when the remote was empty at
        clone time. Raises RebaseConflict / AuthenticationRequired.
        """
        if not self.head_hash():
            return  # unborn local branch: nothing to integrate yet

        fetch = self._run_auth(["fetch", "origin"], credentials)
        if fetch.returncode != 0:
            error = self._err(fetch)
            if git_auth.is_auth_error(error):
                raise AuthenticationRequired(error, self.remote_url)
            return  # empty remote / no refs -> nothing to integrate
        if credentials:
            git_auth.store_credentials(self.remote_url, credentials, self.local_path)

        branch = self._current_branch()
        ref = self._run(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"])
        if ref.returncode != 0 or not ref.stdout.strip():
            return  # remote doesn't have our branch yet (we'll push it)

        rb = self._run(["rebase", "--autostash", f"origin/{branch}"])
        if rb.returncode != 0:
            if self.is_mid_rebase():
                paths = self._conflicted_paths()
                self.abort_rebase()
                raise RebaseConflict(paths)
            raise RuntimeError(self._err(rb) or "Rebase failed")

    def push(self, credentials: tuple[str, str] | None = None) -> None:
        """git push; sets upstream on first push. Raises AuthenticationRequired."""
        result = self._run_auth(["push"], credentials)
        if result.returncode != 0:
            error = self._err(result)
            if "has no upstream branch" in error or "no upstream branch" in error:
                branch = self._current_branch()
                result = self._run_auth(
                    ["push", "--set-upstream", "origin", branch], credentials
                )
                if result.returncode == 0:
                    if credentials:
                        git_auth.store_credentials(self.remote_url, credentials, self.local_path)
                    return
                error = self._err(result)
            if git_auth.is_auth_error(error):
                raise AuthenticationRequired(error, self.remote_url)
            raise RuntimeError(error or "Push failed")
        if credentials:
            git_auth.store_credentials(self.remote_url, credentials, self.local_path)

    def commit_all(self, message: str) -> str | None:
        """Stage everything and commit. Returns the new hash, or None if no changes."""
        self._ensure_identity()
        self._run(["add", "-A"])
        status = self._run(["status", "--porcelain"])
        if not status.stdout.strip():
            return None  # nothing to commit
        result = self._run(["commit", "-m", message])
        if result.returncode != 0:
            raise RuntimeError(self._err(result) or "Commit failed")
        return self.head_hash()

    def abort_rebase(self) -> None:
        if self.is_mid_rebase():
            self._run(["rebase", "--abort"])

    def hard_reset_to(self, commit: str) -> None:
        if commit:
            self._run(["reset", "--hard", commit])
