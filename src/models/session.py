"""Session model representing a Claude Code session."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Session:
    """A Claude Code session parsed from JSONL file."""

    id: str  # Session UUID (filename without extension)
    path: Path  # Full path to JSONL file
    message_count: int = 0
    timestamp: datetime | None = None
    preview: str = ""  # First user message preview

    @property
    def display_date(self) -> str:
        """Return formatted date for display."""
        if self.timestamp is None:
            return "Unknown"
        return self.timestamp.strftime("%Y-%m-%d %H:%M")

    @property
    def short_preview(self) -> str:
        """Return truncated preview text."""
        max_len = 50
        if len(self.preview) <= max_len:
            return self.preview
        return self.preview[: max_len - 3] + "..."

    def __str__(self) -> str:
        return f"{self.display_date} ({self.message_count} msgs)"
