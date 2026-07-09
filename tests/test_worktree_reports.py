"""Stage 5: local worktree completion report channel."""
import pytest

from src.services import worktree_reports as wr


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "get_config_dir", lambda: tmp_path)
    return tmp_path


def test_write_list_resolve(config_dir):
    wr.write_report("/home/u/proj", "feature/login", "/home/u/proj--login",
                    summary="Added login flow", tests="12/14 pass",
                    review="2 minor findings")
    reports = wr.list_reports("/home/u/proj")
    assert len(reports) == 1
    r = reports[0]
    assert r["branch"] == "feature/login"
    assert r["tests"] == "12/14 pass"
    assert r["worktree_path"].endswith("proj--login")
    assert "Added login flow" in r["body"] and "## Review" in r["body"]
    assert wr.count_reports("/home/u/proj") == 1

    assert wr.resolve_report("/home/u/proj", "feature/login") is True
    assert wr.list_reports("/home/u/proj") == []
    assert wr.resolve_report("/home/u/proj", "feature/login") is False  # already gone


def test_reports_filtered_by_parent(config_dir):
    wr.write_report("/home/u/projA", "feature/a", "/home/u/projA--a", summary="A")
    wr.write_report("/home/u/projB", "feature/b", "/home/u/projB--b", summary="B")
    assert {r["branch"] for r in wr.list_reports("/home/u/projA")} == {"feature/a"}
    assert wr.count_reports("/home/u/projB") == 1
    assert wr.count_reports("/home/u/other") == 0


def test_overwrite_same_worktree(config_dir):
    wr.write_report("/home/u/proj", "feature/x", "/home/u/proj--x", summary="first")
    wr.write_report("/home/u/proj", "feature/x", "/home/u/proj--x", summary="second")
    reports = wr.list_reports("/home/u/proj")
    assert len(reports) == 1  # same (parent, branch) key -> single file
    assert "second" in reports[0]["body"]


def test_list_missing_dir(config_dir):
    assert wr.list_reports("/home/u/proj") == []  # no reports dir yet
