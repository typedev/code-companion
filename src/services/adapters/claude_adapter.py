"""Claude Code history adapter."""

from pathlib import Path

from ..history_adapter import HistoryAdapter
from ..history import HistoryService
from ...models import Session, Message


class ClaudeHistoryAdapter(HistoryAdapter):
    """Adapter for Claude Code CLI history.

    Reads session history from ~/.claude/projects/
    """

    name = "Claude Code"
    cli_command = "claude"
    icon_name = "claude"

    def __init__(self):
        self._config_dir = Path.home() / ".claude"
        self._service = HistoryService(self._config_dir)

    @property
    def config_dir(self) -> Path:
        """Return ~/.claude directory."""
        return self._config_dir

    def find_project_history_dir(self, project_path: Path) -> Path | None:
        """Find Claude history directory for a project."""
        return self._service.find_project_history_dir(project_path)

    def get_sessions_for_path(self, project_path: Path) -> list[Session]:
        """Get all Claude sessions for a project path."""
        return self._service.get_sessions_for_path(project_path)

    def load_session_content(self, session: Session) -> list[Message]:
        """Load full session content."""
        return self._service.load_session_content(session)

    @classmethod
    def is_available(cls) -> bool:
        """Check if Claude Code is available (has ~/.claude directory)."""
        return (Path.home() / ".claude").exists()
