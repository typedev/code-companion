"""Tokens this machine holds *as a client* for remote dispatch peers.

The mirror image of ``paired_devices`` (which is the server-side allowlist): when
this machine pairs with a desktop, the desktop issues a per-device token that we
store here, keyed by the remote's ``device_id``, so later connects skip pairing.

Stored at ``<config>/dispatch-tokens.json`` with ``0600`` perms.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from .config_path import get_config_dir


class RemoteTokens:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_config_dir() / "dispatch-tokens.json")

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text("utf-8"))
        except (OSError, ValueError):
            return {"peers": {}}
        if not isinstance(data, dict) or not isinstance(data.get("peers"), dict):
            return {"peers": {}}
        return data

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(data, indent=2), mode=0o600)

    def list(self) -> list[dict]:
        """All remote peers this machine has paired with (no tokens)."""
        out = [
            {"device_id": did, "name": rec.get("name", ""), "paired_at": rec.get("paired_at", 0)}
            for did, rec in self._load()["peers"].items()
        ]
        out.sort(key=lambda d: d.get("paired_at", 0), reverse=True)
        return out

    def token_for(self, device_id: str) -> str | None:
        rec = self._load()["peers"].get(device_id)
        return rec.get("token") if rec else None

    def set(self, device_id: str, name: str, token: str) -> None:
        data = self._load()
        data["peers"][device_id] = {
            "name": name,
            "token": token,
            "paired_at": int(time.time()),
        }
        self._save(data)

    def forget(self, device_id: str) -> bool:
        data = self._load()
        if device_id in data["peers"]:
            del data["peers"][device_id]
            self._save(data)
            return True
        return False
