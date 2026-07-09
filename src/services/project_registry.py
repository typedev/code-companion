"""Service for managing registered projects."""

import json
from datetime import datetime
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from .config_path import get_config_dir


class ProjectRegistry:
    """Manages the list of manually registered projects.

    On-disk format (v2)::

        {"registered_projects": [{"path": str, "name": str, "last_opened": str|null}]}

    ``last_opened`` is an ISO timestamp stamped by :meth:`mark_opened` each time a
    project window starts; the Project Manager sorts by it (most-recent first).
    Legacy formats (a plain ``list[str]`` of paths, or v2 entries without
    ``last_opened``) are migrated transparently on load. An empty ``name`` means
    "use the folder name" — callers resolve that fallback via :meth:`get_name`.
    """

    def __init__(self):
        self.config_dir = get_config_dir()
        self.config_file = self.config_dir / "projects.json"
        self._ensure_config_dir()

    def _ensure_config_dir(self):
        """Ensure config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        """Load registered projects, migrating legacy entries to v2 format.

        Returns a list of ``{"path": str, "name": str}`` dicts.
        """
        if not self.config_file.exists():
            return []

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

        raw = data.get("registered_projects", []) if isinstance(data, dict) else []
        return [entry for entry in (self._normalize_entry(e) for e in raw) if entry]

    @staticmethod
    def _normalize_entry(entry) -> dict | None:
        """Coerce a stored entry (legacy str or v2 dict) into a v2 dict."""
        if isinstance(entry, str):
            return {"path": entry, "name": "", "last_opened": None}
        if isinstance(entry, dict) and entry.get("path"):
            return {
                "path": entry["path"],
                "name": entry.get("name", "") or "",
                "last_opened": entry.get("last_opened") or None,
            }
        return None

    @staticmethod
    def last_opened_epoch(entry: dict) -> float:
        """A sortable open-time for an entry (0.0 when never opened / unparsable)."""
        value = entry.get("last_opened") if isinstance(entry, dict) else None
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value).timestamp()
        except (ValueError, TypeError):
            return 0.0

    def _save(self, projects: list[dict]):
        """Save registered projects to disk in v2 format (atomic)."""
        try:
            atomic_write_text(
                self.config_file,
                json.dumps({"registered_projects": projects}, indent=2),
            )
        except OSError:
            pass

    def get_projects(self) -> list[dict]:
        """Get registered projects as ``{"path", "name"}`` dicts."""
        return self._load()

    def get_registered_projects(self) -> list[str]:
        """Get list of registered project paths (back-compatible)."""
        return [entry["path"] for entry in self._load()]

    def register_project(self, path: str, name: str = ""):
        """Register a project path with an optional custom name.

        If the project is already registered, a non-empty ``name`` updates the
        existing label; an empty ``name`` leaves the existing label untouched.
        """
        projects = self._load()
        normalized = str(Path(path).resolve())

        for entry in projects:
            if entry["path"] == normalized:
                if name:
                    entry["name"] = name
                    self._save(projects)
                return

        projects.append({"path": normalized, "name": name or "", "last_opened": None})
        self._save(projects)

    def mark_opened(self, path: str):
        """Stamp a project as just-opened (creating its entry if needed).

        Called on every project-window start so the Project Manager can float the
        most-recently-opened project to the top. Distinct from
        :meth:`register_project` — clone/add register without counting as "opened".
        """
        projects = self._load()
        normalized = str(Path(path).resolve())
        stamp = datetime.now().isoformat(timespec="seconds")

        for entry in projects:
            if entry["path"] == normalized:
                entry["last_opened"] = stamp
                self._save(projects)
                return

        projects.append({"path": normalized, "name": "", "last_opened": stamp})
        self._save(projects)

    def unregister_project(self, path: str):
        """Unregister a project path."""
        projects = self._load()
        normalized = str(Path(path).resolve())

        remaining = [e for e in projects if e["path"] != normalized]
        if len(remaining) != len(projects):
            self._save(remaining)

    def is_registered(self, path: str) -> bool:
        """Check if a project is registered."""
        normalized = str(Path(path).resolve())
        return any(e["path"] == normalized for e in self._load())

    def get_name(self, path: str) -> str:
        """Get the display name for a project, falling back to the folder name."""
        normalized = str(Path(path).resolve())
        for entry in self._load():
            if entry["path"] == normalized:
                if entry["name"]:
                    return entry["name"]
                break
        return Path(normalized).name

    def set_name(self, path: str, name: str):
        """Set (or clear) the custom display name for a project.

        An empty ``name`` clears the custom label, reverting to the folder name.
        """
        projects = self._load()
        normalized = str(Path(path).resolve())

        for entry in projects:
            if entry["path"] == normalized:
                entry["name"] = name or ""
                self._save(projects)
                return
