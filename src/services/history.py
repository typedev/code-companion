"""Service for reading Claude Code history from ~/.claude/."""

import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import Project, Session, Message, MessageRole, ContentBlock, ContentType, ToolCall
from ..utils import encode_project_path


class HistoryService:
    """Reads and parses Claude Code session history."""

    def __init__(self, claude_dir: Path | None = None):
        self.claude_dir = claude_dir or Path.home() / ".claude"
        self.projects_dir = self.claude_dir / "projects"

    def find_project_history_dir(self, project_path: Path) -> Path | None:
        """Find Claude history directory for a project path."""
        if not self.projects_dir.exists():
            return None

        # Encode the project path as Claude does
        encoded = encode_project_path(str(project_path))

        history_dir = self.projects_dir / encoded
        if history_dir.exists():
            return history_dir

        return None

    def get_sessions_for_path(self, project_path: Path) -> list[Session]:
        """Get all sessions for a project by its filesystem path."""
        history_dir = self.find_project_history_dir(project_path)
        if not history_dir:
            return []

        session_files = self._get_session_files(history_dir)

        sessions = []
        for session_file in session_files:
            session = self._parse_session_metadata(session_file)
            if session:
                sessions.append(session)

        # Sort by timestamp, most recent first
        min_date = datetime.min.replace(tzinfo=timezone.utc)
        sessions.sort(
            key=lambda s: s.timestamp or min_date, reverse=True
        )
        return sessions

    def get_sessions(self, project: Project) -> list[Session]:
        """Get all sessions for a project."""
        project_dir = self.projects_dir / project.encoded_name
        session_files = self._get_session_files(project_dir)

        sessions = []
        for session_file in session_files:
            session = self._parse_session_metadata(session_file)
            if session:
                sessions.append(session)

        # Sort by timestamp, most recent first
        min_date = datetime.min.replace(tzinfo=timezone.utc)
        sessions.sort(
            key=lambda s: s.timestamp or min_date, reverse=True
        )
        return sessions

    def _get_session_files(self, project_dir: Path) -> list[Path]:
        """Get JSONL session files sorted by modification time."""
        if not project_dir.exists():
            return []

        files = list(project_dir.glob("*.jsonl"))
        # Sort by modification time, most recent first
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return files

    def _parse_session_metadata(self, session_file: Path) -> Session | None:
        """Parse session file to extract metadata without loading full content."""
        try:
            message_count = 0
            first_timestamp = None
            first_user_message = ""

            with open(session_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    # Count user and assistant messages
                    if event_type in ("user", "assistant"):
                        message_count += 1

                    # Get first timestamp
                    if first_timestamp is None and "timestamp" in event:
                        try:
                            ts = event["timestamp"]
                            if ts.endswith("Z"):
                                ts = ts[:-1] + "+00:00"
                            first_timestamp = datetime.fromisoformat(ts)
                        except (ValueError, TypeError):
                            pass

                    # Get first user message for preview
                    if not first_user_message and event_type == "user":
                        msg = event.get("message", {})
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            first_user_message = content
                        elif isinstance(content, list):
                            # Extract text from content blocks
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    first_user_message = block.get("text", "")
                                    break

            return Session(
                id=session_file.stem,
                path=session_file,
                message_count=message_count,
                timestamp=first_timestamp,
                preview=first_user_message,
            )

        except (OSError, IOError):
            return None

    def load_session_content(self, session: Session) -> list[Message]:
        """Load full session content with all messages and tool calls."""
        messages: list[Message] = []
        pending_tool_blocks: dict[str, ContentBlock] = {}  # tool_id -> ContentBlock

        try:
            with open(session.path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")
                    timestamp = self._parse_timestamp(event.get("timestamp"))

                    if event_type == "user":
                        msg = self._parse_user_message(event, timestamp)
                        if msg:
                            messages.append(msg)

                    elif event_type == "assistant":
                        msg = self._parse_assistant_message(event, timestamp, pending_tool_blocks)
                        if msg:
                            messages.append(msg)

                    elif event_type == "tool_result":
                        self._handle_tool_result(event, pending_tool_blocks)

        except (OSError, IOError):
            pass

        return messages

    def _parse_timestamp(self, ts_str: str | None) -> datetime | None:
        """Parse ISO timestamp string."""
        if not ts_str:
            return None
        try:
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            return datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            return None

    def _parse_user_message(self, event: dict, timestamp: datetime | None) -> Message | None:
        """Parse a user message event."""
        msg_data = event.get("message", {})
        content = msg_data.get("content", "")

        blocks = []
        if isinstance(content, str):
            blocks.append(ContentBlock(type=ContentType.TEXT, text=content))
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    blocks.append(ContentBlock(type=ContentType.TEXT, text=item.get("text", "")))

        if not blocks:
            return None

        return Message(role=MessageRole.USER, timestamp=timestamp, content_blocks=blocks)

    def _parse_assistant_message(
        self, event: dict, timestamp: datetime | None, pending_tool_blocks: dict
    ) -> Message | None:
        """Parse an assistant message event."""
        msg_data = event.get("message", {})
        content = msg_data.get("content", [])

        blocks = []
        if isinstance(content, str):
            blocks.append(ContentBlock(type=ContentType.TEXT, text=content))
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("type", "")

                if item_type == "text":
                    blocks.append(ContentBlock(type=ContentType.TEXT, text=item.get("text", "")))

                elif item_type == "thinking":
                    blocks.append(ContentBlock(type=ContentType.THINKING, text=item.get("thinking", "")))

                elif item_type == "tool_use":
                    tool_id = item.get("id", "")
                    tool_name = item.get("name", "")
                    tool_input = item.get("input", {})

                    # Create content block and store for later result matching
                    block = ContentBlock(
                        type=ContentType.TOOL_USE,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_id=tool_id,
                    )
                    pending_tool_blocks[tool_id] = block
                    blocks.append(block)

        if not blocks:
            return None

        return Message(role=MessageRole.ASSISTANT, timestamp=timestamp, content_blocks=blocks)

    def _handle_tool_result(self, event: dict, pending_tool_blocks: dict) -> None:
        """Handle tool result event by updating the corresponding content block."""
        tool_id = event.get("tool_use_id", "")
        if tool_id in pending_tool_blocks:
            block = pending_tool_blocks[tool_id]
            block.tool_output = str(event.get("content", ""))
            block.tool_is_error = event.get("is_error", False)
