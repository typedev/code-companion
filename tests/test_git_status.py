"""Roadmap 3.8: single porcelain status source + rename old_path + binary diff."""

import subprocess

import pytest

from src.services.git_service import GitService, FileStatus

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.x",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": __import__("os").environ.get("PATH", "")}


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, env=_ENV)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    _git(d, "init")
    (d / "a.txt").write_text("hello\nworld\n")
    _git(d, "add", "a.txt")
    _git(d, "commit", "-m", "init")
    svc = GitService(d)
    svc.open()
    return d, svc


def test_porcelain_modified_unstaged(repo):
    d, svc = repo
    (d / "a.txt").write_text("hello\nchanged\n")
    staged, unstaged = svc.get_porcelain_status()
    assert staged == []
    assert [(f.path, f.status, f.staged) for f in unstaged] == [
        ("a.txt", FileStatus.MODIFIED, False)
    ]


def test_porcelain_staged_new(repo):
    d, svc = repo
    (d / "new.txt").write_text("x\n")
    _git(d, "add", "new.txt")
    staged, unstaged = svc.get_porcelain_status()
    assert [(f.path, f.status, f.staged) for f in staged] == [
        ("new.txt", FileStatus.ADDED, True)
    ]
    assert unstaged == []


def test_porcelain_rename_populates_old_path(repo):
    d, svc = repo
    _git(d, "mv", "a.txt", "b.txt")
    staged, unstaged = svc.get_porcelain_status()
    renames = [f for f in staged if f.status == FileStatus.RENAMED]
    assert len(renames) == 1
    assert renames[0].path == "b.txt"
    assert renames[0].old_path == "a.txt"


def test_has_uncommitted_changes_uses_porcelain(repo):
    d, svc = repo
    assert svc.has_uncommitted_changes() is False
    (d / "a.txt").write_text("dirty\n")
    assert svc.has_uncommitted_changes() is True


def test_get_diff_text(repo):
    d, svc = repo
    (d / "a.txt").write_text("hello\nCHANGED\n")
    old, new = svc.get_diff("a.txt", staged=False)
    assert old == "hello\nworld\n"
    assert new == "hello\nCHANGED\n"


def test_get_diff_binary_is_shortcircuited(repo):
    d, svc = repo
    # Commit a binary file, then modify it.
    (d / "img.bin").write_bytes(b"\x00\x01\x02" * 10)
    _git(d, "add", "img.bin")
    _git(d, "commit", "-m", "bin")
    (d / "img.bin").write_bytes(b"\x00\x01\x02" * 40)
    old, new = svc.get_diff("img.bin", staged=False)
    # No decoded-garbage; both sides are human "Binary file (...)" notes.
    assert old.startswith("Binary file (")
    assert new.startswith("Binary file (")
    assert "\x00" not in old and "\x00" not in new
