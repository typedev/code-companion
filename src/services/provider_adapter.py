"""Base interface for AI CLI provider adapters.

Each adapter integrates one AI CLI tool (Claude Code, Codex CLI, ...):
reading its session history AND building the command line that launches it
inside the persistent agent pane (MCP wiring, notification hooks, appended
system prompt). Providers differ in what they support — ``ProviderCapabilities``
records the differences so callers can degrade gracefully.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Session, SessionContent, SessionInsight


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider's CLI can do beyond running bare in a terminal."""

    mcp: bool = False                   # can consume our per-window MCP endpoint
    notifications: bool = False         # can write "needs attention" marker files
    notification_clears: bool = False   # provider hooks can also CLEAR markers
    system_prompt_append: bool = False  # supports an appended extra system prompt
    resume: bool = False                # CLI has a native session resume


@dataclass(frozen=True)
class McpEndpoint:
    """The per-window MCP server the launched CLI should connect to.

    The bearer token is intentionally NOT part of this object: it must never
    land in argv or on disk. Providers reference it indirectly — Claude via
    ``${CC_MCP_TOKEN}`` env interpolation in its config JSON, Codex via
    ``bearer_token_env_var`` — and the launcher injects the real value into the
    process environment.
    """

    port: int
    server_id: str = "code-companion"
    port_env: str = "CC_MCP_PORT"
    token_env: str = "CC_MCP_TOKEN"

    def url(self, literal_port: bool = False) -> str:
        """The endpoint URL, with the port as env placeholder or literal.

        Claude's ``--mcp-config`` JSON supports ``${VAR}`` interpolation, so the
        placeholder form keeps the config file environment-independent. TOML
        (Codex) has no interpolation — those providers inline the port, which is
        fine: ports are not secret.
        """
        port = str(self.port) if literal_port else "${%s}" % self.port_env
        return f"http://127.0.0.1:{port}/mcp"


@dataclass
class LaunchPlan:
    """Everything the window needs to start a provider CLI.

    ``command`` is a shell string (tmux runs it via ``sh -c``; the plain VTE
    fallback uses it as ``run_command``). ``temp_files`` are read-once files the
    window deletes at teardown — anything the CLI needs for its whole lifetime
    (e.g. a notify wrapper script) must NOT be listed here.
    """

    command: str
    temp_files: list[Path] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


class ProviderAdapter(ABC):
    """Base interface for AI CLI provider adapters."""

    # Adapter metadata - must be set by subclasses
    name: str = "Unknown"
    provider_id: str = ""  # registry key, e.g. "claude", "codex"
    cli_command: str = ""  # Command to launch CLI, e.g., "claude", "codex"
    icon_name: str = ""  # Icon name in resources/icons/, e.g., "claude", "codex"
    capabilities: ProviderCapabilities = ProviderCapabilities()
    instruction_filenames: tuple[str, ...] = ()  # ("CLAUDE.md",) / ("AGENTS.md",)

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
    def load_session_content(self, session: Session) -> SessionContent:
        """Load full session content with all messages.

        Args:
            session: Session object to load content for

        Returns:
            A SessionContent with the parsed messages (chronological order) and
            an ``in_progress`` flag when the file's tail is still being written.
        """
        pass

    @abstractmethod
    def get_session_insight(self, session: Session) -> SessionInsight:
        """Extract observability data (tokens, files touched, timing) for a session.

        A cheap streaming pass — does not build the full message list. Providers
        other than Claude supply their own extraction from their own log format.
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

    @abstractmethod
    def build_launch(
        self,
        *,
        project_path: Path,
        session_name: str,
        mcp: McpEndpoint | None,
        extra_system_prompt: str | None,
        notifications: bool,
    ) -> LaunchPlan:
        """Build the CLI launch plan for a fresh session.

        Args:
            project_path: Project the session runs in (its cwd).
            session_name: The supervisor's session name (``cc-<hash>``) — keys
                the notification marker files.
            mcp: Our per-window MCP endpoint, or None when MCP is disabled or
                the provider can't consume it.
            extra_system_prompt: Appended system prompt (worktree delegation
                protocol), or None. Ignored by providers without
                ``capabilities.system_prompt_append``.
            notifications: Whether to inject "needs attention" hooks. Ignored
                by providers without ``capabilities.notifications``.
        """
        pass


# Backward-compatible alias (module was history_adapter.py; the read-path
# contract is unchanged).
HistoryAdapter = ProviderAdapter
