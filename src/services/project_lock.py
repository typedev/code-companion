"""Advisory-lock service for single-window / single-instance guarantees.

Locks are backed by ``fcntl.flock`` on a file descriptor held open for the
process lifetime. The kernel releases the lock automatically when the process
dies — crash, ``SIGKILL``, anything — so there are no stale locks to reclaim and
no PID-reuse misdetection (Linux-only, which the app already assumes). The PID is
still written into the file, but only as informational metadata for the
"already open" dialog and the SIGUSR1 activation target.
"""

import fcntl
import hashlib
import os
import signal
import time
from pathlib import Path

LOCK_DIR = Path("/tmp/code-companion-locks")


class _FlockLock:
    """A single advisory lock backed by ``flock`` on ``lock_file``."""

    def __init__(self, lock_file: Path):
        self.lock_file = lock_file
        self._pid = os.getpid()
        self._fd: int | None = None  # held open while we own the lock

    def acquire(self) -> bool:
        """Take the lock. Returns True on success, False if another holds it."""
        if self._fd is not None:
            return True  # already ours
        try:
            LOCK_DIR.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False  # a live process holds it
        # We own it — record our PID (informational only).
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(self._pid).encode())
        except OSError:
            pass
        self._fd = fd
        return True

    def release(self):
        """Release the lock (closing the fd alone would also release it)."""
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        # Deliberately do NOT unlink: removing a file under flock is a race
        # footgun (another process may already hold the same path). An empty
        # leftover file in /tmp is harmless and reused next time.

    def is_locked(self) -> bool:
        """True iff a *live* process (not us) currently holds the lock.

        Stale-proof: a dead owner's lock was already freed by the kernel, so the
        probe simply succeeds and we report unlocked.
        """
        if self._fd is not None:
            return False  # we hold it; not locked "by another"
        if not self.lock_file.exists():
            return False
        try:
            fd = os.open(self.lock_file, os.O_RDWR)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False  # we could have taken it -> nobody holds it
        except OSError:
            return True  # held by a live process
        finally:
            os.close(fd)

    def get_lock_pid(self) -> int | None:
        """PID recorded by the current holder (meaningful only while locked)."""
        try:
            return int(self.lock_file.read_text().strip())
        except (OSError, ValueError):
            return None


class ManagerLock(_FlockLock):
    """Single-instance lock for the Project Manager window."""

    LOCK_FILE = LOCK_DIR / "manager.lock"

    def __init__(self):
        super().__init__(self.LOCK_FILE)

    @staticmethod
    def activate_existing() -> bool:
        """Signal a running Project Manager to raise its window (SIGUSR1).

        Returns True if a live manager was signalled. Gating on the flock means
        the PID we signal belongs to the live holder, so a reused PID can't be
        hit by mistake.
        """
        lock = ManagerLock()
        if not lock.is_locked():
            return False
        pid = lock.get_lock_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGUSR1)
                return True
            except OSError:
                return False
        return False


class ProjectLock(_FlockLock):
    """Prevents opening the same project in more than one window."""

    def __init__(self, project_path: str):
        self.project_path = str(Path(project_path).resolve())
        path_hash = hashlib.md5(self.project_path.encode()).hexdigest()[:16]
        super().__init__(LOCK_DIR / f"{path_hash}.lock")

    def force_release(self) -> bool:
        """Take over from a live owner: SIGTERM it, wait for the flock to free.

        With flock there are no stale locks, so this only ever fires against a
        genuinely running window. Killing it lets the kernel drop its lock; we
        poll briefly so the caller's subsequent acquire succeeds.
        """
        pid = self.get_lock_pid()
        if pid and pid != self._pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            for _ in range(20):  # ~2s for the owner to exit and free the lock
                if not self.is_locked():
                    break
                time.sleep(0.1)
        return True
