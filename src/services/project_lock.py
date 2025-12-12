"""Service for managing project lock files."""

import hashlib
import os
from pathlib import Path


class ProjectLock:
    """Manages lock file for a project to prevent duplicate opening."""

    def __init__(self, project_path: str):
        self.project_path = str(Path(project_path).resolve())
        self.lock_dir = Path("/tmp/claude-companion-locks")
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

            # Check if PID exists
            try:
                os.kill(pid, 0)  # Signal 0 = check existence
                return True  # Process exists, locked
            except OSError:
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
