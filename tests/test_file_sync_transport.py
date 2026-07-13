"""Phase 2 tests: file-sync wire format, project resolver, and broker round-trip."""

import asyncio
import socket
import time
from pathlib import Path

import pytest

from src.services import dispatch_api
from src.services.dispatch_broker import DispatchBroker
from src.services.file_sync import file_index, project_resolver, wire
from src.services.paired_devices import PairedDevices


# --------------------------------------------------------------------------- #
# wire framing
# --------------------------------------------------------------------------- #

def test_wire_roundtrip():
    files = [("shared/a.txt", b"hello"), ("shared/sub/b.bin", b"\x00\x01\x02"), ("empty", b"")]
    blob = b"".join(wire.encode_file(rel, data) for rel, data in files)
    pos = {"i": 0}

    def read(n):
        chunk = blob[pos["i"]:pos["i"] + n]
        pos["i"] += len(chunk)
        return chunk

    assert list(wire.read_files(read)) == files


def test_wire_truncated_stream_stops_cleanly():
    blob = wire.encode_file("a", b"12345")[:-2]  # chop the payload
    pos = {"i": 0}

    def read(n):
        chunk = blob[pos["i"]:pos["i"] + n]
        pos["i"] += len(chunk)
        return chunk

    assert list(wire.read_files(read)) == []  # no partial file yielded


# --------------------------------------------------------------------------- #
# project resolver
# --------------------------------------------------------------------------- #

class _FakeRegistry:
    def __init__(self, paths):
        self._paths = paths

    def get_registered_projects(self):
        return self._paths


def test_resolve_path_for_id(monkeypatch):
    monkeypatch.setattr(
        project_resolver, "resolve_project_identity",
        lambda p: type("I", (), {"project_id": "id-" + Path(p).name})(),
    )
    reg = _FakeRegistry(["/home/u/alpha", "/home/u/beta"])
    assert project_resolver.resolve_path_for_id("id-beta", reg) == "/home/u/beta"
    assert project_resolver.resolve_path_for_id("id-missing", reg) is None
    assert project_resolver.resolve_path_for_id("", reg) is None


# --------------------------------------------------------------------------- #
# broker round-trip (real server on loopback)
# --------------------------------------------------------------------------- #

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _deny(_id, _name):  # pair_prompt (unused — token pre-seeded)
    return False


def _wait_ready(port, token, pid, timeout=10.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            dispatch_api.fetch_manifest("127.0.0.1", port, token, pid)
            return
        except dispatch_api.DispatchError as exc:
            last = exc
            time.sleep(0.2)
    raise RuntimeError(f"broker not ready: {last}")


def test_broker_manifest_and_fetch_roundtrip(tmp_path, monkeypatch):
    # index writes under a temp config dir (broker thread shares module state)
    monkeypatch.setattr(file_index, "get_config_dir", lambda: tmp_path / "cfg")

    project = tmp_path / "proj"
    (project / "shared" / "sub").mkdir(parents=True)
    (project / "shared" / "a.txt").write_text("alpha", encoding="utf-8")
    (project / "shared" / "sub" / "b.bin").write_bytes(b"\x00\x01\x02")
    (project / "outside.txt").write_text("SECRET", encoding="utf-8")  # not shared

    paired = PairedDevices(path=tmp_path / "paired.json")
    token = paired.add("dev", "laptop")

    port = _free_port()
    broker = DispatchBroker(
        port, _deny, paired=paired,
        resolve_project=lambda pid: str(project) if pid == "PID" else None,
    )
    broker.start()
    try:
        _wait_ready(port, token, "PID")

        # manifest = the resolved shared set
        manifest = dispatch_api.fetch_manifest("127.0.0.1", port, token, "PID")
        assert set(manifest) == {"shared/a.txt", "shared/sub/b.bin"}

        # unauthorized (no token) is rejected
        with pytest.raises(dispatch_api.DispatchError):
            dispatch_api.fetch_manifest("127.0.0.1", port, "wrong-token", "PID")

        # unknown project id -> 404
        with pytest.raises(dispatch_api.DispatchError):
            dispatch_api.fetch_manifest("127.0.0.1", port, token, "NOPE")

        # fetch streams the requested files; the non-shared file is sandboxed out
        got = dict(dispatch_api.fetch_files(
            "127.0.0.1", port, token, "PID",
            ["shared/a.txt", "shared/sub/b.bin", "outside.txt", "../escape"],
        ))
        assert got == {"shared/a.txt": b"alpha", "shared/sub/b.bin": b"\x00\x01\x02"}
        assert "outside.txt" not in got   # never served
    finally:
        broker.stop()
