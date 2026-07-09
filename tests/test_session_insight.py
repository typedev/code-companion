"""Phase 8.1: session insight parsing + the mtime/size-keyed cache service."""
import json
import os

import pytest

from src.models import Session, SessionInsight
from src.services.history import HistoryService
from src.services import session_insight_service as sis_mod
from src.services.session_insight_service import SessionInsightService


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _assistant(text=None, tool=None, model="claude-opus-4-8", usage=None, ts="2026-07-08T10:00:00Z"):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool is not None:
        name, inp = tool
        content.append({"type": "tool_use", "name": name, "input": inp})
    message = {"model": model, "content": content}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "timestamp": ts, "message": message}


def _user(text, ts="2026-07-08T09:59:00Z"):
    return {"type": "user", "timestamp": ts, "message": {"content": text}}


def _tool_result(tool_id="t1", ts="2026-07-08T10:00:05Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}


USAGE = {"input_tokens": 100, "output_tokens": 50,
         "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5}


def _write_session(directory, name, events) -> Session:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return Session(id=p.stem, path=p)


# --------------------------------------------------------------------------- #
# parser: HistoryService.parse_session_insight
# --------------------------------------------------------------------------- #
def test_usage_summed_per_model(tmp_path):
    s = _write_session(tmp_path, "s.jsonl", [
        _user("hi"),
        _assistant(text="a", model="claude-opus-4-8", usage=USAGE),
        _assistant(text="b", model="claude-opus-4-8", usage=USAGE),
        _assistant(text="c", model="claude-haiku-4-5", usage={"input_tokens": 7, "output_tokens": 3}),
    ])
    ins = HistoryService().parse_session_insight(s.path)

    assert set(ins.models) == {"claude-opus-4-8", "claude-haiku-4-5"}
    opus = ins.usage_by_model["claude-opus-4-8"]
    assert (opus.input, opus.output, opus.cache_creation, opus.cache_read) == (200, 100, 20, 10)
    haiku = ins.usage_by_model["claude-haiku-4-5"]
    assert (haiku.input, haiku.output) == (7, 3)
    # total across all models & buckets
    assert ins.total_tokens == (200 + 100 + 20 + 10) + (7 + 3)


def test_files_touched_deduped_and_filtered(tmp_path):
    s = _write_session(tmp_path, "s.jsonl", [
        _assistant(tool=("Edit", {"file_path": "/proj/a.py"}), usage=USAGE),
        _assistant(tool=("Write", {"file_path": "/proj/b.py"}), usage=USAGE),
        _assistant(tool=("Edit", {"file_path": "/proj/a.py"}), usage=USAGE),   # dup
        _assistant(tool=("NotebookEdit", {"notebook_path": "/proj/n.ipynb"}), usage=USAGE),
        _assistant(tool=("Read", {"file_path": "/proj/ignored.py"}), usage=USAGE),  # not a write
        _assistant(tool=("Bash", {"command": "ls"}), usage=USAGE),
    ])
    ins = HistoryService().parse_session_insight(s.path)
    assert ins.files_touched == ["/proj/a.py", "/proj/b.py", "/proj/n.ipynb"]


def test_first_prompt_last_reply_and_counts(tmp_path):
    s = _write_session(tmp_path, "s.jsonl", [
        _user("first question", ts="2026-07-08T09:00:00Z"),
        _assistant(text="first answer", usage=USAGE, ts="2026-07-08T09:00:10Z"),
        _tool_result(ts="2026-07-08T09:00:20Z"),          # user event, not a prompt
        _user("second question", ts="2026-07-08T09:01:00Z"),
        _assistant(text="final answer", usage=USAGE, ts="2026-07-08T09:02:00Z"),
    ])
    ins = HistoryService().parse_session_insight(s.path)
    assert ins.first_prompt == "first question"
    assert ins.last_assistant_text == "final answer"
    assert ins.message_count == 5  # 3 user (incl. tool_result) + 2 assistant
    assert ins.first_ts.isoformat() == "2026-07-08T09:00:00+00:00"
    assert ins.last_ts.isoformat() == "2026-07-08T09:02:00+00:00"


def test_usage_counted_once_per_message_id(tmp_path):
    # A single assistant message is split into 3 lines (thinking/text/tool_use),
    # each repeating the same usage under the same message.id -> count it once.
    def line(ctype, mid="msg_abc"):
        return {"type": "assistant", "timestamp": "2026-07-08T10:00:00Z",
                "message": {"id": mid, "model": "claude-opus-4-8", "usage": USAGE,
                            "content": [{"type": ctype, "text": "x"} if ctype != "tool_use"
                                        else {"type": "tool_use", "name": "Edit",
                                              "input": {"file_path": "/a.py"}}]}}
    s = _write_session(tmp_path, "s.jsonl", [
        line("thinking"), line("text"), line("tool_use"),
        # a genuinely separate response with its own id -> counted again
        line("text", mid="msg_def"),
    ])
    ins = HistoryService().parse_session_insight(s.path)
    opus = ins.usage_by_model["claude-opus-4-8"]
    # two distinct message ids, not four lines
    assert opus.input == 200
    assert ins.files_touched == ["/a.py"]  # tool_use block still processed


def test_zero_usage_synthetic_model_ignored(tmp_path):
    zero = {"input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    s = _write_session(tmp_path, "s.jsonl", [
        _assistant(text="real", model="claude-opus-4-8", usage=USAGE),
        {"type": "assistant", "timestamp": "2026-07-08T10:01:00Z",
         "message": {"id": "syn1", "model": "<synthetic>", "usage": zero,
                     "content": [{"type": "text", "text": "synthetic"}]}},
    ])
    ins = HistoryService().parse_session_insight(s.path)
    assert ins.models == ["claude-opus-4-8"]  # <synthetic> dropped
    assert "<synthetic>" not in ins.usage_by_model


def test_partial_tail_is_ignored(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "s.jsonl"
    good = json.dumps(_assistant(text="ok", usage=USAGE)) + "\n"
    p.write_text(good + '{"type":"assistant","message":{"content":[{"type":"tex', encoding="utf-8")
    ins = HistoryService().parse_session_insight(p)
    assert ins.last_assistant_text == "ok"
    assert ins.usage_by_model["claude-opus-4-8"].input == 100


def test_missing_file_returns_empty_insight(tmp_path):
    ins = HistoryService().parse_session_insight(tmp_path / "nope.jsonl")
    assert ins.total_tokens == 0
    assert ins.files_touched == []
    assert ins.message_count == 0


# --------------------------------------------------------------------------- #
# service: SessionInsightService (cache)
# --------------------------------------------------------------------------- #
class _FakeAdapter:
    """Delegates parsing to HistoryService and counts parses to assert cache hits."""

    def __init__(self):
        self._svc = HistoryService()
        self.parse_calls = 0

    def get_session_insight(self, session):
        self.parse_calls += 1
        return self._svc.parse_session_insight(session.path)

    def get_sessions_for_path(self, project_path):
        from pathlib import Path
        files = sorted(Path(project_path).glob("*.jsonl"))
        return [Session(id=p.stem, path=p) for p in files]


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(sis_mod, "get_config_dir", lambda: tmp_path / "config")
    return SessionInsightService()


def test_cache_hit_skips_reparse(service, tmp_path):
    sess_dir = tmp_path / "sessions"
    s = _write_session(sess_dir, "s.jsonl", [_assistant(text="a", usage=USAGE)])
    adapter = _FakeAdapter()

    first = service.get_insight(s, adapter, sess_dir)
    assert adapter.parse_calls == 1
    assert first.total_tokens == 165

    second = service.get_insight(s, adapter, sess_dir)
    assert adapter.parse_calls == 1  # served from cache, no re-parse
    assert second.total_tokens == 165


def test_cache_invalidated_when_file_grows(service, tmp_path):
    sess_dir = tmp_path / "sessions"
    s = _write_session(sess_dir, "s.jsonl", [_assistant(text="a", usage=USAGE)])
    adapter = _FakeAdapter()

    service.get_insight(s, adapter, sess_dir)
    assert adapter.parse_calls == 1

    with s.path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_assistant(text="b", usage=USAGE)) + "\n")

    again = service.get_insight(s, adapter, sess_dir)
    assert adapter.parse_calls == 2  # stat (size) changed -> re-parsed
    assert again.usage_by_model["claude-opus-4-8"].input == 200


