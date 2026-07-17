"""Claude Code provider adapter."""

import json
import os
import shlex
import tempfile
from pathlib import Path

from ..provider_adapter import (
    LaunchPlan,
    McpEndpoint,
    ProviderAdapter,
    ProviderCapabilities,
)
from ..history import HistoryService
from .. import session_notify
from ...models import Session, SessionContent, SessionInsight


class ClaudeHistoryAdapter(ProviderAdapter):
    """Adapter for the Claude Code CLI.

    Reads session history from ~/.claude/projects/ and builds the launch
    command for the embedded session (MCP config, notification hooks,
    appended system prompt).
    """

    name = "Claude Code"
    provider_id = "claude"
    cli_command = "claude"
    icon_name = "claude"
    capabilities = ProviderCapabilities(
        mcp=True,
        notifications=True,
        notification_clears=True,
        system_prompt_append=True,
        resume=True,
    )
    instruction_filenames = ("CLAUDE.md",)

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

    def load_session_content(self, session: Session) -> SessionContent:
        """Load full session content."""
        return self._service.load_session_content(session)

    def get_session_insight(self, session: Session) -> SessionInsight:
        """Extract observability data for a Claude session."""
        return self._service.parse_session_insight(session.path)

    @classmethod
    def is_available(cls) -> bool:
        """Check if Claude Code is available (has ~/.claude directory)."""
        return (Path.home() / ".claude").exists()

    def build_launch(
        self,
        *,
        project_path: Path,
        session_name: str,
        mcp: McpEndpoint | None,
        extra_system_prompt: str | None,
        notifications: bool,
    ) -> LaunchPlan:
        """Build the ``claude`` launch command.

        MCP config and hook settings are temp files the CLI reads once at
        startup; the token reaches the config only as a ``${CC_MCP_TOKEN}``
        placeholder, resolved from the process environment by Claude itself.
        A failed temp-file write degrades that one feature (bare launch /
        no notifications) instead of failing the launch.
        """
        command = self.cli_command
        temp_files: list[Path] = []

        if mcp is not None:
            config = {
                "mcpServers": {
                    mcp.server_id: {
                        "type": "http",
                        "url": mcp.url(),
                        "headers": {
                            "Authorization": "Bearer ${%s}" % mcp.token_env
                        },
                    }
                }
            }
            config_path = self._write_temp("cc_mcp_", config)
            if config_path is not None:
                temp_files.append(config_path)
                command += (
                    f" --strict-mcp-config"
                    f" --mcp-config {shlex.quote(str(config_path))}"
                )

        if notifications:
            settings_path = self._write_temp(
                "cc_notify_", session_notify.hook_settings(session_name)
            )
            if settings_path is not None:
                temp_files.append(settings_path)
                command += f" --settings {shlex.quote(str(settings_path))}"

        if extra_system_prompt:
            command += (
                f" --append-system-prompt {shlex.quote(extra_system_prompt)}"
            )

        return LaunchPlan(command=command, temp_files=temp_files)

    @staticmethod
    def _write_temp(prefix: str, payload: dict) -> Path | None:
        """Write a read-once JSON temp file; None when the write fails."""
        try:
            fd, path = tempfile.mkstemp(prefix=prefix, suffix=".json")
            os.write(fd, json.dumps(payload).encode())
            os.close(fd)
            return Path(path)
        except OSError:
            return None
