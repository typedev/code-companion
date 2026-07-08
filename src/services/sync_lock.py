"""Single-writer lock for the shared sync clone.

The app is multi-process (each project is its own process) but there is exactly
one local sync clone. This lock serializes all git access to it. Backed by the
same ``fcntl.flock`` primitive as ``ProjectLock`` (kernel auto-releases on
process death — no stale locks), plus a context-manager API.
"""

import hashlib
from pathlib import Path

from .project_lock import LOCK_DIR, FlockLock


class SyncBusy(Exception):
    """Raised when another process is already syncing."""


class SyncLock(FlockLock):
    """flock guarding the single sync clone at ``clone_path``."""

    def __init__(self, clone_path: str | Path):
        self._key = str(Path(clone_path).resolve())
        path_hash = hashlib.md5(self._key.encode()).hexdigest()[:16]
        super().__init__(LOCK_DIR / f"sync-{path_hash}.lock")

    def __enter__(self) -> "SyncLock":
        if not self.acquire():
            raise SyncBusy("Another process is already syncing")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