def test_index_persisted_to_disk(service, tmp_path):
    sess_dir = tmp_path / "sessions"
    s = _write_session(sess_dir, "s.jsonl", [_assistant(text="a", usage=USAGE)])
    service.get_insight(s, _FakeAdapter(), sess_dir)

    index_dir = tmp_path / "config" / "session-insights"
    files = list(index_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["_schema"] == sis_mod._SCHEMA_VERSION
    entries = data["entries"]
    assert s.id in entries
    assert entries[s.id]["insight"]["usage_by_model"]["claude-opus-4-8"]["input"] == 100


def test_stale_schema_is_discarded(service, tmp_path, monkeypatch):
    sess_dir = tmp_path / "sessions"
    s = _write_session(sess_dir, "s.jsonl", [_assistant(text="a", usage=USAGE)])
    adapter = _FakeAdapter()
    service.get_insight(s, adapter, sess_dir)
    assert adapter.parse_calls == 1

    # A newer parser bumps the schema -> a fresh service must ignore old entries
    # even though the file's stat is unchanged.
    monkeypatch.setattr(sis_mod, "_SCHEMA_VERSION", sis_mod._SCHEMA_VERSION + 1)
    fresh_service = SessionInsightService()
    fresh_adapter = _FakeAdapter()
    fresh_service.get_insight(s, fresh_adapter, sess_dir)
    assert fresh_adapter.parse_calls == 1  # re-parsed, not served from stale cache


def test_get_project_insights_caches_all(service, tmp_path):
    sess_dir = tmp_path / "sessions"
    _write_session(sess_dir, "a.jsonl", [_assistant(text="a", usage=USAGE)])
    _write_session(sess_dir, "b.jsonl", [_assistant(text="b", usage=USAGE)])
    adapter = _FakeAdapter()

    first = service.get_project_insights(adapter, sess_dir)
    assert len(first) == 2
    assert adapter.parse_calls == 2

    second = service.get_project_insights(adapter, sess_dir)
    assert len(second) == 2
    assert adapter.parse_calls == 2  # all cached on the second sweep


def test_get_latest_insight_picks_newest_mtime(service, tmp_path):
    sess_dir = tmp_path / "sessions"
    old = _write_session(sess_dir, "old.jsonl", [_assistant(text="old", usage=USAGE)])
    new = _write_session(sess_dir, "new.jsonl", [_assistant(text="newest reply", usage=USAGE)])
    os.utime(old.path, (1000, 1000))
    os.utime(new.path, (2000, 2000))

    latest = service.get_latest_insight(_FakeAdapter(), sess_dir)
    assert latest is not None
    assert latest.last_assistant_text == "newest reply"


def test_get_latest_insight_no_sessions_returns_none(service, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert service.get_latest_insight(_FakeAdapter(), empty) is None
