"""Snippets service for quick text templates.

Snippets are stored as individual .md files in ~/.config/code-companion/snippets/
- Filename (without .md) = label (spaces allowed)
- File content = text to insert
"""

from pathlib import Path

import gi

gi.require_version("GObject", "2.0")

from gi.repository import GObject, Gio

from .config_path import get_config_dir


# Default snippets (filename: content)
DEFAULT_SNIPPETS = {
    "Plan": "создай детальный план с чекпоинтами в docs/",
    "Commit": "сделай саммари изменений и коммит",
    "Fix": "исправь ошибку",
    "Summary": "сделай краткое саммари что было сделано",
}


class SnippetsService(GObject.Object):
    """Singleton service for managing text snippets.

    Snippets are stored as .md files in ~/.config/code-companion/snippets/

    Usage:
        snippets = SnippetsService.get_instance()

        # Get all snippets (returns list of {label, text, path})
        all_snippets = snippets.get_all()

        # Add new snippet
        snippets.add("Label", "Text to insert")

        # Delete snippet
        snippets.delete("Label")

        # Listen for changes
        snippets.connect("changed", on_snippets_changed)
    """

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    _instance: "SnippetsService | None" = None

    def __init__(self):
        super().__init__()
        self.config_dir = get_config_dir()
        self.snippets_dir = self.config_dir / "snippets"
        self._ensure_snippets_dir()
        self._setup_file_monitor()

    @classmethod
    def get_instance(cls) -> "SnippetsService":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_snippets_dir(self):
        """Ensure snippets directory exists and has defaults if empty."""
        self.snippets_dir.mkdir(parents=True, exist_ok=True)

        # Create default snippets if directory is empty
        if not any(self.snippets_dir.glob("*.md")):
            for label, text in DEFAULT_SNIPPETS.items():
                self._write_snippet(label, text)

    def _setup_file_monitor(self):
        """Setup file monitor to watch for external changes."""
        gfile = Gio.File.new_for_path(str(self.snippets_dir))
        self._monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", self._on_directory_changed)

    def _on_directory_changed(self, monitor, file, other_file, event_type):
        """Handle file system changes in snippets directory."""
        if event_type in (
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        ):
            # Only emit for .md files
            if file.get_basename().endswith(".md"):
                self.emit("changed")

    def _write_snippet(self, label: str, text: str):
        """Write a snippet file."""
        file_path = self.snippets_dir / f"{label}.md"
        try:
            file_path.write_text(text, encoding="utf-8")
        except OSError as e:
            print(f"Failed to write snippet {label}: {e}")

    def get_all(self) -> list[dict]:
        """Get all snippets.

        Returns list of dicts with keys: label, text, path
        """
        snippets = []
        for file_path in sorted(self.snippets_dir.glob("*.md")):
            try:
                text = file_path.read_text(encoding="utf-8").strip()
                snippets.append({
                    "label": file_path.stem,  # filename without .md
                    "text": text,
                    "path": str(file_path),
                })
            except OSError:
                continue
        return snippets

    def get(self, label: str) -> dict | None:
        """Get a snippet by label."""
        file_path = self.snippets_dir / f"{label}.md"
        if file_path.exists():
            try:
                text = file_path.read_text(encoding="utf-8").strip()
                return {
                    "label": label,
                    "text": text,
                    "path": str(file_path),
                }
            except OSError:
                pass
        return None

    def add(self, label: str, text: str) -> str:
        """Add a new snippet. Returns the file path."""
        self._write_snippet(label, text)
        path = str(self.snippets_dir / f"{label}.md")
        self.emit("changed")
        return path

    def delete(self, label: str) -> bool:
        """Delete a snippet. Returns True if found."""
        file_path = self.snippets_dir / f"{label}.md"
        if file_path.exists():
            try:
                file_path.unlink()
                self.emit("changed")
                return True
            except OSError:
                pass
        return False

    def rename(self, old_label: str, new_label: str) -> bool:
        """Rename a snippet file."""
        old_path = self.snippets_dir / f"{old_label}.md"
        new_path = self.snippets_dir / f"{new_label}.md"
        if old_path.exists() and not new_path.exists():
            try:
                old_path.rename(new_path)
                self.emit("changed")
                return True
            except OSError:
                pass
        return False

    def get_snippets_dir(self) -> Path:
        """Get the snippets directory path."""
        return self.snippets_dir

    def reset_to_defaults(self):
        """Reset snippets to defaults (deletes all and recreates)."""
        # Delete all existing
        for file_path in self.snippets_dir.glob("*.md"):
            try:
                file_path.unlink()
            except OSError:
                pass

        # Create defaults
        for label, text in DEFAULT_SNIPPETS.items():
            self._write_snippet(label, text)

        self.emit("changed")
