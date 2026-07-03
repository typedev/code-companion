"""Service for managing registered projects."""

import json
from pathlib import Path

from .config_path import get_config_dir


class ProjectRegistry:
    """Manages the list of manually registered projects.

    On-disk format (v2)::

        {"registered_projects": [{"path": str, "name": str}]}

    Legacy format (a plain ``list[str]`` of paths) is migrated transparently on
    load. An empty ``name`` means "use the folder name" — callers resolve that
    fallback via :meth:`get_name`.
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
            return {"path": entry, "name": ""}
        if isinstance(entry, dict) and entry.get("path"):
            return {"path": entry["path"], "name": entry.get("name", "") or ""}
        return None

    def _save(self, projects: list[dict]):
        """Save registered projects to disk in v2 format."""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump({"registered_projects": projects}, f, indent=2)
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

        projects.append({"path": normalized, "name": name or ""})
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
