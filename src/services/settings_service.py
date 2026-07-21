"""Settings service for application-wide configuration."""

import copy
import json
from pathlib import Path
from typing import Any, Iterator

import gi

gi.require_version("GObject", "2.0")

from gi.repository import GObject

from .config_path import get_config_dir
from ..utils.atomic_write import atomic_write_text

# Cross-machine sync allowlist: preference groups that mean the same thing on
# every machine. Everything else is per-machine and must NEVER sync — window
# geometry, sync.* itself (feedback loop / machine identity), dispatch pairing,
# hardware-specific input tuning.
SYNC_GROUPS = ("appearance", "editor", "git", "mcp", "ai", "sessions", "linters")
SYNC_EXTRA_KEYS = ("terminal.auto_activate_env",)


def _iter_leaves(node: dict, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Yield (dot.key, value) for every leaf of a nested settings dict."""
    for key, value in node.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _iter_leaves(value, dotted + ".")
        else:
            yield dotted, value


def syncable_slice(settings: dict) -> dict:
    """The allowlisted, machine-independent slice of a settings dict."""
    out: dict = {}
    for group in SYNC_GROUPS:
        value = settings.get(group)
        if isinstance(value, dict) and value:
            out[group] = value
    for dotted in SYNC_EXTRA_KEYS:
        group, leaf = dotted.split(".", 1)
        value = settings.get(group)
        if isinstance(value, dict) and leaf in value:
            out.setdefault(group, {})[leaf] = value[leaf]
    return out


def export_syncable_bytes(settings_file: Path) -> bytes:
    """Deterministic bytes of the syncable slice of a settings.json file.

    Reads the FILE (stored overrides only — untouched defaults have nothing to
    say cross-machine), so it is safe on sync worker threads: no singleton, no
    GObject signals. Deterministic (sorted keys) so content hashes are stable.
    """
    try:
        stored = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return json.dumps(syncable_slice(stored), indent=2, sort_keys=True).encode()


def merge_syncable_into_file(settings_file: Path, incoming: bytes) -> None:
    """Merge an incoming synced slice into settings.json (atomic, file-level).

    Only allowlisted keys are taken from ``incoming``; every per-machine key in
    the local file survives untouched. No signals — callers on the main thread
    follow up with ``SettingsService.reload_from_disk()`` for live apply.
    """
    try:
        data = json.loads(incoming.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    try:
        current = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    for group, value in syncable_slice(data).items():
        if isinstance(current.get(group), dict) and isinstance(value, dict):
            current[group] = {**current[group], **value}
        else:
            current[group] = value
    atomic_write_text(settings_file, json.dumps(current, indent=2))


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
    "terminal": {
        "auto_activate_env": True,
        # Touchpad damping. VTE feeds the app one wheel click per *pixel* of finger
        # travel (it ignores GdkScrollUnit — vte#2720), so a flick sends ~1500
        # clicks where a mouse sends 5. We bank the pixels and emit one step per
        # this many. 1 = VTE's raw behaviour. No divisor suits every touchpad,
        # which is exactly why upstream is stuck and why this is a setting.
        "touchpad_pixels_per_click": 25,
    },
    "window": {
        "width": 1200,
        "height": 800,
        "x": None,
        "y": None,
        "maximized": False,
        # Bottom Claude pane / tabs split
        "workspace_split_position": 260,  # height of the tabs area above the Claude pane
        "workspace_collapsed": False,  # tabs area collapsed to the tab bar
    },
    "sync": {
        "enabled": False,
        "repo_url": "https://github.com/typedev/code-companion-sync",
        "last_good_commit": "",
        # Fetch the backup automatically when the PM opens (silent pull-only,
        # never pushes). Every push is manual via the Sync button — there is no
        # periodic background sync.
        "pull_on_start": True,
        # ("mode" retired: the registry now exports on every sync; a stored
        # legacy value is simply ignored.)
        # Whitelist of ~/.claude.json project fields to sync. "hasTrustDialogAccepted"
        # is intentionally excluded (syncing it would auto-trust on the other machine).
        "claude_json_fields": [
            "allowedTools",
            "mcpServers",
            "enabledMcpjsonServers",
        ],
    },
    "mcp": {
        # Per-window MCP control surface for the embedded AI session (open files,
        # read workspace state, notify). When off, the CLI launches bare.
        "enabled": True,
    },
    "git": {
        # Default branch name for `git init` on New Project (git init -b <name>).
        "default_branch": "main",
    },
    "dispatch": {
        # Local dispatch: let another machine on the LAN attach to this machine's
        # live Claude sessions. When off, the broker never starts and nothing is
        # advertised. Only the PM that holds the ManagerLock runs the broker.
        "enabled": False,
        "port": 47100,  # broker TCP port (control API + PTY bridge + MCP proxy)
        "advertise": True,  # publish presence via zeroconf while enabled
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
        self.config_dir = get_config_dir()
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
        """Deep merge overrides into defaults.

        The result must NOT share nested dicts with ``defaults``: ``set()``
        mutates the merged tree in place, and a shared reference would corrupt
        module-level DEFAULT_SETTINGS for every later instance (and ``reset()``).
        """
        result = copy.deepcopy(defaults)
        for key, value in overrides.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
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

    def refresh_from_disk_silently(self) -> list[tuple[str, Any]]:
        """Re-read settings.json into memory WITHOUT emitting signals.

        For sync worker threads: after the file-level merge of synced keys the
        in-memory copy must catch up immediately (any later ``set()`` in the
        same process would otherwise re-save the stale memory over the merge).
        Returns the changed ``(dot.key, new_value)`` leaves so the caller can
        emit ``changed`` for them later ON THE MAIN THREAD (live apply).
        """
        before = self._settings
        self._load()
        changed: list[tuple[str, Any]] = []
        for key, new_value in _iter_leaves(self._settings):
            old_value = before
            for part in key.split("."):
                old_value = old_value.get(part) if isinstance(old_value, dict) else None
                if old_value is None:
                    break
            if old_value != new_value:
                changed.append((key, new_value))
        return changed

    def reset(self, key: str | None = None) -> None:
        """Reset setting(s) to default.

        Args:
            key: Specific key to reset, or None to reset all
        """
        if key is None:
            self._settings = copy.deepcopy(DEFAULT_SETTINGS)
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
