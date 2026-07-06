"""Per-machine hash base manifest — the merge base for 3-way sync.

Records, per project_id, the sha256 of every file this machine last agreed on
with the sync repo. On the next sync a local file counts as "dirty" iff its
current hash differs from this base; that is what makes concurrent edits to
*different* projects on two machines safe (a project this machine did not touch
is never re-exported and cannot clobber the other machine's version).

Hashes only — never mtime (clocks are not comparable across machines).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .config_path import get_config_dir


class SyncStateStore:
    """JSON-backed store of per-project last-synced file hashes."""

    def __init__(self, path: Path | None = None):
        self.path = path or (get_config_dir() / "sync_state.json")
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("projects"), dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"projects": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # atomic

    def get_base(self, project_id: str) -> dict[str, str]:
        """Return {rel_path: sha256} last synced for this project ({} if first contact)."""
        entry = self._data.get("projects", {}).get(project_id, {})
        return dict(entry.get("files", {}))

    def set_base(self, project_id: str, files: dict[str, str]) -> None:
        """Record the new agreed base (rel_path -> sha256) for this project."""
        self._data.setdefault("projects", {})[project_id] = {
            "files": dict(files),
            "last_synced": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def clear(self, project_id: str) -> None:
        """Forget a project's base (forces first-contact adoption next sync)."""
        if self._data.get("projects", {}).pop(project_id, None) is not None:
            self._save()
