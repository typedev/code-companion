"""Phase 8.3: git helpers correlating a session with its commits/diff."""
from datetime import datetime, timedelta

from src.services.git_service import GitService
from tests.helpers import git, init_repo


def _repo(tmp_path):
    path = init_repo(tmp_path / "r", commit=True)
    git(path, "branch", "-M", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    svc = GitService(path)
    svc.open()
    return path, svc


def _commit(path, name, body="x\n"):
    (path / name).write_text(body, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", f"add {name}")
    return git(path, "rev-parse", "HEAD")


def test_commits_in_range_filters_by_window(tmp_path):
    path, svc = _repo(tmp_path)
    _commit(path, "a.txt")
    _commit(path, "b.txt")

    now = datetime.now()
    window = svc.get_commits_in_range(now - timedelta(minutes=5), now + timedelta(minutes=5))
    subjects = [c.message for c in window]
    assert "add a.txt" in subjects and "add b.txt" in subjects

    # A window far in the past catches nothing.
    old = datetime(2000, 1, 1)
    assert svc.get_commits_in_range(old, old + timedelta(days=1)) == []
    # Newest commit is flagged is_head.
    assert window[0].is_head is True


def test_range_diff_spans_first_to_last(tmp_path):
    path, svc = _repo(tmp_path)
    first = _commit(path, "a.txt", "aaa\n")
    last = _commit(path, "b.txt", "bbb\n")
    diff = svc.get_commit_range_diff(first, last)
    # both files introduced across the range appear in the unified diff
    assert "a.txt" in diff and "b.txt" in diff
    assert "+aaa" in diff and "+bbb" in diff


def test_range_diff_root_commit_safe(tmp_path):
    # first commit is the repo root (README from init) -> no parent; must not crash
    path, svc = _repo(tmp_path)
    root = git(path, "rev-parse", "HEAD")
    diff = svc.get_commit_range_diff(root, root)
    assert "README.md" in diff


def test_paths_diff_shows_uncommitted(tmp_path):
    path, svc = _repo(tmp_path)
    (path / "README.md").write_text("hello\nCHANGED\n", encoding="utf-8")
    (path / "other.txt").write_text("noise\n", encoding="utf-8")
    diff = svc.get_paths_diff(["README.md"])
    assert "+CHANGED" in diff
    assert "other.txt" not in diff  # filtered to the requested path
    assert svc.get_paths_diff([]) == ""
