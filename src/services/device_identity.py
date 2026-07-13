"""Stable per-machine device identity for local dispatch.

The app has no machine-level identity anywhere else — sync keys everything by
per-project ``project_id`` and the tmux session name is path-derived. Local
dispatch pairing needs a stable id for *this machine* so a laptop can be added
to a desktop's allowlist and recognised on reconnect.

The id is a random ``uuid4`` persisted once to ``<config>/device.json`` and read
back forever after. The human-readable name defaults to the hostname but can be
overridden (stored alongside the id).
"""

from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from .config_path import get_config_dir


def _device_file() -> Path:
    return get_config_dir() / "device.json"


def _load() -> dict:
    try:
        return json.loads(_device_file().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _device_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2), mode=0o600)


def get_device_id() -> str:
    """Return this machine's stable device id, creating it on first call."""
    data = _load()
    device_id = data.get("device_id")
    if not device_id:
        device_id = uuid.uuid4().hex
        data["device_id"] = device_id
        _save(data)
    return device_id


def get_device_name() -> str:
    """Return the human-readable device name (custom, else the hostname)."""
    name = _load().get("device_name")
    if name:
        return str(name)
    try:
        return socket.gethostname() or "unknown"
    except OSError:
        return "unknown"


def set_device_name(name: str) -> None:
    """Override the human-readable device name."""
    data = _load()
    data["device_name"] = name.strip()
    _save(data)
