"""Data models for cross-machine sync.

Pure data — no I/O. See docs/plan-sync-across-machines.md.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# Bumped only when the sync-repo layout changes incompatibly. An older client
# that sees a higher schemaVersion in the repo manifest refuses to import.
SCHEMA_VERSION = 1


class SyncState(Enum):
    """Per-project sync status, surfaced as a badge in the Project Manager."""

    NOT_CONFIGURED = "not_configured"  # non-git project or sync disabled
    SYNCED = "synced"                  # local == base == repo
    AHEAD = "ahead"                    # exported local changes this run
    BEHIND = "behind"                  # imported remote changes this run
    CONFLICT = "conflict"             # same file changed on both sides
    PAUSED = "paused"                  # another process holds the sync lock
    ERROR = "error"                    # schema-too-new / auth / git failure
    SYNCING = "syncing"                # transient, during a run


@dataclass
class ProjectSyncStatus:
    """Outcome of syncing a single project."""

    project_id: str
    local_path: str
    state: SyncState
    detail: str = ""
    conflict_files: list[str] = field(default_factory=list)
    snapshot_path: str | None = None
    refreshed_at: datetime | None = None


@dataclass
class SyncResult:
    """Outcome of a whole Sync run."""

    per_project: dict[str, ProjectSyncStatus] = field(default_factory=dict)
    global_ok: bool = True
    error: str | None = None


@dataclass
class FileEntry:
    """A file's identity in the hash manifest (the merge base unit)."""

    rel_path: str
    sha256: str
    size: int
