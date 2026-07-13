"""Per-project persistent hash index for building file-sync manifests fast.

Maps ``rel -> {mtime_ns, size, sha256}`` for the shared set, re-hashing a file
only when its ``(mtime_ns, size)`` changed since the last build (git-index-style).
The index is machine-local and never synced. Keyed by the encoded absolute
project path under the app config dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ...utils import encode_project_path
from ...utils.atomic_write import atomic_write_text
from ...utils.text_files import capture_stat
from ..config_path import get_config_dir
from ..sync_engine import hash_file
from .share_spec import resolve_shared_files

_SCHEMA_VERSION = 1


def _index_dir() -> Path:
    return get_config_dir() / "file-sync-index"


def _index_path(root: Path) -> Path:
    key = encode_project_path(str(root.resolve()))
    return _index_dir() / f"{key}.json"


def _load(root: Path) -> dict:
    path = _index_path(root)
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            # Discard a stale schema so a hashing/logic change invalidates the cache.
            if isinstance(loaded, dict) and loaded.get("_schema") == _SCHEMA_VERSION:
                entries = loaded.get("entries")
                if isinstance(entries, dict):
                    return entries
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save(root: Path, entries: dict) -> None:
    path = _index_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path, json.dumps({"_schema": _SCHEMA_VERSION, "entries": entries}, indent=2)
        )
    except OSError:
        pass


def build_manifest(root: str | os.PathLike) -> dict[str, str]:
    """Return ``{rel: sha256}`` for the project's shared set, using the stat cache.

    Only files whose ``(mtime_ns, size)`` changed since the last build are
    re-hashed; everything else reuses the cached sha256. The refreshed cache is
    persisted so the next build is cheap even after the process restarts.
    """
    root = Path(root)
    shared = resolve_shared_files(root)
    cache = _load(root)

    manifest: dict[str, str] = {}
    new_cache: dict[str, dict] = {}
    for rel in shared:
        abs_path = root / rel
        st = capture_stat(abs_path)
        if st is None:
            continue  # vanished between the walk and the stat
        mtime_ns, size = st
        prev = cache.get(rel)
        if (
            isinstance(prev, dict)
            and prev.get("mtime_ns") == mtime_ns
            and prev.get("size") == size
            and isinstance(prev.get("sha256"), str)
        ):
            sha = prev["sha256"]
        else:
            try:
                sha = hash_file(abs_path)
            except OSError:
                continue
        manifest[rel] = sha
        new_cache[rel] = {"mtime_ns": mtime_ns, "size": size, "sha256": sha}

    _save(root, new_cache)
    return manifest
