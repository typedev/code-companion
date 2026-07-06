"""Single-writer lock for the shared sync clone.

The app is multi-process (each project is its own process) but there is exactly
one local sync clone. This lock serializes all git access to it. Mirrors the
PID-file pattern of ``ProjectLock`` and adds a context-manager API.
"""

import hashlib
import os
from pathlib import Path

LOCK_DIR = Path("/tmp/code-companion-locks")


class SyncBusy(Exception):
    """Raised when another process is already syncing."""


class SyncLock:
    """PID-file lock guarding the single sync clone at ``clone_path``."""

    def __init__(self, clone_path: str | Path):
        self._key = str(Path(clone_path).resolve())
        path_hash = hashlib.md5(self._key.encode()).hexdigest()[:16]
        self.lock_file = LOCK_DIR / f"sync-{path_hash}.lock"
        self._pid = os.getpid()

    def acquire(self) -> bool:
        if self.is_locked():
            return False
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.lock_file, "w") as f:
                f.write(str(self._pid))
            return True
        except OSError:
            return False

    def release(self) -> None:
        try:
            if self.lock_file.exists():
                with open(self.lock_file, "r") as f:
                    pid = int(f.read().strip())
                if pid == self._pid:
                    self.lock_file.unlink()
        except (OSError, ValueError):
            pass

    def is_locked(self) -> bool:
        if not self.lock_file.exists():
            return False
        try:
            with open(self.lock_file, "r") as f:
                pid = int(f.read().strip())
            if pid == self._pid:
                return False
            if self._is_process_alive(pid):
                return True
            self.lock_file.unlink()  # stale
            return False
        except (OSError, ValueError):
            try:
                self.lock_file.unlink()
            except OSError:
                pass
            return False

    def _is_process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_text()
                if "python" in cmdline and (
                    "code-companion" in cmdline or "src.main" in cmdline or "main.py" in cmdline
                ):
                    return True
                return False
            return True
        except (OSError, PermissionError):
            return False

    def __enter__(self) -> "SyncLock":
        if not self.acquire():
            raise SyncBusy("Another process is already syncing")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
