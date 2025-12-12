"""Git service using pygit2 for repository operations."""

from dataclasses import dataclass
from datetime import datetime
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
        try:
            # Get the commit to branch from
            if from_ref:
                # Try as branch name first
                if from_ref in self.repo.branches:
                    commit = self.repo.branches[from_ref].peel(pygit2.Commit)
                else:
                    # Try as commit hash
                    commit = self.repo.get(from_ref)
                    if commit is None:
                        raise RuntimeError(f"Reference '{from_ref}' not found")
            else:
                commit = self.repo.head.peel(pygit2.Commit)

            # Create the branch
            self.repo.branches.local.create(name, commit)
            return name

        except pygit2.GitError as e:
            raise RuntimeError(f"Failed to create branch: {e}")

    def switch_branch(self, name: str) -> None:
        """Switch to a branch.

        Args:
            name: Name of the branch to switch to
        """
        try:
            # Check for uncommitted changes
            if self.repo.status():
                # Check if there are actual modifications (not just untracked)
                for path, flags in self.repo.status().items():
                    if flags & (pygit2.GIT_STATUS_INDEX_MODIFIED |
                               pygit2.GIT_STATUS_INDEX_NEW |
                               pygit2.GIT_STATUS_INDEX_DELETED |
                               pygit2.GIT_STATUS_WT_MODIFIED |
                               pygit2.GIT_STATUS_WT_DELETED):
                        raise RuntimeError("You have uncommitted changes. Commit or stash them first.")

            # Get the branch
            branch = self.repo.branches.get(name)
            if branch is None:
                raise RuntimeError(f"Branch '{name}' not found")

            # Get the commit
            commit = branch.peel(pygit2.Commit)

            # Checkout the tree
            self.repo.checkout_tree(commit)

            # Update HEAD to point to the branch
            self.repo.set_head(branch.name)

        except pygit2.GitError as e:
            raise RuntimeError(f"Failed to switch branch: {e}")

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
