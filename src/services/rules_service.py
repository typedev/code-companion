"""Rules service for CLAUDE.md guidelines.

Rules are stored as individual .md files in ~/.config/claude-companion/rules/
- Filename (without .md) = rule name
- File content = rule text for copying into CLAUDE.md
"""

from pathlib import Path

import gi

gi.require_version("GObject", "2.0")

from gi.repository import GObject, Gio


# Default rules (filename: content)
DEFAULT_RULES = {
    "Language Policy": """## Language Policy

- All code comments and documentation in English
- Variable and function names in English
- Chat/communication can be in any language
""",
    "Linter Rules": """## Linter Rules

Before committing code, ensure:
1. Run `ruff check .` and fix all errors
2. Run `mypy src/` and fix type errors
3. Format code with `ruff format .`

Ignore rules can be configured in pyproject.toml or via inline comments.
""",
    "Planning": """## Planning

Before implementing new features:
1. Create detailed plan with checkpoints in `/docs` folder
2. Break down into small, testable steps
3. Update plan progress as you go
4. Commit after each checkpoint
""",
    "Git Workflow": """## Git Workflow

- Write clear commit messages describing "why" not "what"
- Keep commits atomic (one logical change per commit)
- Don't commit secrets, credentials, or .env files
- Run tests before pushing
""",
}


class RulesService(GObject.Object):
    """Singleton service for managing CLAUDE.md rules/guidelines.

    Rules are stored as .md files in ~/.config/claude-companion/rules/

    Usage:
        rules = RulesService.get_instance()

        # Get all rules (returns list of {name, content, path})
        all_rules = rules.get_all()

        # Add new rule
        rules.add("Rule Name", "Rule content...")

        # Delete rule
        rules.delete("Rule Name")

        # Listen for changes
        rules.connect("changed", on_rules_changed)
    """

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    _instance: "RulesService | None" = None

    def __init__(self):
        super().__init__()
        self.config_dir = Path.home() / ".config" / "claude-companion"
        self.rules_dir = self.config_dir / "rules"
        self._ensure_rules_dir()
        self._setup_file_monitor()

    @classmethod
    def get_instance(cls) -> "RulesService":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_rules_dir(self):
        """Ensure rules directory exists and has defaults if empty."""
        self.rules_dir.mkdir(parents=True, exist_ok=True)

        # Create default rules if directory is empty
        if not any(self.rules_dir.glob("*.md")):
            for name, content in DEFAULT_RULES.items():
                self._write_rule(name, content)

    def _setup_file_monitor(self):
        """Setup file monitor to watch for external changes."""
        gfile = Gio.File.new_for_path(str(self.rules_dir))
        self._monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", self._on_directory_changed)

    def _on_directory_changed(self, monitor, file, other_file, event_type):
        """Handle file system changes in rules directory."""
        if event_type in (
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        ):
            # Only emit for .md files
            if file.get_basename().endswith(".md"):
                self.emit("changed")

    def _write_rule(self, name: str, content: str):
        """Write a rule file."""
        file_path = self.rules_dir / f"{name}.md"
        try:
            file_path.write_text(content, encoding="utf-8")
        except OSError as e:
            print(f"Failed to write rule {name}: {e}")

    def get_all(self) -> list[dict]:
        """Get all rules.

        Returns list of dicts with keys: name, content, path
        """
        rules = []
        for file_path in sorted(self.rules_dir.glob("*.md")):
            try:
                content = file_path.read_text(encoding="utf-8")
                rules.append({
                    "name": file_path.stem,  # filename without .md
                    "content": content,
                    "path": str(file_path),
                })
            except OSError:
                continue
        return rules

    def get(self, name: str) -> dict | None:
        """Get a rule by name."""
        file_path = self.rules_dir / f"{name}.md"
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8")
                return {
                    "name": name,
                    "content": content,
                    "path": str(file_path),
                }
            except OSError:
                pass
        return None

    def add(self, name: str, content: str) -> str:
        """Add a new rule. Returns the file path."""
        self._write_rule(name, content)
        path = str(self.rules_dir / f"{name}.md")
        self.emit("changed")
        return path

    def delete(self, name: str) -> bool:
        """Delete a rule. Returns True if found."""
        file_path = self.rules_dir / f"{name}.md"
        if file_path.exists():
            try:
                file_path.unlink()
                self.emit("changed")
                return True
            except OSError:
                pass
        return False

    def rename(self, old_name: str, new_name: str) -> bool:
        """Rename a rule file."""
        old_path = self.rules_dir / f"{old_name}.md"
        new_path = self.rules_dir / f"{new_name}.md"
        if old_path.exists() and not new_path.exists():
            try:
                old_path.rename(new_path)
                self.emit("changed")
                return True
            except OSError:
                pass
        return False

    def get_rules_dir(self) -> Path:
        """Get the rules directory path."""
        return self.rules_dir

    def reset_to_defaults(self):
        """Reset rules to defaults (deletes all and recreates)."""
        # Delete all existing
        for file_path in self.rules_dir.glob("*.md"):
            try:
                file_path.unlink()
            except OSError:
                pass

        # Create defaults
        for name, content in DEFAULT_RULES.items():
            self._write_rule(name, content)

        self.emit("changed")
