"""Client-side HTTP calls to a remote dispatch broker (stdlib urllib only).

Used by the laptop PM to pair with a discovered desktop and list its sessions.
Kept dependency-free and GTK-agnostic so it can run on a worker thread and be
tested headless.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

from .file_sync import wire

_TIMEOUT = 8
_FETCH_TIMEOUT = 60  # per-read socket timeout during a (possibly large) file stream


class DispatchError(Exception):
    """A broker call failed (network, HTTP status, or denial)."""


def _post(url: str, body: dict, token: str | None = None) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return _send(req)


def _get(url: str, token: str | None = None) -> dict:
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return _send(req)


def _send(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        raise DispatchError(payload.get("error") or f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DispatchError(str(exc)) from exc


def pair(
    host: str, port: int, device_id: str, device_name: str, callback_token: str | None = None
) -> str:
    """Pair with a broker; return the issued token (raises on denial/error).

    Blocks until the desktop user clicks Allow/Deny — call off the main thread.
    ``callback_token`` (optional) is a token WE issued for the peer so, on Allow,
    the peer can call us back — this makes pairing mutual (bidirectional).
    """
    body = {"device_id": device_id, "device_name": device_name}
    if callback_token:
        body["callback_token"] = callback_token
    resp = _post(f"http://{host}:{port}/pair", body)
    token = resp.get("token")
    if not token:
        raise DispatchError("no token returned")
    return token


def pair_mutual(
    host: str, port: int, my_device_id: str, my_name: str,
    peer_device_id: str, peer_name: str, paired, remote_tokens,
) -> str:
    """Pair mutually: one Allow on the peer leaves BOTH machines able to call the
    other. We pre-issue the peer a token (registered in our own ``paired`` allow-
    list) and send it as ``callback_token``; the peer's issued token is stored in
    our ``remote_tokens``. Returns the token we use to call the peer.
    """
    callback = paired.add(peer_device_id, peer_name)
    token = pair(host, port, my_device_id, my_name, callback_token=callback)
    remote_tokens.set(peer_device_id, peer_name, token)
    return token


def list_sessions(host: str, port: int, token: str) -> dict:
    """Return ``{"pty_port": int, "sessions": [...]}`` for a paired broker."""
    return _get(f"http://{host}:{port}/sessions", token=token)


# --- read-only panel data (computed by the broker from the session's path) --
# ``port`` here is the broker's HTTP port (not the PTY port).

def _panel(host: str, port: int, token: str, session: str, tool: str, **params) -> dict:
    import urllib.parse
    qs = f"?{urllib.parse.urlencode(params)}" if params else ""
    return _get(f"http://{host}:{port}/{session}/panel/{tool}{qs}", token=token)


def get_changes(host, port, token, session) -> dict:
    return _panel(host, port, token, session, "list_changes")


def get_file_diff(host, port, token, session, path: str, staged: bool = False) -> dict:
    return _panel(host, port, token, session, "get_file_diff",
                  path=path, staged=str(staged).lower())


def list_files(host, port, token, session) -> dict:
    return _panel(host, port, token, session, "list_files")


def read_file(host, port, token, session, path: str, max_bytes: int = 1_000_000) -> dict:
    return _panel(host, port, token, session, "read_file", path=path, max_bytes=max_bytes)


def get_problems(host, port, token, session) -> dict:
    return _panel(host, port, token, session, "get_problems")


# --- file-sync (project-addressed) ------------------------------------------

def fetch_manifest(host, port, token, project_id: str) -> dict[str, str]:
    """Return the peer's ``{rel: sha256}`` manifest for a project's shared set."""
    resp = _post(
        f"http://{host}:{port}/filesync/manifest",
        {"project_id": project_id},
        token=token,
    )
    manifest = resp.get("manifest", {})
    return manifest if isinstance(manifest, dict) else {}


def fetch_files(
    host, port, token, project_id: str, rels
) -> Iterator[tuple[str, bytes]]:
    """Stream ``(rel, bytes)`` for each requested file from the peer.

    Yields incrementally so a large seed never buffers wholly in memory. The peer
    only serves rels inside its resolved shared set (others are silently dropped).
    """
    data = json.dumps({"project_id": project_id, "rels": list(rels)}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/filesync/fetch", data=data, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        raise DispatchError(payload.get("error") or f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DispatchError(str(exc)) from exc
    with resp:
        yield from wire.read_files(resp.read)
