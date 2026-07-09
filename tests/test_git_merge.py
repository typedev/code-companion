"""Stage 5: GitService.preview_merge / merge_branch."""
import pytest

from src.services.git_service import GitService
from tests.helpers import git, init_repo


def _repo(tmp_path):
    main = init_repo(tmp_path / "main", commit=True)
    git(main, "branch", "-M", "main")
    git(main, "config", "user.name", "Test")
    git(main, "config", "user.email", "test@example.com")
    svc = GitService(main)
    svc.open()
    return main, svc


def _branch_with_file(repo, branch, name, body):
    git(repo, "switch", "-c", branch, "main")
    (repo / name).write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"{branch}: {name}")
    git(repo, "switch", "main")


def test_preview_clean_merge(tmp_path):
    main, svc = _repo(tmp_path)
    _branch_with_file(main, "feature/a", "a.txt", "aaa\n")  # touches a new file
    clean, conflicts = svc.preview_merge("feature/a")
    assert clean is True and conflicts == []


def test_preview_detects_conflict(tmp_path):
    main, svc = _repo(tmp_path)
    # both main and the branch change README.md differently -> conflict
    git(main, "switch", "-c", "feature/x", "main")
    (main / "README.md").write_text("branch side\n", encoding="utf-8")
    git(main, "add", "-A"); git(main, "commit", "-q", "-m", "x")
    git(main, "switch", "main")
    (main / "README.md").write_text("main side\n", encoding="utf-8")
    git(main, "add", "-A"); git(main, "commit", "-q", "-m", "main change")

    clean, conflicts = svc.preview_merge("feature/x")
    assert clean is False
    assert any("README.md" in c for c in conflicts)


def test_merge_branch_integrates_clean(tmp_path):
    main, svc = _repo(tmp_path)
    _branch_with_file(main, "feature/a", "a.txt", "aaa\n")
    svc.merge_branch("feature/a")
    assert (main / "a.txt").exists()
    assert git(main, "log", "-1", "--pretty=%s").startswith("Merge feature/a")


def test_merge_branch_conflict_raises_and_aborts(tmp_path):
    main, svc = _repo(tmp_path)
    git(main, "switch", "-c", "feature/x", "main")
    (main / "README.md").write_text("branch\n", encoding="utf-8")
    git(main, "add", "-A"); git(main, "commit", "-q", "-m", "x")
    git(main, "switch", "main")
    (main / "README.md").write_text("main\n", encoding="utf-8")
    git(main, "add", "-A"); git(main, "commit", "-q", "-m", "m")

    with pytest.raises(RuntimeError):
        svc.merge_branch("feature/x")
    # repo must not be left mid-merge
    assert git(main, "status", "--porcelain").strip() == ""
    assert not (main / ".git" / "MERGE_HEAD").exists()
