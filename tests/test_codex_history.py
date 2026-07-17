"""Codex rollout history reader: index, cwd filter, content, insight."""

import json
import time
from pathlib import Path

import pytest

from src.models import ContentType, MessageRole
from src.services import codex_history
from src.services.codex_history import CodexHistoryService


def _meta_line(cwd, session_id="0199-aaaa", ts="2026-07-17T14:03:16.267Z"):
    return {
        "timestamp": ts,
        "type": "session_meta",
        "payload": {"id": session_id, "cwd": str(cwd), "timestamp": ts},
    }


def _user_line(text, ts="2026-07-17T14:04:03.255Z"):
    return {
        "timestamp": ts,
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _assistant_line(text, ts="2026-07-17T14:04:05.940Z"):
    return {
        "timestamp": ts,
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _write_rollout(root: Path, name: str, lines: list) -> Path:
    day = root / "sessions" / "2026" / "07" / "17"
    day.mkdir(parents=True, exist_ok=True)
    path = day / name
    path.write_text(
        "\n".join(json.dumps(line) if isinstance(line, dict) else line for line in lines)
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def service(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setattr(codex_history, "get_config_dir", lambda: config)
    return CodexHistoryService(codex_home=tmp_path / "codex")


def test_cwd_filtering(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    _write_rollout(
        service.codex_home, "rollout-a.jsonl",
        [_meta_line(project), _user_line("hello from proj")],
    )
    _write_rollout(
        service.codex_home, "rollout-b.jsonl",
        [_meta_line(other, session_id="0199-bbbb"), _user_line("other project")],
    )
    sessions = service.get_sessions_for_path(project)
    assert [s.id for s in sessions] == ["0199-aaaa"]
    assert sessions[0].preview == "hello from proj"
    assert sessions[0].message_count == 1
    assert sessions[0].timestamp is not None


def test_wrapper_messages_skipped(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    _write_rollout(
        service.codex_home, "rollout-a.jsonl",
        [
            _meta_line(project),
            _user_line("<environment_context>\n  <cwd>/x</cwd>"),
            _user_line("<user_instructions>stuff"),
            _user_line("настоящий вопрос"),
            _assistant_line("ответ"),
        ],
    )
    sessions = service.get_sessions_for_path(project)
    assert sessions[0].preview == "настоящий вопрос"
    assert sessions[0].message_count == 2

    content = service.load_session_content(sessions[0])
    roles = [m.role for m in content.messages]
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT]


def test_index_reuse_and_invalidation(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    path = _write_rollout(
        service.codex_home, "rollout-a.jsonl",
        [_meta_line(project), _user_line("first")],
    )
    assert len(service.get_sessions_for_path(project)) == 1

    # Unchanged file: refresh must not re-read headers (stat-only).
    original = service._read_first_line_meta
    calls = []
    service._read_first_line_meta = lambda p: calls.append(p) or original(p)
    assert len(service.get_sessions_for_path(project)) == 1
    assert calls == []

    # Changed file (size/mtime): header re-read, metadata recomputed.
    time.sleep(0.01)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_user_line("second")) + "\n")
    sessions = service.get_sessions_for_path(project)
    assert calls == [path]
    assert sessions[0].message_count == 2

    # Deleted file: pruned from results.
    path.unlink()
    assert service.get_sessions_for_path(project) == []


def test_index_survives_service_restart(service, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _write_rollout(
        service.codex_home, "rollout-a.jsonl",
        [_meta_line(project), _user_line("hello")],
    )
    assert len(service.get_sessions_for_path(project)) == 1

    fresh = CodexHistoryService(codex_home=service.codex_home)
    original = fresh._read_first_line_meta
    calls = []
    fresh._read_first_line_meta = lambda p: calls.append(p) or original(p)
    assert len(fresh.get_sessions_for_path(project)) == 1
    assert calls == []  # persisted index carried the header facts


def test_content_tool_calls_and_results(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    lines = [
        _meta_line(project),
        _user_line("run something"),
        {
            "timestamp": "2026-07-17T14:04:07.372Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call_1",
                "name": "exec",
                "input": "tools.exec_command({\"cmd\": \"ls\"})",
            },
        },
        {
            "timestamp": "2026-07-17T14:04:07.466Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call_1",
                "output": [{"type": "input_text", "text": "file-a\nfile-b"}],
            },
        },
        {
            "timestamp": "2026-07-17T14:04:08.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "call_2",
                "name": "shell",
                "arguments": "{\"command\": [\"git\", \"status\"]}",
            },
        },
        _assistant_line("done"),
    ]
    _write_rollout(service.codex_home, "rollout-a.jsonl", lines)
    session = service.get_sessions_for_path(project)[0]
    content = service.load_session_content(session)

    assistant = content.messages[-1]
    tools = [b for m in content.messages for b in m.content_blocks
             if b.type == ContentType.TOOL_USE]
    assert [t.tool_name for t in tools] == ["exec", "shell"]
    assert tools[0].tool_output == "file-a\nfile-b"
    assert tools[1].tool_input == {"command": ["git", "status"]}
    assert assistant.text_content == "done"
    assert content.in_progress is False


def test_reasoning_summary_only(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    lines = [
        _meta_line(project),
        _user_line("think"),
        {
            "timestamp": "2026-07-17T14:04:05.449Z",
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "planning the fix"}],
                "encrypted_content": "gAAAA...",
            },
        },
        {
            "timestamp": "2026-07-17T14:04:05.500Z",
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": [], "encrypted_content": "x"},
        },
        _assistant_line("done"),
    ]
    _write_rollout(service.codex_home, "rollout-a.jsonl", lines)
    session = service.get_sessions_for_path(project)[0]
    content = service.load_session_content(session)
    thinking = [b for m in content.messages for b in m.content_blocks
                if b.type == ContentType.THINKING]
    assert [b.text for b in thinking] == ["planning the fix"]


def test_truncated_tail_means_in_progress(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    lines = [
        _meta_line(project),
        _user_line("hello"),
        '{"timestamp": "2026-07-17T14:05:00.000Z", "type": "response_item", "payl',
    ]
    _write_rollout(service.codex_home, "rollout-a.jsonl", lines)
    session = service.get_sessions_for_path(project)[0]
    assert service.load_session_content(session).in_progress is True


def test_insight_tokens_and_model(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    lines = [
        _meta_line(project),
        {
            "timestamp": "2026-07-17T14:04:03.252Z",
            "type": "turn_context",
            "payload": {"turn_id": "t1", "model": "gpt-5.6-terra"},
        },
        _user_line("hello"),
        {
            "timestamp": "2026-07-17T14:04:07.466Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 13362,
                        "cached_input_tokens": 10496,
                        "output_tokens": 125,
                    },
                    "last_token_usage": {
                        "input_tokens": 13362,
                        "cached_input_tokens": 10496,
                        "output_tokens": 125,
                    },
                },
            },
        },
        _assistant_line("all good"),
    ]
    path = _write_rollout(service.codex_home, "rollout-a.jsonl", lines)
    insight = service.parse_session_insight(path)

    assert insight.session_id == "0199-aaaa"
    usage = insight.usage_by_model["gpt-5.6-terra"]
    assert usage.input == 13362 - 10496
    assert usage.cache_read == 10496
    assert usage.cache_creation == 0
    assert usage.output == 125
    assert insight.last_context_tokens == 13362
    assert insight.first_prompt == "hello"
    assert insight.last_assistant_text == "all good"
    assert insight.message_count == 2
    assert insight.first_ts is not None and insight.last_ts is not None


def test_unknown_types_ignored(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    lines = [
        _meta_line(project),
        {"timestamp": "t", "type": "world_state", "payload": {"full": True}},
        {"timestamp": "t", "type": "event_msg", "payload": {"type": "brand_new_thing"}},
        {"timestamp": "t", "type": "response_item", "payload": {"type": "hologram"}},
        "not json at all",
        _user_line("still works"),
    ]
    _write_rollout(service.codex_home, "rollout-a.jsonl", lines)
    sessions = service.get_sessions_for_path(project)
    assert sessions[0].message_count == 1
    content = service.load_session_content(sessions[0])
    assert len(content.messages) == 1


def test_no_sessions_dir(service, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert service.get_sessions_for_path(project) == []
    assert service.find_project_history_dir(project) is None
