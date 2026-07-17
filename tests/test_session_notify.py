"""Session notify markers: codex notify script + payload normalization."""

import json
import subprocess

import pytest

from src.services import session_notify


@pytest.fixture(autouse=True)
def _config_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(session_notify, "get_config_dir", lambda: tmp_path)


def test_codex_script_writes_marker_atomically(tmp_path):
    script = session_notify.ensure_codex_notify_script()
    assert script is not None and script.stat().st_mode & 0o777 == 0o700
    payload = json.dumps({"type": "agent-turn-complete",
                          "last-assistant-message": "done", "cwd": "/x"})
    subprocess.run(["/bin/sh", str(script), "cc-abc", payload], check=True)
    markers = session_notify.read_markers()
    assert markers["cc-abc"]["last-assistant-message"] == "done"
    assert not (tmp_path / "notify" / "cc-abc.json.tmp").exists()


def test_codex_script_idempotent_and_versioned(tmp_path):
    first = session_notify.ensure_codex_notify_script()
    content = first.read_text()
    assert session_notify.ensure_codex_notify_script().read_text() == content
    # Stale content (older version) gets regenerated.
    first.write_text("#!/bin/sh\n# old\n")
    assert session_notify.ensure_codex_notify_script().read_text() == content


def test_codex_script_rejects_bad_session_names(tmp_path):
    script = session_notify.ensure_codex_notify_script()
    subprocess.run(["/bin/sh", str(script), "../evil", "{}"], check=True)
    subprocess.run(["/bin/sh", str(script), "", "{}"], check=True)
    assert session_notify.read_markers() == {}


def test_read_markers_normalizes_codex_payload(tmp_path):
    session_notify.notify_dir().mkdir(parents=True)
    (session_notify.notify_dir() / "cc-a.json").write_text(json.dumps(
        {"type": "agent-turn-complete",
         "last-assistant-message": "First line.\nSecond line.", "cwd": "/p"}))
    (session_notify.notify_dir() / "cc-b.json").write_text(json.dumps(
        {"type": "agent-turn-complete", "cwd": "/p"}))
    (session_notify.notify_dir() / "cc-c.json").write_text(json.dumps(
        {"message": "claude says", "cwd": "/p"}))
    markers = session_notify.read_markers()
    assert markers["cc-a"]["message"] == "First line."
    assert markers["cc-b"]["message"] == "Agent finished a turn"
    assert markers["cc-c"]["message"] == "claude says"


def test_clear_command_matches_hook_settings(tmp_path):
    cmd = session_notify.clear_command("cc-x")
    payload = session_notify.hook_settings("cc-x")
    stop_cmd = payload["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert stop_cmd == cmd
    marker = session_notify.marker_path("cc-x")
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    subprocess.run(cmd, shell=True, check=True)
    assert not marker.exists()
