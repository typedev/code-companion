"""Allowlist of devices paired for local dispatch (the client-authorization store).

A paired device holds a long-lived, per-device bearer token issued the first time
the user clicks "Allow" on the desktop. All broker access — the control API and
the PTY handshake — is gated on a token in this store. Revoking a device removes
its entry, immediately invalidating its token.

Stored at ``<config>/paired-devices.json`` with ``0600`` perms (tokens are
secrets). Format::

    {"devices": {"<device_id>": {"name": str, "token": str, "added_at": int}}}
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from .config_path import get_config_dir


class PairedDevices:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_config_dir() / "paired-devices.json")

    # ---- storage -----------------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text("utf-8"))
        except (OSError, ValueError):
            return {"devices": {}}
        if not isinstance(data, dict) or not isinstance(data.get("devices"), dict):
            return {"devices": {}}
        return data

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(data, indent=2), mode=0o600)

    # ---- queries -----------------------------------------------------------
    def list(self) -> list[dict]:
        """All paired devices as ``{device_id, name, added_at}`` (no tokens)."""
        out = []
        for device_id, rec in self._load()["devices"].items():
            out.append(
                {
                    "device_id": device_id,
                    "name": rec.get("name", ""),
                    "added_at": rec.get("added_at", 0),
                }
            )
        out.sort(key=lambda d: d.get("added_at", 0), reverse=True)
        return out

    def is_paired(self, device_id: str) -> bool:
        return device_id in self._load()["devices"]

    def token_for(self, device_id: str) -> str | None:
        rec = self._load()["devices"].get(device_id)
        return rec.get("token") if rec else None

    def device_for_token(self, token: str) -> dict | None:
        """Return ``{device_id, name}`` for a valid token, else None (constant-time)."""
        if not token:
            return None
        for device_id, rec in self._load()["devices"].items():
            stored = rec.get("token", "")
            if stored and hmac.compare_digest(str(stored), str(token)):
                return {"device_id": device_id, "name": rec.get("name", "")}
        return None

    # ---- mutations ---------------------------------------------------------
    def add(self, device_id: str, name: str) -> str:
        """Pair a device, issuing (or reusing) its token; returns the token."""
        data = self._load()
        rec = data["devices"].get(device_id)
        if rec and rec.get("token"):
            rec["name"] = name or rec.get("name", "")
            self._save(data)
            return rec["token"]
        token = secrets.token_urlsafe(32)
        data["devices"][device_id] = {
            "name": name,
            "token": token,
            "added_at": int(time.time()),
        }
        self._save(data)
        return token

    def revoke(self, device_id: str) -> bool:
        data = self._load()
        if device_id in data["devices"]:
            del data["devices"][device_id]
            self._save(data)
            return True
        return False
