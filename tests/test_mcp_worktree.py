"""Stage 5: MCP worktree-report tools (report / list / resolve)."""
from types import SimpleNamespace

from src.services.mcp_server import McpServer
from src.services import worktree_reports as wr
from src.services.git_service import GitService
from tests.helpers import git, init_repo


def _srv(window):
    """A minimal stand-in for McpServer — the _do handlers only touch self.window."""
    return SimpleNamespace(window=window)


def test_report_list_resolve_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "get_config_dir", lambda: tmp_path / "cfg")
    main = init_repo(tmp_path / "main", commit=True)
    git(main, "branch", "-M", "main")
    git(main, "config", "user.name", "T"); git(main, "config", "user.email", "t@e")
    wt = tmp_path / "main--login"
    GitService(main).add_worktree(str(wt), "feature/login")

    # worktree side reports completion
    wt_srv = _srv(SimpleNamespace(_is_worktree=True, _worktree_parent=main, project_path=wt))
    res = McpServer._do_report_worktree_complete(wt_srv, "Added login flow", "12/14", "2 findings")
    assert res["ok"] is True and res["branch"] == "feature/login"

    # main side lists it
    main_srv = _srv(SimpleNamespace(project_path=main))
    listed = McpServer._do_list_worktree_reports(main_srv)
    assert listed["count"] == 1
    r = listed["reports"][0]
    assert r["branch"] == "feature/login" and r["tests"] == "12/14"
    assert "Added login flow" in r["body"] and "2 findings" in r["body"]

    # main resolves it after merging
    assert McpServer._do_resolve_worktree_report(main_srv, "feature/login")["ok"] is True
    assert McpServer._do_list_worktree_reports(main_srv)["count"] == 0


def test_report_rejected_outside_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "get_config_dir", lambda: tmp_path / "cfg")
    srv = _srv(SimpleNamespace(_is_worktree=False))
    res = McpServer._do_report_worktree_complete(srv, "x", "", "")
    assert res["ok"] is False and "worktree" in res["error"]
