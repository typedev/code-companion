"""Version management for Claude Companion."""

import subprocess
from pathlib import Path

# Base version (update manually at milestones)
__version_base__ = "0.7"


def get_version() -> str:
    """Get full version string: base.commit_count (e.g., 0.7.22)."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
            timeout=5,
        )
        if result.returncode == 0:
            commit_count = result.stdout.strip()
            return f"{__version_base__}.{commit_count}"
    except Exception:
        pass

    return f"{__version_base__}.0"


def get_version_info() -> dict:
    """Get detailed version info."""
    try:
        # Short commit hash
        hash_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
            timeout=5,
        )
        commit_hash = hash_result.stdout.strip() if hash_result.returncode == 0 else None

        # Check if dirty
        dirty_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
            timeout=5,
        )
        is_dirty = bool(dirty_result.stdout.strip()) if dirty_result.returncode == 0 else False

        return {
            "version": get_version(),
            "base": __version_base__,
            "commit": commit_hash,
            "dirty": is_dirty,
        }
    except Exception:
        return {
            "version": get_version(),
            "base": __version_base__,
            "commit": None,
            "dirty": False,
        }


__version__ = get_version()
