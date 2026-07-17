"""Codex CLI provider adapter."""

import shutil
from pathlib import Path

from ..provider_adapter import (
    LaunchPlan,
    McpEndpoint,
    ProviderAdapter,
    ProviderCapabilities,
)
from ..codex_history import CodexHistoryService
from ...models import Session, SessionContent, SessionInsight


class CodexAdapter(ProviderAdapter):
    """Adapter for the OpenAI Codex CLI.

    Reads rollout history from ~/.codex/sessions/ (cwd-indexed, see
    ``CodexHistoryService``). Launch is a bare ``codex`` for now — MCP wiring,
    notifications and system-prompt injection land with the Stage-3 launch
    support (capabilities flip on then).
    """

    name = "Codex CLI"
    provider_id = "codex"
    cli_command = "codex"
    icon_name = "codex"
    capabilities = ProviderCapabilities(resume=True)
    instruction_filenames = ("AGENTS.md",)

    def __init__(self):
        self._service = CodexHistoryService()

    @property
    def config_dir(self) -> Path:
        """Return the Codex home directory (~/.codex or $CODEX_HOME)."""
        return self._service.codex_home

    def find_project_history_dir(self, project_path: Path) -> Path | None:
        """Codex has no per-project directory; the sessions root or None."""
        return self._service.find_project_history_dir(project_path)

    def get_sessions_for_path(self, project_path: Path) -> list[Session]:
        """All Codex sessions recorded for this project's cwd."""
        return self._service.get_sessions_for_path(project_path)

    def load_session_content(self, session: Session) -> SessionContent:
        """Load full session content."""
        return self._service.load_session_content(session)

    def get_session_insight(self, session: Session) -> SessionInsight:
        """Extract observability data for a Codex session."""
        return self._service.parse_session_insight(session.path)

    @classmethod
    def is_available(cls) -> bool:
        """Check if the Codex CLI is installed."""
        return shutil.which("codex") is not None

    def build_launch(
        self,
        *,
        project_path: Path,
        session_name: str,
        mcp: McpEndpoint | None,
        extra_system_prompt: str | None,
        notifications: bool,
    ) -> LaunchPlan:
        """Bare launch until Stage-3 lands MCP/notify/prompt injection."""
        return LaunchPlan(command=self.cli_command)
