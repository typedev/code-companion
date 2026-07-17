"""Codex CLI provider adapter."""

import shlex
import shutil
from pathlib import Path

from ..provider_adapter import (
    LaunchPlan,
    McpEndpoint,
    ProviderAdapter,
    ProviderCapabilities,
)
from ..codex_history import CodexHistoryService
from .. import session_notify
from ...models import Session, SessionContent, SessionInsight


def _toml_string(value: str) -> str:
    """A TOML basic string literal for embedding in a ``-c key=value`` override.

    Codex parses the value portion as TOML (falling back to a raw literal), so
    quotes/newlines in e.g. the delegation prompt must be properly escaped.
    """
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


class CodexAdapter(ProviderAdapter):
    """Adapter for the OpenAI Codex CLI.

    History: rollouts under ~/.codex/sessions/ (cwd-indexed, see
    ``CodexHistoryService``). Launch: everything rides on per-launch
    ``-c key=value`` config overrides — MCP via ``[mcp_servers]`` with the
    bearer token referenced by env var (never on disk/argv), notifications via
    the ``notify`` program (``agent-turn-complete`` → marker file), the
    delegation prompt via ``developer_instructions`` (appended — never
    ``model_instructions_file``, which would REPLACE the base prompt).

    No hook-based marker clears: Codex hooks are trust-gated, so an injected
    hook is silently skipped until the user approves it (verified on 0.144.5).
    The window's terminal-focus clear covers it → ``notification_clears=False``.
    Unlike Claude there is no ``--strict-mcp-config`` equivalent: MCP servers
    from the user's own ``~/.codex/config.toml`` also load. Accepted.
    """

    name = "Codex CLI"
    provider_id = "codex"
    cli_command = "codex"
    icon_name = "codex"
    capabilities = ProviderCapabilities(
        mcp=True,
        notifications=True,
        notification_clears=False,
        system_prompt_append=True,
        resume=True,
    )
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
        """Compose the ``codex`` command from ``-c`` overrides (no temp files)."""
        overrides: list[str] = []

        if mcp is not None:
            base = f"mcp_servers.{mcp.server_id}"
            overrides += [
                f"{base}.url={_toml_string(mcp.url(literal_port=True))}",
                f"{base}.bearer_token_env_var={_toml_string(mcp.token_env)}",
                # Our own control surface: auto-approve its (read/act) tools in
                # the TUI instead of prompting on every call.
                f'{base}.default_tools_approval_mode="auto"',
            ]

        if notifications:
            script = session_notify.ensure_codex_notify_script()
            if script is not None:
                notify_argv = ["/bin/sh", str(script), session_name]
                items = ", ".join(_toml_string(a) for a in notify_argv)
                overrides.append(f"notify=[{items}]")

        if extra_system_prompt:
            overrides.append(
                f"developer_instructions={_toml_string(extra_system_prompt)}"
            )

        command = self.cli_command
        for override in overrides:
            command += f" -c {shlex.quote(override)}"
        return LaunchPlan(command=command)
