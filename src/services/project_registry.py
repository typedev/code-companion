"""Service for managing registered projects."""

import json
from pathlib import Path


class ProjectRegistry:
    """Manages the list of manually registered projects."""

    def __init__(self):
        self.config_dir = Path.home() / ".config" / "claude-companion"
        self.config_file = self.config_dir / "projects.json"
        self._ensure_config_dir()

    def _ensure_config_dir(self):
        """Ensure config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        """Load registry from disk."""
        if not self.config_file.exists():
            return {"registered_projects": []}

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"registered_projects": []}

    def _save(self, data: dict):
        """Save registry to disk."""
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_registered_projects(self) -> list[str]:
        """Get list of registered project paths."""
        data = self._load()
        return data.get("registered_projects", [])

    def register_project(self, path: str):
        """Register a project path."""
        data = self._load()
        projects = data.get("registered_projects", [])

        # Normalize path
        normalized = str(Path(path).resolve())

        if normalized not in projects:
            projects.append(normalized)
            data["registered_projects"] = projects
            self._save(data)

    def unregister_project(self, path: str):
        """Unregister a project path."""
        data = self._load()
        projects = data.get("registered_projects", [])

        # Normalize path
        normalized = str(Path(path).resolve())

        if normalized in projects:
            projects.remove(normalized)
            data["registered_projects"] = projects
            self._save(data)

    def is_registered(self, path: str) -> bool:
        """Check if a project is registered."""
        normalized = str(Path(path).resolve())
        return normalized in self.get_registered_projects()
