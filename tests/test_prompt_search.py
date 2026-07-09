"""Phase 8.5: cross-project prompt search."""
import json

import pytest

from src.services import prompt_search as ps


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(ps.claude_paths, "projects_root", lambda: root)
    return root


def _session(root, encoded_dir, session_id, events):
    d = root / encoded_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )


def _user(text, cwd="/home/u/proj-a", ts="2026-07-09T10:00:00Z"):
    return {"type": "user", "timestamp": ts, "cwd": cwd,
            "message": {"content": text}}


def _assistant(text="ok"):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_finds_user_prompts_across_projects(projects_root):
    _session(projects_root, "-home-u-proj-a", "s1", [
        _user("How do I configure the widget cache?", cwd="/home/u/proj-a"),
        _assistant("widget cache answer"),
    ])
    _session(projects_root, "-home-u-proj-b", "s2", [
        _user("unrelated question about colors", cwd="/home/u/proj-b"),
        _user("widget rendering pipeline", cwd="/home/u/proj-b", ts="2026-07-09T12:00:00Z"),
    ])

    hits = ps.search_prompts("widget")
    assert len(hits) == 2
    # newest first
    assert hits[0].session_id == "s2"
    assert hits[0].project_path == "/home/u/proj-b"
    assert hits[0].project_name == "proj-b"
    assert "widget rendering" in hits[0].snippet


def test_ignores_assistant_and_nonmatching_lines(projects_root):
    _session(projects_root, "-home-u-proj-a", "s1", [
        _user("please summarize"),
        _assistant("here is the widget summary"),  # 'widget' only in assistant text
    ])
    assert ps.search_prompts("widget") == []


def test_short_query_and_missing_root(projects_root, monkeypatch):
    _session(projects_root, "-home-u-proj-a", "s1", [_user("hello world")])
    assert ps.search_prompts("h") == []           # too short
    assert ps.search_prompts("  ") == []
    monkeypatch.setattr(ps.claude_paths, "projects_root",
                        lambda: projects_root.parent / "nope")
    assert ps.search_prompts("hello") == []


def test_filters_injected_meta_events(projects_root):
    _session(projects_root, "-home-u-proj-a", "s1", [
        # real prompt
        _user("please add worktree support", cwd="/home/u/proj-a"),
        # harness-injected user events that mention the query but aren't prompts
        {"type": "user", "isMeta": True, "timestamp": "2026-07-09T10:01:00Z",
         "message": {"content": "worktree via a command expansion"}},
        {"type": "user", "timestamp": "2026-07-09T10:02:00Z",
         "message": {"content": "<task-notification> worktree job done </task-notification>"}},
        {"type": "user", "isCompactSummary": True, "timestamp": "2026-07-09T10:03:00Z",
         "message": {"content": "This session is being continued... worktree"}},
    ])
    hits = ps.search_prompts("worktree")
    assert len(hits) == 1
    assert hits[0].snippet == "please add worktree support"


def test_cwd_fallback_decodes_dir(projects_root):
    # a user event without cwd falls back to decoding the encoded dir name
    _session(projects_root, "-home-u-proj-c", "s3", [
        {"type": "user", "timestamp": "2026-07-09T09:00:00Z",
         "message": {"content": "decode me please"}},
    ])
    hits = ps.search_prompts("decode me")
    assert len(hits) == 1
    assert hits[0].project_path.endswith("proj-c")
