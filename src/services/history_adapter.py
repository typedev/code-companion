"""Base interface for AI CLI history adapters."""

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import Session, Message


class HistoryAdapter(ABC):
    """Base interface for AI CLI history adapters.

    Each adapter provides access to session history for a specific AI CLI tool
    (Claude Code, Gemini CLI, Codex CLI, etc.).
    """

    # Adapter metadata - must be set by subclasses
    name: str = "Unknown"
    cli_command: str = ""  # Command to launch CLI, e.g., "claude", "gemini"
    icon_name: str = ""  # Icon name in resources/icons/, e.g., "claude", "gemini"

    @property
    @abstractmethod
    def config_dir(self) -> Path:
        """Return the config directory for this adapter (e.g., ~/.claude)."""
        pass

    @abstractmethod
    def find_project_history_dir(self, project_path: Path) -> Path | None:
        """Find history directory for a project.

        Args:
            project_path: Absolute path to the project directory

        Returns:
            Path to history directory, or None if not found
        """
        pass

    @abstractmethod
    def get_sessions_for_path(self, project_path: Path) -> list[Session]:
        """Get all sessions for a project path.

        Args:
            project_path: Absolute path to the project directory

        Returns:
            List of Session objects, sorted by timestamp (most recent first)
        """
        pass

    @abstractmethod
    def load_session_content(self, session: Session) -> list[Message]:
        """Load full session content with all messages.

        Args:
            session: Session object to load content for

        Returns:
            List of Message objects in chronological order
        """
        pass

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Check if this adapter's CLI tool is available.

        Returns:
            True if the CLI tool is installed and configured
        """
        pass
