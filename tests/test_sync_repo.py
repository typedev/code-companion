"""CP3 tests: SyncRepo / SyncLock / recovery against a local bare repo."""

import subprocess

import pytest

from src.services.sync_lock import SyncBusy, SyncLock
from src.services.sync_recovery import recover
from src.services.sync_repo import RebaseConflict, SyncRepo

from tests.helpers import _GIT_ENV, make_bare


def raw_git(cwd, *args):
    """Run git allowing non-zero exit (for inducing conflict states)."""
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=_GIT_ENV
    )


def seed_remote(tmp_path, bare, content="base\n"):
    """Put an initial commit on the bare remote via a throwaway clone."""
    seed = tmp_path / "seed"
    repo = SyncRepo(seed, str(bare))
    repo.clone()
    (seed / "file.txt").write_text(content, encoding="utf-8")
    repo.commit_all("seed")
    repo.push()
    return repo


# --------------------------------------------------------------------------- #
# SyncRepo
# --------------------------------------------------------------------------- #

def test_clone_commit_push_roundtrip(tmp_path):
    bare = make_bare(tmp_path)
    a = SyncRepo(tmp_path / "a", str(bare))
    a.clone()
    assert a.exists_locally()
    (tmp_path / "a" / "file.txt").write_text("hello", encoding="utf-8")
    h = a.commit_all("first")
    assert h and len(h) == 40
    a.push()

    b = SyncRepo(tmp_path / "b", str(bare))
    b.clone()
    assert (tmp_path / "b" / "file.txt").read_text() == "hello"


def test_commit_all_returns_none_when_clean(tmp_path):
    bare = make_bare(tmp_path)
    a = SyncRepo(tmp_path / "a", str(bare))
    a.clone()
    (tmp_path / "a" / "f").write_text("x", encoding="utf-8")
    a.commit_all("c1")
    assert a.commit_all("noop") is None  # nothing changed


def test_pull_rebase_fast_forward(tmp_path):
    bare = make_bare(tmp_path)
    seed_remote(tmp_path, bare)
    a = SyncRepo(tmp_path / "a", str(bare))
    a.clone()
    b = SyncRepo(tmp_path / "b", str(bare))
    b.clone()
    # a advances and pushes
    (tmp_path / "a" / "file.txt").write_text("changed\n", encoding="utf-8")
    a.commit_all("a-change")
    a.push()
    # b pulls -> sees a's change
    b.pull_rebase()
    assert (tmp_path / "b" / "file.txt").read_text() == "changed\n"


def test_pull_rebase_conflict_raises_and_aborts(tmp_path):
    bare = make_bare(tmp_path)
    seed_remote(tmp_path, bare)
    a = SyncRepo(tmp_path / "a", str(bare))
    a.clone()
    b = SyncRepo(tmp_path / "b", str(bare))
    b.clone()
    # a and b change the SAME file differently
    (tmp_path / "a" / "file.txt").write_text("AAA\n", encoding="utf-8")
    a.commit_all("a")
    a.push()
    (tmp_path / "b" / "file.txt").write_text("BBB\n", encoding="utf-8")
    b.commit_all("b")

    with pytest.raises(RebaseConflict) as exc:
        b.pull_rebase()
    assert "file.txt" in exc.value.paths
    assert not b.is_mid_rebase()  # aborted, back to a clean state
    assert (tmp_path / "b" / "file.txt").read_text() == "BBB\n"  # local preserved


def test_push_sets_upstream_on_empty_remote(tmp_path):
    bare = make_bare(tmp_path)  # empty, no branch yet
    a = SyncRepo(tmp_path / "a", str(bare))
    a.clone()
    (tmp_path / "a" / "f").write_text("x", encoding="utf-8")
    a.commit_all("first")
    a.push()  # must set upstream without raising
    b = SyncRepo(tmp_path / "b", str(bare))
    b.clone()
    assert (tmp_path / "b" / "f").read_text() == "x"


# --------------------------------------------------------------------------- #
# recovery
# --------------------------------------------------------------------------- #

def test_recover_noop_on_clean_clone(tmp_path):
    bare = make_bare(tmp_path)
    seed_remote(tmp_path, bare)
    a = SyncRepo(tmp_path / "a", str(bare))
    a.clone()
    recover(a)  # must not raise
    assert not a.is_mid_rebase()


def test_recover_aborts_mid_rebase(tmp_path):
    bare = make_bare(tmp_path)
    seed_remote(tmp_path, bare)
    a = SyncRepo(tmp_path / "a", str(bare))
    a.clone()
    b = SyncRepo(tmp_path / "b", str(bare))
    b.clone()
    (tmp_path / "a" / "file.txt").write_text("AAA\n", encoding="utf-8")
    a.commit_all("a")
    a.push()
    (tmp_path / "b" / "file.txt").write_text("BBB\n", encoding="utf-8")
    b.commit_all("b")
    # Induce a stuck rebase via raw git (no auto-abort).
    raw_git(tmp_path / "b", "-c", "rebase.autostash=false", "pull", "--rebase")
    assert b.is_mid_rebase()
    recover(b)
    assert not b.is_mid_rebase()


# --------------------------------------------------------------------------- #
# SyncLock
# --------------------------------------------------------------------------- #

def test_sync_lock_context_manager(tmp_path):
    lock = SyncLock(tmp_path / "clone")
    with lock:
        assert lock.lock_file.exists()
        assert lock._fd is not None  # we hold the flock
    # flock is released on exit (fd closed). The lock file is intentionally NOT
    # unlinked (removing it under flock is a race footgun); it is simply free.
    assert lock._fd is None
    assert lock.is_locked() is False


def test_sync_lock_stale_holder_is_free(tmp_path):
    # A leftover lock file whose owner has died is not "locked": the kernel
    # already freed the flock, so a fresh probe succeeds. No PID heuristic.
    lock = SyncLock(tmp_path / "clone")
    lock.lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock.lock_file.write_text("999999", encoding="utf-8")  # dead pid, no flock
    assert lock.is_locked() is False
    # It is acquirable despite the leftover file.
    assert lock.acquire() is True
    lock.release()


def test_sync_lock_busy_raises(tmp_path):
    # A second holder (separate open fd, as another process would have) is
    # denied while the first holds the flock. flock treats distinct fds to the
    # same file independently, so this exercises the real busy path in-process.
    held = SyncLock(tmp_path / "clone")
    assert held.acquire() is True
    try:
        other = SyncLock(tmp_path / "clone")
        assert other.is_locked() is True
        with pytest.raises(SyncBusy):
            with other:
                pass
    finally:
        held.release()
    # Once released, the lock is takeable again.
    with SyncLock(tmp_path / "clone"):
        pass
