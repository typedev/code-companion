"""Git service using pygit2 for repository operations."""

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import pygit2

from ..utils import git_auth
from ..utils.text_files import is_binary_bytes, human_size


class AuthenticationRequired(Exception):
    """Raised when git operation requires authentication."""

    def __init__(self, message: str, remote_url: str):
        super().__init__(message)
        self.remote_url = remote_url


class PushRejected(Exception):
    """Raised when a push is rejected as non-fast-forward (remote has new work)."""

    def __init__(self, message: str, remote_url: str):
        super().__init__(message)
        self.remote_url = remote_url


class FileStatus(Enum):
    """Git file status codes."""
    MODIFIED = "M"
    ADDED = "A"
    DELETED = "D"
    RENAMED = "R"
    UNTRACKED = "U"
    TYPECHANGE = "T"


@dataclass
class GitFileStatus:
    """Status of a file in the repository."""
    path: str
    status: FileStatus
    staged: bool
    old_path: str | None = None  # For renames


@dataclass
class GitCommit:
    """A git commit."""
    hash: str
    short_hash: str
    message: str
    author: str
    author_email: str
    timestamp: datetime
    is_head: bool


class GitService:
    """Service for git operations using pygit2."""

    def __init__(self, repo_path: Path | str):
        self.repo_path = Path(repo_path)
        self._repo: pygit2.Repository | None = None

    def is_git_repo(self) -> bool:
        """Check if path is inside a git repository."""
        try:
            result = pygit2.discover_repository(str(self.repo_path))
            return result is not None
        except pygit2.GitError:
            return False

    def open(self) -> bool:
        """Open the repository. Returns True if successful."""
        try:
            repo_path = pygit2.discover_repository(str(self.repo_path))
            if repo_path:
                self._repo = pygit2.Repository(repo_path)
                return True
        except pygit2.GitError:
            pass
        return False

    @property
    def repo(self) -> pygit2.Repository:
        """Get repository, opening if needed."""
        if self._repo is None:
            if not self.open():
                raise RuntimeError("Not a git repository")
        return self._repo

    def get_branch_name(self) -> str:
        """Get current branch name or HEAD commit if detached."""
        try:
            if self.repo.head_is_detached:
                return str(self.repo.head.target)[:7]
            return self.repo.head.shorthand
        except pygit2.GitError:
            return "unknown"

    # Status code letters from `git status --porcelain`.
    _PORCELAIN_STATUS = {
        "M": FileStatus.MODIFIED,
        "A": FileStatus.ADDED,
        "D": FileStatus.DELETED,
        "R": FileStatus.RENAMED,
        "C": FileStatus.RENAMED,  # copy — treat like a rename for display
        "T": FileStatus.TYPECHANGE,
        "?": FileStatus.UNTRACKED,
    }

    def get_porcelain_status(self, env=None) -> tuple[list[GitFileStatus], list[GitFileStatus]]:
        """The single source of truth for working-tree status: (staged, unstaged).

        Uses ``git status --porcelain -z`` (reads the on-disk index, so it stays
        correct after external index changes) — the one status parser in the app,
        replacing the former pygit2 ``get_status`` path so the changes panel, the
        notes-panel badges and the pull pre-check can never disagree.

        Raises ``RuntimeError`` if git fails, so callers surface it (roadmap 3.2)
        rather than reading a failure as "no changes".
        """
        result = subprocess.run(
            ["git", "status", "--porcelain", "-z"],
            capture_output=True, cwd=str(self.repo_path), timeout=30,
            env=env or git_auth.build_git_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.decode("utf-8", errors="replace").strip() or "git status failed"
            )
        return self._parse_porcelain(result.stdout)

    @classmethod
    def _parse_porcelain(cls, data: bytes) -> tuple[list[GitFileStatus], list[GitFileStatus]]:
        """Parse ``git status --porcelain -z`` bytes into (staged, unstaged)."""
        staged: list[GitFileStatus] = []
        unstaged: list[GitFileStatus] = []
        entries = data.split(b"\x00")
        i = 0
        while i < len(entries):
            entry = entries[i]
            if len(entry) < 4:
                i += 1
                continue

            index_code = chr(entry[0])
            wt_code = chr(entry[1])
            path = entry[3:].decode("utf-8", errors="replace")

            # A rename/copy is followed by a NUL-separated original path.
            old_path = None
            if index_code in ("R", "C") or wt_code in ("R", "C"):
                if i + 1 < len(entries):
                    old_path = entries[i + 1].decode("utf-8", errors="replace")
                i += 1  # consume the original-path token

            if index_code in cls._PORCELAIN_STATUS and index_code != "?":
                staged.append(GitFileStatus(
                    path, cls._PORCELAIN_STATUS[index_code], staged=True, old_path=old_path))
            if wt_code in cls._PORCELAIN_STATUS:
                unstaged.append(GitFileStatus(
                    path, cls._PORCELAIN_STATUS[wt_code], staged=False, old_path=old_path))

            i += 1

        staged.sort(key=lambda x: x.path)
        unstaged.sort(key=lambda x: x.path)
        return staged, unstaged

    def stage(self, path: str) -> None:
        """Stage a file."""
        full_path = self.repo_path / path
        if full_path.exists():
            self.repo.index.add(path)
        else:
            # File was deleted
            self.repo.index.remove(path)
        self.repo.index.write()

    def unstage(self, path: str) -> None:
        """Unstage a file (reset the index entry to HEAD)."""
        try:
            head = self.repo.head.peel(pygit2.Commit)
        except pygit2.GitError:
            head = None  # no HEAD yet (initial commit)
        try:
            if head is not None and path in head.tree:
                entry = head.tree[path]
                self.repo.index.add(pygit2.IndexEntry(path, entry.id, entry.filemode))
            else:
                # New file (or no HEAD): drop it from the index.
                self.repo.index.remove(path)
            self.repo.index.write()
        except (pygit2.GitError, KeyError) as e:
            raise RuntimeError(f"Cannot unstage '{path}': {e}")

    def stage_all(self) -> None:
        """Stage all changes."""
        self.repo.index.add_all()
        self.repo.index.write()

    def unstage_all(self) -> None:
        """Unstage all staged changes (reset the index to HEAD)."""
        try:
            self.repo.reset(self.repo.head.target, pygit2.GIT_RESET_MIXED)
        except pygit2.GitError as e:
            raise RuntimeError(f"Cannot unstage all: {e}")

    def restore_file(self, path: str) -> None:
        """Discard a file's changes by restoring it from HEAD (index + worktree).

        Uses ``git checkout`` rather than a raw blob write so ``.gitattributes``
        filters (CRLF/smudge) are applied, symlinks are recreated as symlinks
        (not their target text), the exec bit is set correctly, and a staged
        change to the path is reverted too.
        """
        result = subprocess.run(
            ["git", "checkout", "HEAD", "--", path],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
            timeout=30,
            env=git_auth.build_git_env(),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "git checkout failed"
            raise RuntimeError(f"Cannot restore '{path}': {detail}")
        # The CLI updated the on-disk index; resync pygit2's in-memory copy.
        try:
            self.repo.index.read()
        except pygit2.GitError:
            pass

    def _run_git(self, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a git subprocess in the repo with the deterministic env (roadmap 3.4).

        ``LC_ALL=C`` + ``GIT_TERMINAL_PROMPT=0`` make output stable and prompt-free.
        Never raises on a non-zero exit — callers inspect ``returncode``/``stderr``.
        """
        return subprocess.run(
            ["git", *args],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=git_auth.build_git_env(),
        )

    def commit(self, message: str) -> str:
        """Create a commit from the staged index. Returns the short hash.

        Uses the git CLI (roadmap 3.4/3.8) so hooks, config, ``.gitattributes`` and
        merge semantics match real git — committing during a merge correctly records
        both parents, and there is no stale in-memory index. Refuses an empty commit;
        unresolved conflicts are refused by git itself.
        """
        result = self._run_git(["commit", "-m", message])
        if result.returncode != 0:
            out = f"{result.stderr}\n{result.stdout}".lower()
            if any(m in out for m in (
                "nothing to commit", "nothing added to commit", "no changes added",
            )):
                raise RuntimeError("Nothing staged to commit.")
            if "tell me who you are" in out or "user.name" in out or "user.email" in out:
                raise RuntimeError("Git user.name and user.email must be configured")
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Commit failed")
        return self._run_git(["rev-parse", "--short", "HEAD"]).stdout.strip()

    def amend_commit(self, message: str) -> str:
        """Amend HEAD with the current index and a new message. Returns the short hash.

        ``git commit --amend`` preserves the original author and rewrites HEAD; a
        message-only amend (nothing newly staged) is allowed.
        """
        result = self._run_git(["commit", "--amend", "-m", message])
        if result.returncode != 0:
            out = f"{result.stderr}\n{result.stdout}".lower()
            if "tell me who you are" in out or "user.name" in out or "user.email" in out:
                raise RuntimeError("Git user.name and user.email must be configured")
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Amend failed")
        return self._run_git(["rev-parse", "--short", "HEAD"]).stdout.strip()

    def get_head_message(self) -> str:
        """Return the full message of HEAD (for prefilling an amend), or ''."""
        result = self._run_git(["log", "-1", "--pretty=%B"])
        return result.stdout.strip() if result.returncode == 0 else ""

    def get_diff(self, path: str, staged: bool = False) -> tuple[str, str]:
        """
        Get diff for a file.
        Returns (old_content, new_content) tuple for DiffView.

        Content is read as raw bytes and decoded only if textual: a binary file
        is short-circuited to a human "Binary file (size)" note on each side so
        the diff view never renders decoded-garbage line noise.
        """
        old_bytes = b""
        new_bytes = b""
        full_path = self.repo_path / path

        try:
            if staged:
                # Diff between HEAD and index
                head = self.repo.head.peel(pygit2.Commit)
                if path in head.tree:
                    old_bytes = self.repo.get(head.tree[path].id).data
                # Get content from index
                if path in self.repo.index:
                    new_bytes = self.repo.get(self.repo.index[path].id).data
            else:
                # Base content (from index if present, else from HEAD)
                if path in self.repo.index:
                    old_bytes = self.repo.get(self.repo.index[path].id).data
                else:
                    try:
                        head = self.repo.head.peel(pygit2.Commit)
                        if path in head.tree:
                            old_bytes = self.repo.get(head.tree[path].id).data
                    except pygit2.GitError:
                        pass
                # Working tree content
                if full_path.exists():
                    new_bytes = full_path.read_bytes()

        except (pygit2.GitError, OSError):
            pass

        if is_binary_bytes(old_bytes) or is_binary_bytes(new_bytes):
            def note(data: bytes) -> str:
                return f"Binary file ({human_size(len(data))})" if data else ""
            return note(old_bytes), note(new_bytes)

        # Decode both sides the same way (bytes → text, CRLF preserved) so a
        # CRLF file doesn't show spurious ^M diffs from mixed read paths.
        return (
            old_bytes.decode("utf-8", errors="replace"),
            new_bytes.decode("utf-8", errors="replace"),
        )

    def get_remote(self) -> pygit2.Remote | None:
        """Get the origin remote."""
        try:
            return self.repo.remotes["origin"]
        except KeyError:
            # Try first remote
            if self.repo.remotes:
                return self.repo.remotes[0]
        return None

    def get_ahead_behind(self) -> tuple[int, int]:
        """Get count of commits ahead and behind remote.

        Returns:
            Tuple of (ahead, behind) counts. Returns (0, 0) if no upstream.
        """
        if not self.repo:
            return (0, 0)

        try:
            result = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=10,
                env=git_auth.build_git_env(),
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) == 2:
                    return (int(parts[0]), int(parts[1]))
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

        return (0, 0)

    def has_uncommitted_changes(self) -> bool:
        """Check if there are uncommitted changes (staged or unstaged)."""
        try:
            staged, unstaged = self.get_porcelain_status()
        except (RuntimeError, OSError):
            return False
        return bool(staged or unstaged)

    def pull(self, credentials: tuple[str, str] | None = None,
             remember: bool = True) -> str:
        """Pull from remote using git CLI. Returns status message.

        Args:
            credentials: Optional (username, password) tuple for authentication.
            remember: Persist ``credentials`` on success (opt-in from the UI).
        """
        remote = self.get_remote()
        if not remote:
            raise RuntimeError("No remote configured")

        try:
            env = self._get_auth_env(remote.url, credentials)
            result = subprocess.run(
                ["git", "pull"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or result.stdout.strip()
                if self._is_auth_error(error):
                    raise AuthenticationRequired(error, remote.url)
                raise RuntimeError(error or "Pull failed")

            # Store credentials only if the user opted in (roadmap 3.7).
            if credentials and remember:
                self._store_credentials(remote.url, credentials)

            output = result.stdout.strip()
            if "Already up to date" in output:
                return "Already up to date"
            return "Pull successful"

        except subprocess.TimeoutExpired:
            raise RuntimeError("Pull timed out")
        except FileNotFoundError:
            raise RuntimeError("git command not found")
        finally:
            self._cleanup_askpass()

    @staticmethod
    def _is_rejected_push(error: str) -> bool:
        """True if the push failed as a non-fast-forward rejection."""
        markers = ("non-fast-forward", "fetch first", "! [rejected]",
                   "Updates were rejected", "tip of your current branch is behind")
        return any(m in error for m in markers)

    def push(self, credentials: tuple[str, str] | None = None,
             force_with_lease: bool = False, remember: bool = True) -> str:
        """Push to remote using git CLI. Returns status message.

        Automatically sets upstream for new branches. A non-fast-forward
        rejection raises PushRejected so the UI can offer pull-then-push or a
        force-with-lease. ``force_with_lease=True`` adds ``--force-with-lease``
        (never a bare ``--force``), so a push only overwrites the remote tip the
        local ref was based on.

        Args:
            credentials: Optional (username, password) tuple for authentication.
            force_with_lease: Retry semantics with ``--force-with-lease``.
        """
        remote = self.get_remote()
        if not remote:
            raise RuntimeError("No remote configured")

        branch_name = self.get_branch_name()
        env = self._get_auth_env(remote.url, credentials)
        base_cmd = ["git", "push"]
        if force_with_lease:
            base_cmd.append("--force-with-lease")

        try:
            # First try normal push
            result = subprocess.run(
                base_cmd,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )

            if result.returncode != 0:
                error = result.stderr.strip() or result.stdout.strip()

                # Check if it's "no upstream branch" error
                if "has no upstream branch" in error or "no upstream branch" in error:
                    # Retry with --set-upstream
                    upstream_cmd = ["git", "push"]
                    if force_with_lease:
                        upstream_cmd.append("--force-with-lease")
                    upstream_cmd += ["--set-upstream", remote.name, branch_name]
                    result = subprocess.run(
                        upstream_cmd,
                        cwd=str(self.repo_path),
                        capture_output=True,
                        text=True,
                        timeout=60,
                        env=env,
                    )
                    if result.returncode == 0:
                        if credentials and remember:
                            self._store_credentials(remote.url, credentials)
                        return f"Push successful (upstream set to {remote.name}/{branch_name})"
                    error = result.stderr.strip() or result.stdout.strip()

                if self._is_auth_error(error):
                    raise AuthenticationRequired(error, remote.url)
                # A non-fast-forward rejection is recoverable — let the UI decide.
                if self._is_rejected_push(error):
                    raise PushRejected(error, remote.url)
                raise RuntimeError(error or "Push failed")

            # Store credentials only if the user opted in (roadmap 3.7).
            if credentials and remember:
                self._store_credentials(remote.url, credentials)

            return "Push successful"

        except subprocess.TimeoutExpired:
            raise RuntimeError("Push timed out")
        except FileNotFoundError:
            raise RuntimeError("git command not found")
        finally:
            self._cleanup_askpass()

    def get_file_status_map(self) -> dict[str, FileStatus]:
        """Get a map of path -> status for FileTree indicators (single source)."""
        result = {}
        try:
            staged, unstaged = self.get_porcelain_status()
        except (RuntimeError, OSError):
            return result
        for file_status in staged + unstaged:
            # Prefer unstaged status for display (more urgent)
            if file_status.path not in result or not file_status.staged:
                result[file_status.path] = file_status.status
        return result

    # ==================== History Methods ====================

    def get_commits(self, limit: int = 50) -> list[GitCommit]:
        """Get recent commits from current branch."""
        commits = []

        try:
            head_oid = self.repo.head.target
        except pygit2.GitError:
            return commits

        for commit in self.repo.walk(head_oid, pygit2.GIT_SORT_TIME):
            if len(commits) >= limit:
                break

            git_commit = GitCommit(
                hash=str(commit.id),
                short_hash=str(commit.id)[:7],
                message=commit.message.strip(),
                author=commit.author.name,
                author_email=commit.author.email,
                timestamp=datetime.fromtimestamp(commit.commit_time),
                is_head=(commit.id == head_oid),
            )
            commits.append(git_commit)

        return commits

    def get_commit(self, commit_hash: str) -> GitCommit | None:
        """Get a single commit by hash."""
        try:
            commit = self.repo.get(commit_hash)
            if not commit:
                return None

            head_oid = self.repo.head.target

            return GitCommit(
                hash=str(commit.id),
                short_hash=str(commit.id)[:7],
                message=commit.message.strip(),
                author=commit.author.name,
                author_email=commit.author.email,
                timestamp=datetime.fromtimestamp(commit.commit_time),
                is_head=(commit.id == head_oid),
            )
        except pygit2.GitError:
            return None

    def get_commit_diff(self, commit_hash: str) -> tuple[str, str]:
        """
        Get diff for a commit (compared to its parent).
        Returns (old_content, new_content) as combined diff text.
        """
        try:
            commit = self.repo.get(commit_hash)
            if not commit:
                return "", ""

            # Get parent commit (or empty tree for initial commit)
            if commit.parents:
                parent = commit.parents[0]
                diff = self.repo.diff(parent, commit)
            else:
                # Initial commit - diff against empty tree
                diff = commit.tree.diff_to_tree(swap=True)

            # Collect all changes as text
            old_lines = []
            new_lines = []

            for patch in diff:
                file_path = patch.delta.new_file.path
                old_lines.append(f"--- a/{file_path}")
                new_lines.append(f"+++ b/{file_path}")

                for hunk in patch.hunks:
                    for line in hunk.lines:
                        if line.origin == '+':
                            new_lines.append(f"+{line.content.rstrip()}")
                        elif line.origin == '-':
                            old_lines.append(f"-{line.content.rstrip()}")
                        else:
                            old_lines.append(f" {line.content.rstrip()}")
                            new_lines.append(f" {line.content.rstrip()}")

            return "\n".join(old_lines), "\n".join(new_lines)

        except (pygit2.GitError, KeyError):
            return "", ""

    def get_commit_full_diff(self, commit_hash: str) -> str:
        """Get full unified diff text for a commit."""
        try:
            commit = self.repo.get(commit_hash)
            if not commit:
                return ""

            if commit.parents:
                parent = commit.parents[0]
                diff = self.repo.diff(parent, commit)
            else:
                diff = commit.tree.diff_to_tree(swap=True)

            return diff.patch or ""

        except (pygit2.GitError, KeyError):
            return ""

    def checkout_commit(self, commit_hash: str) -> None:
        """Checkout a specific commit (detached HEAD)."""
        try:
            commit = self.repo.get(commit_hash)
            if not commit:
                raise RuntimeError(f"Commit {commit_hash} not found")

            # Checkout the tree
            self.repo.checkout_tree(commit)

            # Set HEAD to the commit (detached)
            self.repo.set_head(commit.id)

        except pygit2.GitError as e:
            raise RuntimeError(f"Checkout failed: {e}")

    def reset_to_commit(self, commit_hash: str, hard: bool = False) -> None:
        """Reset current branch to commit."""
        try:
            commit = self.repo.get(commit_hash)
            if not commit:
                raise RuntimeError(f"Commit {commit_hash} not found")

            reset_type = pygit2.GIT_RESET_HARD if hard else pygit2.GIT_RESET_SOFT
            self.repo.reset(commit.id, reset_type)

        except pygit2.GitError as e:
            raise RuntimeError(f"Reset failed: {e}")

    def revert_commit(self, commit_hash: str) -> str:
        """Create a revert commit. Returns new commit hash."""
        try:
            commit = self.repo.get(commit_hash)
            if not commit:
                raise RuntimeError(f"Commit {commit_hash} not found")

            # Revert the commit
            self.repo.revert_commit(commit, self.repo.head.peel(pygit2.Commit))

            # Check if there are conflicts
            if self.repo.index.conflicts:
                self.repo.state_cleanup()
                raise RuntimeError("Revert resulted in conflicts")

            # Create the revert commit
            tree_id = self.repo.index.write_tree()

            # Get signature
            config = self.repo.config
            name = config["user.name"]
            email = config["user.email"]
            signature = pygit2.Signature(name, email)

            message = f"Revert \"{commit.message.split(chr(10))[0]}\"\n\nThis reverts commit {commit_hash[:7]}."

            new_commit_id = self.repo.create_commit(
                "HEAD",
                signature,
                signature,
                message,
                tree_id,
                [self.repo.head.target]
            )

            self.repo.state_cleanup()
            return str(new_commit_id)[:7]

        except pygit2.GitError as e:
            self.repo.state_cleanup()
            raise RuntimeError(f"Revert failed: {e}")

    # --- Branch Management ---

    def list_branches(self) -> dict[str, list[str]]:
        """List all branches.

        Returns dict with 'local' and 'remote' keys containing branch names.
        """
        result = {"local": [], "remote": []}

        try:
            for branch_name in self.repo.branches.local:
                result["local"].append(branch_name)

            for branch_name in self.repo.branches.remote:
                result["remote"].append(branch_name)

        except pygit2.GitError:
            pass

        return result

    def create_branch(self, name: str, from_ref: str | None = None) -> str:
        """Create a new branch.

        Args:
            name: Name of the new branch
            from_ref: Reference to create from (branch name or commit hash). Defaults to HEAD.

        Returns:
            The name of the created branch
        """
        # ``git branch <name> [<start-point>]`` — create-only (no checkout);
        # start-point accepts a branch name or a commit hash, defaults to HEAD.
        args = ["branch", name]
        if from_ref:
            args.append(from_ref)
        result = self._run_git(args)
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip()
                or f"Failed to create branch '{name}'"
            )
        return name

    def switch_branch(self, name: str) -> None:
        """Switch to a branch (git CLI).

        ``git switch`` performs its own safety check (refuses when the switch would
        overwrite local changes, carries them when safe) and returns a clear message
        on failure — replacing the raw pygit2 errors and the hand-rolled status guard.
        """
        result = self._run_git(["switch", name])
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip()
                or f"Failed to switch to '{name}'"
            )

    def checkout_remote_tracking(self, remote_branch: str) -> str:
        """Check out a remote branch (e.g. ``origin/feature``) as a local tracking branch.

        Returns the local branch name. If the local branch already exists, switches to it.
        """
        local = remote_branch.split("/", 1)[1] if "/" in remote_branch else remote_branch
        result = self._run_git(["switch", "--track", remote_branch])
        if result.returncode != 0:
            out = f"{result.stderr}\n{result.stdout}".lower()
            if "already exists" in out:
                # Local branch is already there — just switch to it.
                self.switch_branch(local)
                return local
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip()
                or f"Failed to check out '{remote_branch}'"
            )
        return local

    def has_upstream(self) -> bool:
        """True if the current branch has a configured upstream ('published')."""
        result = self._run_git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]
        )
        return result.returncode == 0

    def delete_branch(self, name: str, force: bool = False) -> None:
        """Delete a branch.

        Args:
            name: Name of the branch to delete
            force: If True, delete even if not fully merged
        """
        try:
            branch = self.repo.branches.get(name)
            if branch is None:
                raise RuntimeError(f"Branch '{name}' not found")

            # Don't allow deleting current branch
            if not self.repo.head_is_detached:
                current = self.repo.head.shorthand
                if name == current:
                    raise RuntimeError("Cannot delete the currently checked out branch")

            # Check if branch is merged (unless force)
            if not force:
                # Get branch commit
                branch_commit = branch.peel(pygit2.Commit)
                head_commit = self.repo.head.peel(pygit2.Commit)

                # Check if branch commit is ancestor of HEAD
                if not self.repo.descendant_of(head_commit.id, branch_commit.id):
                    raise RuntimeError(f"Branch '{name}' is not fully merged. Use force=True to delete anyway.")

            branch.delete()

        except pygit2.GitError as e:
            raise RuntimeError(f"Failed to delete branch: {e}")

    def get_branch_info(self, name: str) -> dict:
        """Get information about a branch.

        Returns dict with: name, is_current, is_remote, ahead, behind, last_commit
        """
        try:
            branch = self.repo.branches.get(name)
            if branch is None:
                return {}

            is_remote = name in self.repo.branches.remote
            is_current = False
            if not self.repo.head_is_detached:
                is_current = (self.repo.head.shorthand == name)

            commit = branch.peel(pygit2.Commit)

            info = {
                "name": name,
                "is_current": is_current,
                "is_remote": is_remote,
                "last_commit": commit.short_id,
                "last_message": commit.message.split("\n")[0][:50],
                "ahead": 0,
                "behind": 0,
            }

            # Calculate ahead/behind for local branches with upstream
            if not is_remote and branch.upstream:
                upstream_commit = branch.upstream.peel(pygit2.Commit)
                ahead, behind = self.repo.ahead_behind(commit.id, upstream_commit.id)
                info["ahead"] = ahead
                info["behind"] = behind

            return info

        except pygit2.GitError:
            return {}

    # --- Commit Details ---

    def get_commit_files(self, commit_hash: str) -> list[dict]:
        """Get list of files changed in a commit with stats.

        Returns list of dicts with: path, status, additions, deletions
        """
        result = []

        try:
            commit = self.repo.get(commit_hash)
            if commit is None:
                return result

            # Get parent commit (or empty tree for initial commit)
            if commit.parents:
                parent = commit.parents[0]
                parent_tree = parent.tree
            else:
                parent_tree = None

            # Get diff
            if parent_tree:
                diff = self.repo.diff(parent_tree, commit.tree)
            else:
                diff = commit.tree.diff_to_tree()

            # Collect file stats
            for patch in diff:
                delta = patch.delta
                status_map = {
                    pygit2.GIT_DELTA_ADDED: "A",
                    pygit2.GIT_DELTA_DELETED: "D",
                    pygit2.GIT_DELTA_MODIFIED: "M",
                    pygit2.GIT_DELTA_RENAMED: "R",
                    pygit2.GIT_DELTA_COPIED: "C",
                    pygit2.GIT_DELTA_TYPECHANGE: "T",
                }

                result.append({
                    "path": delta.new_file.path or delta.old_file.path,
                    "old_path": delta.old_file.path if delta.status == pygit2.GIT_DELTA_RENAMED else None,
                    "status": status_map.get(delta.status, "M"),
                    "additions": patch.line_stats[1],
                    "deletions": patch.line_stats[2],
                })

        except pygit2.GitError:
            pass

        return result

    def get_commit_file_diff(self, commit_hash: str, file_path: str) -> str:
        """Get diff for a specific file in a commit.

        Returns unified diff string.
        """
        try:
            commit = self.repo.get(commit_hash)
            if commit is None:
                return ""

            # Get parent commit
            if commit.parents:
                parent = commit.parents[0]
                parent_tree = parent.tree
            else:
                parent_tree = None

            # Get diff
            if parent_tree:
                diff = self.repo.diff(parent_tree, commit.tree)
            else:
                diff = commit.tree.diff_to_tree()

            # Find the specific file
            for patch in diff:
                delta = patch.delta
                if delta.new_file.path == file_path or delta.old_file.path == file_path:
                    return patch.text or ""

            return ""

        except pygit2.GitError:
            return ""

    # --- Authentication helpers ---
    #
    # The actual implementation lives in src/utils/git_auth.py so the sync repo
    # wrapper can reuse it. These thin wrappers preserve the original signatures.

    def _is_auth_error(self, error: str) -> bool:
        """Check if error message indicates authentication failure."""
        return git_auth.is_auth_error(error)

    def _get_stored_credentials(self, remote_url: str) -> tuple[str, str] | None:
        """Try to get stored credentials (keyring, then git credential helper)."""
        from .credential_service import CredentialService
        return CredentialService.get_instance().lookup(remote_url, self.repo_path)

    def _get_auth_env(self, remote_url: str, credentials: tuple[str, str] | None) -> dict:
        """Get environment dict with GIT_ASKPASS for credentials."""
        # Auto-fill from the keyring (then the store helper) for HTTPS remotes,
        # so a saved credential is used without re-prompting.
        if not credentials and remote_url.lower().startswith(("http://", "https://")):
            credentials = self._get_stored_credentials(remote_url)
        env, askpass_path = git_auth.build_auth_env(remote_url, credentials, self.repo_path)
        # Track the temp GIT_ASKPASS script so the caller can remove it after the
        # git operation (see _cleanup_askpass); otherwise it leaks in /tmp.
        self._askpass_script = askpass_path
        return env

    def _cleanup_askpass(self):
        """Delete the temporary GIT_ASKPASS helper script, if one was created."""
        path = getattr(self, "_askpass_script", None)
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
            self._askpass_script = None

    def _store_credentials(self, remote_url: str, credentials: tuple[str, str]):
        """Store credentials (keyring if available, else git credential helper)."""
        from .credential_service import CredentialService
        username, password = credentials
        CredentialService.get_instance().store(remote_url, username, password, self.repo_path)
