"""Stage 3: GitService worktree add/remove/list + slugify."""
from pathlib import Path

import pytest

from src.services.git_service import GitService
from src.utils.git_worktree import slugify, is_linked_worktree
from tests.helpers import git, init_repo


def _repo(tmp_path):
    main = init_repo(tmp_path / "main", commit=True)
    git(main, "branch", "-M", "main")
    git(main, "config", "user.name", "Test")
    git(main, "config", "user.email", "test@example.com")
    svc = GitService(main)
    svc.open()
    return main, svc


def test_slugify():
    assert slugify("Add login flow") == "add-login-flow"
    assert slugify("  Fix: cache!! ") == "fix-cache"
    assert slugify("feature/UI redesign") == "feature-ui-redesign"
    assert slugify("!!!") == "worktree"  # falls back


def test_add_list_remove_worktree(tmp_path):
    main, svc = _repo(tmp_path)
    wt = tmp_path / "main--login"

    svc.add_worktree(str(wt), "feature/login")
    assert wt.is_dir()
    assert is_linked_worktree(wt)  # .git is a file
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "feature/login"

    worktrees = svc.list_worktrees()
    branches = {w.get("branch") for w in worktrees}
    assert "feature/login" in branches
    assert any(w.get("path", "").endswith("main--login") for w in worktrees)

    svc.remove_worktree(str(wt))
    assert not wt.exists()
    assert all(not w.get("path", "").endswith("main--login") for w in svc.list_worktrees())


def test_add_worktree_duplicate_branch_raises(tmp_path):
    main, svc = _repo(tmp_path)
    svc.add_worktree(str(tmp_path / "wt1"), "feature/x")
    with pytest.raises(RuntimeError):
        svc.add_worktree(str(tmp_path / "wt2"), "feature/x")  # branch already checked out


def test_remove_dirty_worktree_needs_force(tmp_path):
    main, svc = _repo(tmp_path)
    wt = tmp_path / "main--dirty"
    svc.add_worktree(str(wt), "feature/dirty")
    (wt / "new.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        svc.remove_worktree(str(wt))          # git refuses a dirty worktree
    svc.remove_worktree(str(wt), force=True)  # force discards it
    assert not wt.exists()
