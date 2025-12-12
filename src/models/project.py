"""Project model representing a Claude Code project."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Project:
    """A Claude Code project with its sessions."""

    path: Path  # Original decoded path (e.g., /home/user/my-project)
    encoded_name: str  # Encoded directory name in ~/.claude/projects/
    session_count: int = 0
    last_session_date: datetime | None = None

    @property
    def name(self) -> str:
        """Return the project directory name."""
        return self.path.name

    @property
    def display_path(self) -> str:
        """Return a shortened display path."""
        home = Path.home()
        try:
            return "~" / self.path.relative_to(home)
        except ValueError:
            return str(self.path)

    def __str__(self) -> str:
        return f"{self.name} ({self.session_count} sessions)"
