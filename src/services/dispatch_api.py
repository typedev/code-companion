"""Client-side HTTP calls to a remote dispatch broker (stdlib urllib only).

Used by the laptop PM to pair with a discovered desktop and list its sessions.
Kept dependency-free and GTK-agnostic so it can run on a worker thread and be
tested headless.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

_TIMEOUT = 8


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


def pair(host: str, port: int, device_id: str, device_name: str) -> str:
    """Pair with a broker; return the issued token (raises on denial/error).

    Blocks until the desktop user clicks Allow/Deny — call off the main thread.
    """
    resp = _post(f"http://{host}:{port}/pair", {
        "device_id": device_id,
        "device_name": device_name,
    })
    token = resp.get("token")
    if not token:
        raise DispatchError("no token returned")
    return token


def list_sessions(host: str, port: int, token: str) -> dict:
    """Return ``{"pty_port": int, "sessions": [...]}`` for a paired broker."""
    return _get(f"http://{host}:{port}/sessions", token=token)
