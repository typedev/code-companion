"""Stage 2: linked-worktree awareness helpers + the identity collision they guard."""
from src.utils.git_worktree import (
    is_linked_worktree, resolve_worktree_dirs, worktree_parent_root,
)
from src.utils.project_identity import resolve_project_identity
from tests.helpers import git, init_repo


def _repo_with_worktree(tmp_path):
    main = init_repo(tmp_path / "main", commit=True)
    git(main, "branch", "-M", "main")
    git(main, "config", "user.name", "Test")
    git(main, "config", "user.email", "test@example.com")
    wt = tmp_path / "main--feature"
    git(main, "worktree", "add", "-b", "feature/x", str(wt))
    return main, wt


def test_is_linked_worktree(tmp_path):
    main, wt = _repo_with_worktree(tmp_path)
    assert is_linked_worktree(main) is False   # main repo: .git is a dir
    assert is_linked_worktree(wt) is True       # worktree: .git is a file
    assert is_linked_worktree(tmp_path / "nope") is False


def test_resolve_worktree_dirs(tmp_path):
    main, wt = _repo_with_worktree(tmp_path)
    assert resolve_worktree_dirs(main) is None  # not a linked worktree

    dirs = resolve_worktree_dirs(wt)
    assert dirs is not None
    git_dir, common_dir = dirs
    # per-worktree gitdir holds this worktree's HEAD
    assert (git_dir / "HEAD").exists()
    assert git_dir.parent.name == "worktrees"
    # common dir is the shared <main>/.git (holds refs/heads)
    assert common_dir.resolve() == (main / ".git").resolve()
    assert (common_dir / "refs" / "heads").exists()


def test_worktree_parent_root(tmp_path):
    main, wt = _repo_with_worktree(tmp_path)
    assert worktree_parent_root(main) is None
    assert worktree_parent_root(wt).resolve() == main.resolve()


def test_worktree_shares_parent_identity(tmp_path):
    # No remote → identity is the root-commit hash, shared by main + worktree.
    # This collision is exactly why worktrees are excluded from sync (Area 3).
    main, wt = _repo_with_worktree(tmp_path)
    id_main = resolve_project_identity(main)
    id_wt = resolve_project_identity(wt)
    assert id_main is not None and id_wt is not None
    assert id_main.project_id == id_wt.project_id
