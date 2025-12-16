"""Configuration path utilities with migration support.

Handles migration from claude-companion to code-companion config directories.
"""

from pathlib import Path


def get_config_dir() -> Path:
    """Get the configuration directory, handling migration from old name.

    If old ~/.config/claude-companion exists and new ~/.config/code-companion
    doesn't, returns old path for backward compatibility.
    Otherwise returns new path.
    """
    old_dir = Path.home() / ".config" / "claude-companion"
    new_dir = Path.home() / ".config" / "code-companion"

    # If old exists and new doesn't, use old (backward compatible)
    if old_dir.exists() and not new_dir.exists():
        return old_dir

    # Use new directory
    return new_dir


def migrate_config_if_needed() -> bool:
    """Migrate config from old to new directory if needed.

    Returns True if migration was performed.
    """
    old_dir = Path.home() / ".config" / "claude-companion"
    new_dir = Path.home() / ".config" / "code-companion"

    # Only migrate if old exists and new doesn't
    if old_dir.exists() and not new_dir.exists():
        try:
            old_dir.rename(new_dir)
            return True
        except OSError:
            # If rename fails (e.g., cross-device), copy instead
            import shutil
            try:
                shutil.copytree(old_dir, new_dir)
                return True
            except OSError:
                pass

    return False
