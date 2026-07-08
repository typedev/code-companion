"""Roadmap 5.1 JSONL robustness: encoding safety, truncated tail, list tolerance.

Regression guard for the freeze/crash cluster in the session viewer path:
- a non-UTF-8 byte must not raise (open with errors="replace");
- a truncated trailing line marks the session in_progress instead of vanishing;
- one corrupt session file must not crash the whole sessions *list* (metadata pass).
"""

import json

from src.models import Session, SessionContent
from src.services.history import HistoryService


def _assistant(i: int) -> bytes:
    return (json.dumps({
        "type": "assistant",
        "timestamp": f"2026-07-08T10:00:0{i % 10}Z",
        "message": {"content": [{"type": "text", "text": f"reply {i}"}]},
    }) + "\n").encode()


def _write(tmp_path, name: str, data: bytes) -> Session:
    p = tmp_path / name
    p.write_bytes(data)
    return Session(id=p.stem, path=p)


def test_load_returns_session_content(tmp_path):
    svc = HistoryService()
    result = svc.load_session_content(_write(tmp_path, "s.jsonl", _assistant(0) + _assistant(1)))
    assert isinstance(result, SessionContent)
    assert len(result.messages) == 2
    assert result.in_progress is False


def test_truncated_tail_marks_in_progress(tmp_path):
    svc = HistoryService()
    partial = b'{"type":"assistant","message":{"content":[{"type":"tex'
    result = svc.load_session_content(
        _write(tmp_path, "s.jsonl", _assistant(0) + _assistant(1) + partial)
    )
    # The two complete events survive; the partial tail flags "in progress".
    assert len(result.messages) == 2
    assert result.in_progress is True


def test_broken_middle_line_is_skipped_not_in_progress(tmp_path):
    svc = HistoryService()
    result = svc.load_session_content(
        _write(tmp_path, "s.jsonl", _assistant(0) + b"{not json\n" + _assistant(1))
    )
    assert len(result.messages) == 2
    assert result.in_progress is False


def test_non_utf8_bytes_do_not_crash(tmp_path):
    svc = HistoryService()
    bad = b'{"type":"user","message":{"content":"caf\xe9\x80 bad byte"}}\n'
    result = svc.load_session_content(
        _write(tmp_path, "s.jsonl", _assistant(0) + bad + _assistant(1))
    )
    # errors="replace" keeps the line parseable -> all three messages present.
    assert len(result.messages) == 3
    assert result.in_progress is False


def test_metadata_tolerates_non_utf8_file(tmp_path):
    """One bad-bytes file must not crash the sessions list (the observed bug)."""
    svc = HistoryService()
    bad = b'{"type":"user","message":{"content":"caf\xe9\x80"}}\n'
    session_file = tmp_path / "bad.jsonl"
    session_file.write_bytes(_assistant(0) + bad + _assistant(1))
    meta = svc._parse_session_metadata(session_file)
    assert meta is not None
    assert meta.message_count == 3
