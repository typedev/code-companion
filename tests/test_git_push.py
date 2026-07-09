"""Roadmap 3.5: non-fast-forward push -> PushRejected; force-with-lease safety."""

import pytest

from src.services.git_service import GitService, PushRejected
from tests.helpers import git, make_bare


def _clone(tmp_path, bare, name):
    git(tmp_path, "clone", "-q", str(bare), name)
    return tmp_path / name


def _commit(repo, fname, text, msg):
    (repo / fname).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def diverged(tmp_path):
    """Two clones of a bare remote where clone B has diverged from the remote."""
    bare = make_bare(tmp_path)
    a = _clone(tmp_path, bare, "a")
    _commit(a, "f.txt", "one\n", "c1")
    git(a, "push", "-q", "-u", "origin", "main")

    b = _clone(tmp_path, bare, "b")   # has c1

    _commit(a, "f.txt", "two\n", "c2")
    git(a, "push", "-q")              # remote now at c2

    _commit(b, "f.txt", "two-prime\n", "c2prime")  # b diverges from remote's c2

    svc = GitService(b)
    svc.open()
    return bare, a, b, svc


def test_push_rejected_raises(diverged):
    _, _, _, svc = diverged
    with pytest.raises(PushRejected):
        svc.push()


def test_cleanup_askpass_removes_temp_script(tmp_path):
    """The temp GIT_ASKPASS helper must be deleted after a git op (no /tmp leak)."""
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    svc = GitService(repo)

    script = tmp_path / "git_askpass_x.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    svc._askpass_script = str(script)

    svc._cleanup_askpass()
    assert not script.exists()
    assert svc._askpass_script is None

    # Idempotent: a second call with nothing to clean is a no-op.
    svc._cleanup_askpass()


def test_force_with_lease_refuses_when_lease_stale(diverged):
    # b never fetched the remote's c2, so its lease (origin/main) is stale;
    # --force-with-lease must refuse rather than clobber c2.
    _, _, _, svc = diverged
    with pytest.raises(PushRejected):
        svc.push(force_with_lease=True)


def test_force_with_lease_succeeds_after_fetch(diverged):
    bare, _, b, svc = diverged
    git(b, "fetch", "-q", "origin")          # lease now current (origin/main == c2)
    result = svc.push(force_with_lease=True)
    assert "successful" in result.lower()
    # The bare remote's main now points at b's commit.
    remote_head = git(bare, "log", "-1", "--format=%s", "main")
    assert remote_head == "c2prime"
