"""Settings service for application-wide configuration."""

import json
from pathlib import Path
from typing import Any

import gi

gi.require_version("GObject", "2.0")

from gi.repository import GObject


# Default settings
DEFAULT_SETTINGS = {
    "appearance": {
        "theme": "system",  # system, light, dark
        "syntax_scheme": "Adwaita-dark",
    },
    "editor": {
        "font_family": "Monospace",
        "font_size": 12,
        "line_height": 1.4,
        "tab_size": 4,
        "insert_spaces": True,
    },
    "file_tree": {
        "show_hidden": False,
    },
    "window": {
        "width": 1200,
        "height": 800,
        "x": None,
        "y": None,
        "maximized": False,
    },
}


class SettingsService(GObject.Object):
    """Singleton service for application settings.

    Usage:
        # Get instance
        settings = SettingsService.get_instance()

        # Get setting
        theme = settings.get("appearance.theme")
        font_size = settings.get("editor.font_size")

        # Set setting (auto-saves)
        settings.set("appearance.theme", "dark")

        # Connect to changes
        settings.connect("changed::appearance.theme", on_theme_changed)
        settings.connect("changed", on_any_setting_changed)
    """

    __gsignals__ = {
        # Emitted when any setting changes: callback(service, key, value)
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    _instance: "SettingsService | None" = None

    def __init__(self):
        super().__init__()
        self.config_dir = Path.home() / ".config" / "claude-companion"
        self.config_file = self.config_dir / "settings.json"
        self._settings: dict = {}
        self._ensure_config_dir()
        self._load()

    @classmethod
    def get_instance(cls) -> "SettingsService":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_config_dir(self):
        """Ensure config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load(self):
        """Load settings from disk, merging with defaults."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
            except (json.JSONDecodeError, OSError):
                saved = {}
        else:
            saved = {}

        # Deep merge with defaults
        self._settings = self._deep_merge(DEFAULT_SETTINGS, saved)

    def _save(self):
        """Save settings to disk."""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2)
        except OSError as e:
            print(f"Failed to save settings: {e}")

    def _deep_merge(self, defaults: dict, overrides: dict) -> dict:
        """Deep merge overrides into defaults."""
        result = defaults.copy()
        for key, value in overrides.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting by dot-notation key.

        Args:
            key: Setting key like "appearance.theme" or "editor.font_size"
            default: Default value if key not found

        Returns:
            The setting value or default
        """
        parts = key.split(".")
        value = self._settings

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default

        return value

    def set(self, key: str, value: Any) -> None:
        """Set a setting by dot-notation key.

        Automatically saves to disk and emits 'changed' signal.

        Args:
            key: Setting key like "appearance.theme"
            value: New value to set
        """
        parts = key.split(".")
        target = self._settings

        # Navigate to parent
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]

        # Set value
        old_value = target.get(parts[-1])
        if old_value != value:
            target[parts[-1]] = value
            self._save()
            self.emit("changed", key, value)

    def get_all(self) -> dict:
        """Get all settings as a dict."""
        return self._settings.copy()

    def reset(self, key: str | None = None) -> None:
        """Reset setting(s) to default.

        Args:
            key: Specific key to reset, or None to reset all
        """
        if key is None:
            self._settings = DEFAULT_SETTINGS.copy()
            self._save()
            self.emit("changed", "*", None)
        else:
            # Get default value
            parts = key.split(".")
            default_value = DEFAULT_SETTINGS
            for part in parts:
                if isinstance(default_value, dict) and part in default_value:
                    default_value = default_value[part]
                else:
                    return  # Key not in defaults

            self.set(key, default_value)
