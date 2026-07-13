"""Service for reading Claude Code history from ~/.claude/."""

import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import (
    Project, Session, Message, MessageRole, ContentBlock, ContentType, SessionContent,
    SessionInsight, TokenUsage,
)
from ..utils import encode_project_path

# Tool names whose input names a file the session touched (8.1 files_touched).
_FILE_TOOL_KEYS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}


def _is_command_meta(text: str) -> bool:
    """True for slash-command / local-command wrapper messages (not a real prompt).

    Sessions opened via a slash command (e.g. ``/usage-credits``) begin with a
    ``<local-command-caveat>`` / ``<command-…>`` user event; these should not be
    used as a session preview.
    """
    t = text.lstrip()
    return (
        t.startswith("<local-command-caveat>")
        or t.startswith("<local-command-stdout>")
        or t.startswith("<command-")
    )


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

            with open(session_file, "r", encoding="utf-8", errors="replace") as f:
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

                    # Get first *real* user message for preview, skipping
                    # slash-command wrappers (e.g. the /usage-credits caveat).
                    if not first_user_message and event_type == "user":
                        candidate = self._extract_user_text(event)
                        if candidate and not _is_command_meta(candidate):
                            first_user_message = candidate

            return Session(
                id=session_file.stem,
                path=session_file,
                message_count=message_count,
                timestamp=first_timestamp,
                preview=first_user_message,
            )

        except (OSError, IOError):
            return None

    def parse_session_insight(self, session_file: Path) -> SessionInsight:
        """Extract observability data (tokens, files, timing) in one streaming pass.

        Mirrors ``_parse_session_metadata``'s cheap line-by-line read (never builds
        ``Message`` objects). Token ``usage`` is bucketed per ``message.model`` since
        a session can mix models (main agent + subagents). Robust to a still-writing
        tail: an unparsable last line is simply skipped.
        """
        insight = SessionInsight(session_id=session_file.stem, path=session_file)
        seen_files: set[str] = set()
        counted_ids: set[str] = set()  # usage is counted once per assistant message id

        try:
            with open(session_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")
                    if event_type in ("user", "assistant"):
                        insight.message_count += 1

                    ts = self._parse_timestamp(event.get("timestamp"))
                    if ts is not None:
                        if insight.first_ts is None:
                            insight.first_ts = ts
                        insight.last_ts = ts

                    if event_type == "assistant":
                        self._accumulate_assistant_insight(event, insight, seen_files, counted_ids)
                    elif event_type == "user" and not insight.first_prompt:
                        insight.first_prompt = self._extract_user_text(event)

        except (OSError, IOError):
            pass

        return insight

    def _accumulate_assistant_insight(
        self, event: dict, insight: SessionInsight, seen_files: set[str], counted_ids: set[str]
    ) -> None:
        """Fold one assistant event's usage / touched files / last text into ``insight``.

        A single assistant message is split across several JSONL lines (one per
        content block: thinking / text / tool_use), and **every line repeats the
        same ``usage``**. So usage is counted once per ``message.id`` (falling back
        to ``requestId``), while content blocks are processed on every line.
        All-zero usage (e.g. ``<synthetic>`` placeholder messages) is ignored.
        """
        msg = event.get("message", {})

        usage = msg.get("usage") or {}
        if usage:
            tokens = TokenUsage(
                input=int(usage.get("input_tokens", 0) or 0),
                output=int(usage.get("output_tokens", 0) or 0),
                cache_creation=int(usage.get("cache_creation_input_tokens", 0) or 0),
                cache_read=int(usage.get("cache_read_input_tokens", 0) or 0),
            )
            msg_id = msg.get("id") or event.get("requestId")
            if tokens.total > 0 and (msg_id is None or msg_id not in counted_ids):
                if msg_id is not None:
                    counted_ids.add(msg_id)
                model = msg.get("model") or "unknown"
                insight.usage_by_model.setdefault(model, TokenUsage()).add(tokens)
                # Input-side occupancy of this turn = how full the context window is.
                # Runs once per message (dedup above), in file order, so the final
                # assignment reflects the latest turn. Output is excluded on purpose
                # (it is generation, not window occupancy).
                insight.last_context_tokens = (
                    tokens.input + tokens.cache_read + tokens.cache_creation
                )

        content = msg.get("content", [])
        if not isinstance(content, list):
            return
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type == "text":
                text = (item.get("text") or "").strip()
                if text:
                    insight.last_assistant_text = text
            elif item_type == "tool_use":
                key = _FILE_TOOL_KEYS.get(item.get("name", ""))
                if not key:
                    continue
                path = (item.get("input") or {}).get(key)
                if path and path not in seen_files:
                    seen_files.add(path)
                    insight.files_touched.append(path)

    @staticmethod
    def _extract_user_text(event: dict) -> str:
        """First textual content of a user event (skips tool_result-only messages)."""
        content = event.get("message", {}).get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return (block.get("text") or "").strip()
        return ""

    def load_session_content(self, session: Session) -> SessionContent:
        """Load full session content with all messages and tool calls.

        Opens with ``errors="replace"`` so a stray non-UTF-8 byte never crashes
        the load. A JSON parse failure on an intermediate line is skipped, but a
        failure on the *last* non-empty line is treated as a still-writing tail
        (``in_progress=True``) rather than silently dropped.
        """
        messages: list[Message] = []
        pending_tool_blocks: dict[str, ContentBlock] = {}  # tool_id -> ContentBlock
        in_progress = False

        try:
            with open(session.path, "r", encoding="utf-8", errors="replace") as f:
                # Keep only non-empty lines; the final one may be a partial write.
                lines = [line for line in f if line.strip()]

                for index, line in enumerate(lines):
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        # A broken *last* line == the session is still being written.
                        if index == len(lines) - 1:
                            in_progress = True
                        continue

                    event_type = event.get("type", "")
                    timestamp = self._parse_timestamp(event.get("timestamp"))

                    if event_type == "user":
                        msg = self._parse_user_message(event, timestamp, pending_tool_blocks)
                        if msg:
                            messages.append(msg)

                    elif event_type == "assistant":
                        msg = self._parse_assistant_message(event, timestamp, pending_tool_blocks)
                        if msg:
                            messages.append(msg)

                    elif event_type == "system":
                        msg = self._parse_system_message(event, timestamp)
                        if msg:
                            messages.append(msg)

        except (OSError, IOError):
            pass

        return SessionContent(messages=messages, in_progress=in_progress)

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

    def _parse_user_message(
        self, event: dict, timestamp: datetime | None, pending_tool_blocks: dict
    ) -> Message | None:
        """Parse a user message event."""
        msg_data = event.get("message", {})
        content = msg_data.get("content", "")

        blocks = []
        has_tool_result = False

        if isinstance(content, str):
            blocks.append(ContentBlock(type=ContentType.TEXT, text=content))
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("type", "")

                if item_type == "text":
                    blocks.append(ContentBlock(type=ContentType.TEXT, text=item.get("text", "")))

                elif item_type == "tool_result":
                    # Tool results come as user messages with tool_result content blocks
                    tool_id = item.get("tool_use_id", "")
                    result_content = item.get("content", "")
                    is_error = item.get("is_error", False)

                    if tool_id and tool_id in pending_tool_blocks:
                        block = pending_tool_blocks[tool_id]
                        block.tool_output = str(result_content) if result_content else ""
                        block.tool_is_error = is_error
                        has_tool_result = True

        # Don't create message if it only contains tool results (they're attached to tool_use)
        if has_tool_result and not blocks:
            return None

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

    def _parse_system_message(self, event: dict, timestamp: datetime | None) -> Message | None:
        """Parse a system event (compaction, etc.)."""
        subtype = event.get("subtype", "")
        content = event.get("content", "")

        # Only show compact_boundary events for now
        if subtype != "compact_boundary":
            return None

        # Extract useful metadata
        metadata = event.get("compactMetadata", {})
        trigger = metadata.get("trigger", "")
        pre_tokens = metadata.get("preTokens", 0)

        # Build display text
        text = content or "Conversation compacted"
        if pre_tokens:
            text = f"{text} ({pre_tokens:,} tokens)"
        if trigger:
            text = f"{text} [{trigger}]"

        block = ContentBlock(type=ContentType.SYSTEM, text=text)
        return Message(role=MessageRole.SYSTEM, timestamp=timestamp, content_blocks=[block])
