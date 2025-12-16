"""Service for managing project lock files."""

import hashlib
import os
import signal
from pathlib import Path

LOCK_DIR = Path("/tmp/code-companion-locks")


class ManagerLock:
    """Manages lock file for Project Manager to enable single-instance activation."""

    LOCK_FILE = LOCK_DIR / "manager.lock"

    def __init__(self):
        self._pid = os.getpid()

    def acquire(self) -> bool:
        """Acquire lock for Project Manager. Returns True if successful."""
        if self.is_locked():
            return False

        LOCK_DIR.mkdir(parents=True, exist_ok=True)

        try:
            with open(self.LOCK_FILE, "w") as f:
                f.write(str(self._pid))
            return True
        except OSError:
            return False

    def release(self):
        """Release the lock."""
        try:
            if self.LOCK_FILE.exists():
                with open(self.LOCK_FILE, "r") as f:
                    pid = int(f.read().strip())
                if pid == self._pid:
                    self.LOCK_FILE.unlink()
        except (OSError, ValueError):
            pass

    def is_locked(self) -> bool:
        """Check if Project Manager is locked by another process."""
        if not self.LOCK_FILE.exists():
            return False

        try:
            with open(self.LOCK_FILE, "r") as f:
                pid = int(f.read().strip())

            if pid == self._pid:
                return False

            if self._is_process_alive(pid):
                return True
            else:
                self.LOCK_FILE.unlink()
                return False

        except (OSError, ValueError):
            try:
                self.LOCK_FILE.unlink()
            except OSError:
                pass
            return False

    def get_lock_pid(self) -> int | None:
        """Get the PID holding the lock, or None if not locked."""
        if not self.LOCK_FILE.exists():
            return None
        try:
            with open(self.LOCK_FILE, "r") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def activate_existing() -> bool:
        """Send SIGUSR1 to existing Project Manager to activate its window.

        Returns True if signal was sent successfully.
        """
        lock = ManagerLock()
        pid = lock.get_lock_pid()
        if pid and lock._is_process_alive(pid):
            try:
                os.kill(pid, signal.SIGUSR1)
                return True
            except OSError:
                return False
        return False

    def _is_process_alive(self, pid: int) -> bool:
        """Check if process with given PID is alive and is our app."""
        try:
            os.kill(pid, 0)
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_text()
                if "python" in cmdline and ("code-companion" in cmdline or "src.main" in cmdline):
                    return True
                return False
            return True
        except (OSError, PermissionError):
            return False


class ProjectLock:
    """Manages lock file for a project to prevent duplicate opening."""

    def __init__(self, project_path: str):
        self.project_path = str(Path(project_path).resolve())
        self.lock_dir = Path("/tmp/code-companion-locks")
        self.lock_file = self._get_lock_file_path()
        self._pid = os.getpid()

    def _get_lock_file_path(self) -> Path:
        """Generate lock file path based on project path hash."""
        path_hash = hashlib.md5(self.project_path.encode()).hexdigest()[:16]
        return self.lock_dir / f"{path_hash}.lock"

    def acquire(self) -> bool:
        """Acquire lock for this project. Returns True if successful."""
        # Check if already locked by another process
        if self.is_locked():
            return False

        # Create lock directory
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        # Write lock file with our PID
        try:
            with open(self.lock_file, "w") as f:
                f.write(str(self._pid))
            return True
        except OSError:
            return False

    def release(self):
        """Release the lock."""
        try:
            if self.lock_file.exists():
                # Only remove if we own the lock
                with open(self.lock_file, "r") as f:
                    pid = int(f.read().strip())
                if pid == self._pid:
                    self.lock_file.unlink()
        except (OSError, ValueError):
            pass

    def is_locked(self) -> bool:
        """Check if project is locked by another process."""
        if not self.lock_file.exists():
            return False

        try:
            with open(self.lock_file, "r") as f:
                pid = int(f.read().strip())

            # Check if process is still running
            if pid == self._pid:
                return False  # It's us

            # Check if PID exists and is a python process (our app)
            if self._is_process_alive(pid):
                return True  # Process exists, locked
            else:
                # Process doesn't exist, stale lock
                self.lock_file.unlink()
                return False

        except (OSError, ValueError):
            # Corrupted lock file, remove it
            try:
                self.lock_file.unlink()
            except OSError:
                pass
            return False

    def force_release(self) -> bool:
        """Force release lock by killing the owning process."""
        if not self.lock_file.exists():
            return True

        try:
            with open(self.lock_file, "r") as f:
                pid = int(f.read().strip())

            # Don't kill ourselves
            if pid == self._pid:
                return True

            # Try to kill the process
            try:
                os.kill(pid, 15)  # SIGTERM
            except OSError:
                pass  # Process may already be dead

            # Remove lock file
            try:
                self.lock_file.unlink()
            except OSError:
                pass

            return True

        except (OSError, ValueError):
            # Remove corrupted lock
            try:
                self.lock_file.unlink()
            except OSError:
                pass
            return True

    def get_lock_pid(self) -> int | None:
        """Get the PID holding the lock, or None if not locked."""
        if not self.lock_file.exists():
            return None
        try:
            with open(self.lock_file, "r") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _is_process_alive(self, pid: int) -> bool:
        """Check if process with given PID is alive and is our app."""
        try:
            # Check if process exists
            os.kill(pid, 0)

            # Verify it's a python process (could be our app)
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_text()
                # Check if it's python running our app
                if "python" in cmdline and "code-companion" in cmdline:
                    return True
                elif "python" in cmdline and "main.py" in cmdline:
                    return True
                elif "python" in cmdline and "src.main" in cmdline:
                    return True
                # PID exists but it's not our app - stale lock
                return False
            return True  # Can't read cmdline, assume alive

        except (OSError, PermissionError):
            return False
