"""Git service using pygit2 for repository operations."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pygit2


class FileStatus(Enum):
    """Git file status codes."""
    MODIFIED = "M"
    ADDED = "A"
    DELETED = "D"
    RENAMED = "R"
    UNTRACKED = "?"
    TYPECHANGE = "T"


@dataclass
class GitFileStatus:
    """Status of a file in the repository."""
    path: str
    status: FileStatus
    staged: bool
    old_path: str | None = None  # For renames


class GitService:
    """Service for git operations using pygit2."""

    def __init__(self, repo_path: Path | str):
        self.repo_path = Path(repo_path)
        self._repo: pygit2.Repository | None = None

    def is_git_repo(self) -> bool:
        """Check if path is inside a git repository."""
        try:
            pygit2.discover_repository(str(self.repo_path))
            return True
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
                return self.repo.head.target.hex[:7]
            return self.repo.head.shorthand
        except pygit2.GitError:
            return "unknown"

    def get_status(self) -> list[GitFileStatus]:
        """Get status of all changed files."""
        result = []

        try:
            status = self.repo.status()
        except pygit2.GitError:
            return result

        for path, flags in status.items():
            # Check staged status (index)
            if flags & pygit2.GIT_STATUS_INDEX_NEW:
                result.append(GitFileStatus(path, FileStatus.ADDED, staged=True))
            elif flags & pygit2.GIT_STATUS_INDEX_MODIFIED:
                result.append(GitFileStatus(path, FileStatus.MODIFIED, staged=True))
            elif flags & pygit2.GIT_STATUS_INDEX_DELETED:
                result.append(GitFileStatus(path, FileStatus.DELETED, staged=True))
            elif flags & pygit2.GIT_STATUS_INDEX_RENAMED:
                result.append(GitFileStatus(path, FileStatus.RENAMED, staged=True))
            elif flags & pygit2.GIT_STATUS_INDEX_TYPECHANGE:
                result.append(GitFileStatus(path, FileStatus.TYPECHANGE, staged=True))

            # Check working tree status
            if flags & pygit2.GIT_STATUS_WT_NEW:
                result.append(GitFileStatus(path, FileStatus.UNTRACKED, staged=False))
            elif flags & pygit2.GIT_STATUS_WT_MODIFIED:
                result.append(GitFileStatus(path, FileStatus.MODIFIED, staged=False))
            elif flags & pygit2.GIT_STATUS_WT_DELETED:
                result.append(GitFileStatus(path, FileStatus.DELETED, staged=False))
            elif flags & pygit2.GIT_STATUS_WT_RENAMED:
                result.append(GitFileStatus(path, FileStatus.RENAMED, staged=False))
            elif flags & pygit2.GIT_STATUS_WT_TYPECHANGE:
                result.append(GitFileStatus(path, FileStatus.TYPECHANGE, staged=False))

        # Sort: staged first, then by path
        result.sort(key=lambda x: (not x.staged, x.path))
        return result

    def get_staged_files(self) -> list[GitFileStatus]:
        """Get only staged files."""
        return [f for f in self.get_status() if f.staged]

    def get_unstaged_files(self) -> list[GitFileStatus]:
        """Get only unstaged/untracked files."""
        return [f for f in self.get_status() if not f.staged]

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
        """Unstage a file (reset to HEAD)."""
        try:
            # Get the HEAD commit
            head = self.repo.head.peel(pygit2.Commit)
            # Reset index entry to HEAD
            if path in head.tree:
                entry = head.tree[path]
                self.repo.index.add(pygit2.IndexEntry(path, entry.id, entry.filemode))
            else:
                # File is new, remove from index
                self.repo.index.remove(path)
            self.repo.index.write()
        except (pygit2.GitError, KeyError):
            # If HEAD doesn't exist (initial commit) or other error
            try:
                self.repo.index.remove(path)
                self.repo.index.write()
            except pygit2.GitError:
                pass

    def stage_all(self) -> None:
        """Stage all changes."""
        self.repo.index.add_all()
        self.repo.index.write()

    def unstage_all(self) -> None:
        """Unstage all staged changes."""
        try:
            self.repo.reset(self.repo.head.target, pygit2.GIT_RESET_MIXED)
        except pygit2.GitError:
            pass

    def commit(self, message: str) -> str:
        """Create a commit. Returns commit hash."""
        # Build the tree from index
        tree_id = self.repo.index.write_tree()

        # Get signature from git config
        try:
            config = self.repo.config
            name = config["user.name"]
            email = config["user.email"]
            signature = pygit2.Signature(name, email)
        except KeyError:
            raise RuntimeError("Git user.name and user.email must be configured")

        # Get parent commits
        try:
            parents = [self.repo.head.target]
        except pygit2.GitError:
            # Initial commit
            parents = []

        # Create commit
        commit_id = self.repo.create_commit(
            "HEAD",
            signature,  # author
            signature,  # committer
            message,
            tree_id,
            parents
        )

        return str(commit_id)[:7]

    def get_diff(self, path: str, staged: bool = False) -> tuple[str, str]:
        """
        Get diff for a file.
        Returns (old_content, new_content) tuple for DiffView.
        """
        old_content = ""
        new_content = ""
        full_path = self.repo_path / path

        try:
            if staged:
                # Diff between HEAD and index
                head = self.repo.head.peel(pygit2.Commit)
                if path in head.tree:
                    blob = self.repo.get(head.tree[path].id)
                    old_content = blob.data.decode("utf-8", errors="replace")

                # Get content from index
                if path in self.repo.index:
                    entry = self.repo.index[path]
                    blob = self.repo.get(entry.id)
                    new_content = blob.data.decode("utf-8", errors="replace")
            else:
                # Diff between index (or HEAD) and working tree
                # Get base content (from index if staged, else from HEAD)
                if path in self.repo.index:
                    entry = self.repo.index[path]
                    blob = self.repo.get(entry.id)
                    old_content = blob.data.decode("utf-8", errors="replace")
                else:
                    # Try HEAD
                    try:
                        head = self.repo.head.peel(pygit2.Commit)
                        if path in head.tree:
                            blob = self.repo.get(head.tree[path].id)
                            old_content = blob.data.decode("utf-8", errors="replace")
                    except pygit2.GitError:
                        pass

                # Get working tree content
                if full_path.exists():
                    new_content = full_path.read_text(errors="replace")

        except (pygit2.GitError, OSError, UnicodeDecodeError):
            pass

        return old_content, new_content

    def get_remote(self) -> pygit2.Remote | None:
        """Get the origin remote."""
        try:
            return self.repo.remotes["origin"]
        except KeyError:
            # Try first remote
            if self.repo.remotes:
                return self.repo.remotes[0]
        return None

    def _get_credentials_callback(self):
        """Create credentials callback for remote operations."""
        def credentials(url, username_from_url, allowed_types):
            if allowed_types & pygit2.GIT_CREDENTIAL_SSH_KEY:
                # Try SSH key
                ssh_dir = Path.home() / ".ssh"
                for key_name in ["id_ed25519", "id_rsa", "id_ecdsa"]:
                    private_key = ssh_dir / key_name
                    public_key = ssh_dir / f"{key_name}.pub"
                    if private_key.exists() and public_key.exists():
                        return pygit2.Keypair(
                            username_from_url or "git",
                            str(public_key),
                            str(private_key),
                            ""
                        )
            if allowed_types & pygit2.GIT_CREDENTIAL_USERPASS_PLAINTEXT:
                # Rely on system credential helper - this won't work directly
                # User needs to have credentials cached
                pass
            return None
        return credentials

    def pull(self) -> str:
        """Pull from remote. Returns status message."""
        remote = self.get_remote()
        if not remote:
            raise RuntimeError("No remote configured")

        # Fetch
        callbacks = pygit2.RemoteCallbacks(credentials=self._get_credentials_callback())
        remote.fetch(callbacks=callbacks)

        # Get remote branch
        branch_name = self.get_branch_name()
        remote_ref = f"refs/remotes/{remote.name}/{branch_name}"

        try:
            remote_id = self.repo.references[remote_ref].target
        except KeyError:
            return "No remote branch to pull from"

        # Merge
        merge_result, _ = self.repo.merge_analysis(remote_id)

        if merge_result & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
            return "Already up to date"
        elif merge_result & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
            # Fast-forward
            self.repo.checkout_tree(self.repo.get(remote_id))
            self.repo.head.set_target(remote_id)
            return "Fast-forward merge"
        elif merge_result & pygit2.GIT_MERGE_ANALYSIS_NORMAL:
            # Need real merge - for now just report
            return "Merge required (not implemented)"

        return "Pull completed"

    def push(self) -> str:
        """Push to remote. Returns status message."""
        remote = self.get_remote()
        if not remote:
            raise RuntimeError("No remote configured")

        branch_name = self.get_branch_name()
        callbacks = pygit2.RemoteCallbacks(credentials=self._get_credentials_callback())

        try:
            remote.push([f"refs/heads/{branch_name}"], callbacks=callbacks)
            return "Push successful"
        except pygit2.GitError as e:
            raise RuntimeError(f"Push failed: {e}")

    def get_file_status_map(self) -> dict[str, FileStatus]:
        """Get a map of path -> status for FileTree indicators."""
        result = {}
        for file_status in self.get_status():
            # Prefer unstaged status for display (more urgent)
            if file_status.path not in result or not file_status.staged:
                result[file_status.path] = file_status.status
        return result
