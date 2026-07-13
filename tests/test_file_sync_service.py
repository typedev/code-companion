"""Phase 3 tests: file-sync orchestration (Get mirror, preview, Give trigger)."""

import socket
import time

import pytest

from src.services import dispatch_api, file_sync_service as svc
from src.services.dispatch_broker import DispatchBroker
from src.services.file_sync import file_index
from src.services.paired_devices import PairedDevices
from src.services.remote_tokens import RemoteTokens


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _deny(_id, _name):
    return False


def _wait_ready(port, token, pid, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            dispatch_api.fetch_manifest("127.0.0.1", port, token, pid)
            return
        except dispatch_api.DispatchError:
            time.sleep(0.2)
    raise RuntimeError("broker not ready")


def _serve(tmp_path, source_path, monkeypatch, *, remote_tokens=None, resolve=None):
    """Start a broker serving ``source_path`` as project id 'PID'. Returns (broker, port, token)."""
    monkeypatch.setattr(file_index, "get_config_dir", lambda: tmp_path / "cfg")
    paired = PairedDevices(path=tmp_path / "paired.json")
    token = paired.add("dev", "laptop")
    port = _free_port()
    broker = DispatchBroker(
        port, _deny, paired=paired,
        remote_tokens=remote_tokens,
        resolve_project=resolve or (lambda pid: str(source_path) if pid == "PID" else None),
    )
    broker.start()
    _wait_ready(port, token, "PID")
    return broker, port, token


def _peer(port, token):
    return svc.Peer("dev", "desktop", "127.0.0.1", port, token)


def test_run_get_mirrors_peer_and_trashes_destroyed(tmp_path, monkeypatch):
    # Source (peer) shared set
    source = tmp_path / "A"
    (source / "shared").mkdir(parents=True)
    (source / "shared" / "a.txt").write_text("alpha", encoding="utf-8")
    (source / "shared" / "b.txt").write_text("bee", encoding="utf-8")

    broker, port, token = _serve(tmp_path, source, monkeypatch)
    try:
        # Destination diverges: a.txt differs, gone.txt is local-only, b.txt missing.
        dest = tmp_path / "B"
        (dest / "shared").mkdir(parents=True)
        (dest / "shared" / "a.txt").write_text("OLD-alpha", encoding="utf-8")
        (dest / "shared" / "gone.txt").write_text("local-only", encoding="utf-8")

        result = svc.run_get(str(dest), "PID", _peer(port, token), stamp="S")

        # dest now mirrors the peer's shared set
        assert (dest / "shared" / "a.txt").read_text() == "alpha"   # overwritten
        assert (dest / "shared" / "b.txt").read_text() == "bee"     # pulled
        assert not (dest / "shared" / "gone.txt").exists()          # removed
        # destroyed local versions recoverable from .deleted/
        assert (dest / ".deleted" / "S" / "shared" / "a.txt").read_text() == "OLD-alpha"
        assert (dest / ".deleted" / "S" / "shared" / "gone.txt").read_text() == "local-only"
        assert result.fetched == 2 and result.removed == 1 and result.overwritten == 1
    finally:
        broker.stop()


def test_build_preview_counts(tmp_path, monkeypatch):
    source = tmp_path / "A"
    (source / "shared").mkdir(parents=True)
    (source / "shared" / "a.txt").write_text("alpha", encoding="utf-8")
    (source / "shared" / "new.txt").write_text("new", encoding="utf-8")

    broker, port, token = _serve(tmp_path, source, monkeypatch)
    try:
        dest = tmp_path / "B"
        (dest / "shared").mkdir(parents=True)
        (dest / "shared" / "a.txt").write_text("OLD", encoding="utf-8")   # changed
        (dest / "shared" / "only.txt").write_text("mine", encoding="utf-8")  # only local

        p = svc.build_preview(str(dest), "PID", _peer(port, token))
        assert p.diff.only_remote == {"shared/new.txt"}
        assert p.diff.changed == {"shared/a.txt"}
        assert p.diff.only_local == {"shared/only.txt"}
        assert p.get.destructive_count == 2  # a.txt overwritten + only.txt removed
        # Give (local -> peer): would send a.txt + only.txt, remove new.txt on peer
        assert p.give.fetch == {"shared/a.txt", "shared/only.txt"}
        assert p.give.remove == {"shared/new.txt"}
    finally:
        broker.stop()


def test_pull_request_rejects_unpaired_requester(tmp_path, monkeypatch):
    dest = tmp_path / "B"
    (dest / "shared").mkdir(parents=True)
    # broker with an EMPTY remote-token store -> can't pull back
    broker, port, token = _serve(
        tmp_path, dest, monkeypatch,
        remote_tokens=RemoteTokens(path=tmp_path / "empty-tokens.json"),
    )
    try:
        with pytest.raises(dispatch_api.DispatchError):
            dispatch_api.request_pull("127.0.0.1", port, token, "PID", "requester-id", 40000)
    finally:
        broker.stop()


def test_mutual_pairing_stores_callback_token(tmp_path, monkeypatch):
    monkeypatch.setattr(file_index, "get_config_dir", lambda: tmp_path / "cfg")

    async def _allow(_id, _name):
        return True

    paired = PairedDevices(path=tmp_path / "paired.json")
    rt = RemoteTokens(path=tmp_path / "rt.json")
    port = _free_port()
    broker = DispatchBroker(
        port, _allow, paired=paired, remote_tokens=rt,
        resolve_project=lambda pid: None,
    )
    broker.start()
    try:
        token = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                token = dispatch_api.pair(
                    "127.0.0.1", port, "laptop-id", "laptop", callback_token="CB-TOKEN"
                )
                break
            except dispatch_api.DispatchError:
                time.sleep(0.2)
        assert token  # peer issued us a token to call it
        # ...and it stored our callback token so it can call us back (mutual)
        assert rt.token_for("laptop-id") == "CB-TOKEN"
    finally:
        broker.stop()


def test_pull_request_accepts_when_paired(tmp_path, monkeypatch):
    dest = tmp_path / "B"
    (dest / "shared").mkdir(parents=True)
    rt = RemoteTokens(path=tmp_path / "tokens.json")
    rt.set("requester-id", "the-requester", "tok-abc")  # we hold a token for them
    broker, port, token = _serve(tmp_path, dest, monkeypatch, remote_tokens=rt)
    try:
        # Accepted (background pull-back to a dead port fails harmlessly).
        resp = dispatch_api.request_pull("127.0.0.1", port, token, "PID", "requester-id", 40000)
        assert resp.get("status") == "started"
    finally:
        broker.stop()
