"""Phase 4 git backend (CLI): commit/branch migration + amend/checkout/upstream."""

import pytest

from src.services.git_service import GitService
from tests.helpers import git, init_repo, make_bare


def _repo(tmp_path, name="r", remote=None):
    """A git repo with local user config (so GitService's CLI commits work)."""
    path = init_repo(tmp_path / name, remote=remote, commit=True)
    git(path, "branch", "-M", "main")  # deterministic branch name across git defaults
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    svc = GitService(path)
    svc.open()
    return path, svc


def test_commit_returns_short_hash_and_refuses_empty(tmp_path):
    path, svc = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="Nothing staged"):
        svc.commit("noop")

    (path / "f.txt").write_text("x\n", encoding="utf-8")
    git(path, "add", "-A")
    short = svc.commit("add f")
    assert short and len(short) >= 7
    assert git(path, "log", "-1", "--pretty=%s") == "add f"
    assert git(path, "rev-parse", "--short", "HEAD") == short


def test_amend_changes_message_and_preserves_author(tmp_path):
    path, svc = _repo(tmp_path)
    (path / "f.txt").write_text("x\n", encoding="utf-8")
    git(path, "add", "-A")
    svc.commit("orig msg")
    author = git(path, "log", "-1", "--pretty=%an <%ae>")
    count_before = git(path, "rev-list", "--count", "HEAD")

    svc.amend_commit("amended msg")
    assert git(path, "log", "-1", "--pretty=%s") == "amended msg"
    assert git(path, "log", "-1", "--pretty=%an <%ae>") == author
    # Amend rewrites HEAD, not a new commit: history length is unchanged.
    assert git(path, "rev-list", "--count", "HEAD") == count_before


def test_create_branch_from_head_and_ref(tmp_path):
    path, svc = _repo(tmp_path)
    svc.create_branch("feature")
    branches = git(path, "branch", "--format=%(refname:short)").split()
    assert "feature" in branches

    head = git(path, "rev-parse", "HEAD")
    svc.create_branch("from-hash", head)
    assert "from-hash" in git(path, "branch", "--format=%(refname:short)").split()

    with pytest.raises(RuntimeError):
        svc.create_branch("bad", "nonexistent-ref")


def test_switch_branch_changes_head_and_errors_clearly(tmp_path):
    path, svc = _repo(tmp_path)
    svc.create_branch("dev")
    svc.switch_branch("dev")
    assert git(path, "rev-parse", "--abbrev-ref", "HEAD") == "dev"

    with pytest.raises(RuntimeError):
        svc.switch_branch("no-such-branch")


def test_has_upstream(tmp_path):
    bare = make_bare(tmp_path)
    path, svc = _repo(tmp_path, remote=str(bare))
    assert svc.has_upstream() is False
    git(path, "push", "-q", "-u", "origin", "main")
    assert svc.has_upstream() is True


def test_checkout_remote_tracking(tmp_path):
    bare = make_bare(tmp_path)
    prod, _ = _repo(tmp_path, name="prod", remote=str(bare))
    git(prod, "push", "-q", "-u", "origin", "main")
    git(prod, "switch", "-c", "feature")
    (prod / "g.txt").write_text("y\n", encoding="utf-8")
    git(prod, "add", "-A")
    git(prod, "commit", "-q", "-m", "feat")
    git(prod, "push", "-q", "-u", "origin", "feature")

    cons = tmp_path / "cons"
    git(tmp_path, "clone", "-q", str(bare), str(cons))
    git(cons, "config", "user.name", "Test")
    git(cons, "config", "user.email", "test@example.com")
    svc = GitService(cons)
    svc.open()

    local = svc.checkout_remote_tracking("origin/feature")
    assert local == "feature"
    assert git(cons, "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    assert git(cons, "rev-parse", "--abbrev-ref", "feature@{upstream}") == "origin/feature"
    # Idempotent: calling again when the local branch exists just switches to it.
    assert svc.checkout_remote_tracking("origin/feature") == "feature"
