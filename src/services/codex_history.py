"""Codex CLI session history reader.

Codex rollouts live in ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`` and
are NOT keyed by project directory — the project cwd sits in the first-line
``session_meta`` event. Finding a project's sessions therefore needs a scan, so
this service keeps a small index (first-line facts keyed by file path, guarded
by mtime+size) at ``<config>/codex_session_index.json``; per-session display
metadata is computed lazily for matching files only and cached in the same
index.

The rollout event schema is NOT a stable contract (it drifts between Codex
versions), so every line is parsed defensively: unknown top-level types and
payload types are skipped, and a malformed tail line means "still being
written" rather than "corrupt" — same convention as the Claude reader.
Observed shape (codex-cli 0.144.x): ``session_meta`` / ``response_item``
(payload types message, reasoning, function_call, custom_tool_call,
local_shell_call, web_search_call and their ``*_output``) / ``event_msg``
(user_message, agent_message, token_count, task_complete) / ``turn_context``
(carries the active model id).
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from ..models import (
    ContentBlock,
    ContentType,
    Message,
    MessageRole,
    Session,
    SessionContent,
    SessionInsight,
    TokenUsage,
)
from .config_path import get_config_dir
from ..utils.atomic_write import atomic_write_text

# Synthetic user payloads Codex wraps into the transcript; not real prompts.
_WRAPPER_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
    "<multi_agent_mode>",
    "<turn_aborted",
    "<AGENTS.md",
)

_TOOL_CALL_TYPES = {
    "function_call",
    "custom_tool_call",
    "local_shell_call",
    "web_search_call",
}
_TOOL_OUTPUT_TYPES = {"function_call_output", "custom_tool_call_output"}

_INDEX_VERSION = 1


def _parse_ts(value) -> datetime | None:
    """ISO-8601 (with trailing Z) → aware datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_wrapper_text(text: str) -> bool:
    return text.lstrip().startswith(_WRAPPER_PREFIXES)


def _content_text(content) -> str:
    """Join the text of a Codex content list (or accept a bare string)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts)


class CodexHistoryService:
    """Reader for Codex CLI rollout files."""

    def __init__(self, codex_home: Path | None = None):
        home = codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        self.codex_home = Path(home)
        self.sessions_root = self.codex_home / "sessions"
        self._index_path = get_config_dir() / "codex_session_index.json"
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ index

    def _load_index(self) -> dict:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict) or data.get("version") != _INDEX_VERSION:
            return {}
        files = data.get("files")
        return files if isinstance(files, dict) else {}

    def _save_index(self, files: dict) -> None:
        try:
            atomic_write_text(
                self._index_path,
                json.dumps({"version": _INDEX_VERSION, "files": files}),
            )
        except OSError:
            pass  # index is a cache; worst case we rescan next time

    @staticmethod
    def _read_first_line_meta(path: Path) -> dict | None:
        """The ``session_meta`` facts from a rollout's first line, or None.

        Accepts both the wrapped ``{"type": "session_meta", "payload": {...}}``
        form and a bare meta object (schema is not a stable contract).
        """
        try:
            with open(path, "r", encoding="utf-8") as handle:
                line = handle.readline(1024 * 1024).strip()
        except OSError:
            return None
        if not line:
            return None
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        payload = data.get("payload") if data.get("type") == "session_meta" else data
        if not isinstance(payload, dict) or "cwd" not in payload:
            return None
        return {
            "cwd": str(payload.get("cwd", "")),
            "session_id": str(payload.get("id") or payload.get("session_id") or ""),
            "ts": payload.get("timestamp") or data.get("timestamp") or "",
        }

    def _refresh_index(self) -> dict:
        """Bring the first-line index up to date with the sessions tree.

        Stat-only for unchanged files; reads exactly one line for new/changed
        files; prunes deleted ones. Returns the fresh ``{path: entry}`` map.
        """
        index = self._load_index()
        seen: set[str] = set()
        changed = False
        if self.sessions_root.exists():
            for path in self.sessions_root.rglob("*.jsonl"):
                key = str(path)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                seen.add(key)
                entry = index.get(key)
                if (
                    entry
                    and entry.get("mtime") == stat.st_mtime
                    and entry.get("size") == stat.st_size
                ):
                    continue
                meta = self._read_first_line_meta(path)
                if meta is None:
                    # Unreadable header: remember the attempt so we don't
                    # re-read the file every refresh until it changes.
                    meta = {"cwd": "", "session_id": "", "ts": ""}
                entry = {**meta, "mtime": stat.st_mtime, "size": stat.st_size}
                index[key] = entry
                changed = True
        for key in list(index):
            if key not in seen:
                del index[key]
                changed = True
        if changed:
            self._save_index(index)
        return index

    # ------------------------------------------------------------ public API

    def find_project_history_dir(self, project_path: Path) -> Path | None:
        """Codex has no per-project directory; return the sessions root."""
        return self.sessions_root if self.sessions_root.exists() else None

    def rollout_paths_for_cwd(self, project_path: Path) -> list[Path]:
        """Rollout file paths whose recorded cwd is this project (index-only).

        Lean counterpart to ``get_sessions_for_path``: refreshes the first-line
        index and filters by cwd, but never scans display metadata. Cheap enough
        to call from the sync worker per project. Empty when Codex is unused.
        """
        if not self.sessions_root.exists():
            return []
        cwd = os.path.realpath(str(project_path))
        with self._lock:
            index = self._refresh_index()
            return [Path(key) for key, entry in index.items()
                    if entry.get("cwd") == cwd]

    def get_sessions_for_path(self, project_path: Path) -> list[Session]:
        """All Codex sessions whose recorded cwd is this project."""
        cwd = os.path.realpath(str(project_path))
        with self._lock:
            index = self._refresh_index()
            sessions = []
            meta_changed = False
            for key, entry in index.items():
                if entry.get("cwd") != cwd:
                    continue
                path = Path(key)
                session, cached = self._session_from_entry(path, entry)
                if session is None:
                    continue
                if not cached:
                    meta_changed = True
                sessions.append(session)
            if meta_changed:
                self._save_index(index)
        # Sort by last activity, most recent first (matches the Claude reader);
        # epoch floats sidestep aware/None comparison issues.
        def sort_key(s: Session) -> float:
            ts = s.last_timestamp or s.timestamp
            return ts.timestamp() if ts else float("-inf")

        sessions.sort(key=sort_key, reverse=True)
        return sessions

    def _session_from_entry(self, path: Path, entry: dict) -> tuple[Session | None, bool]:
        """Build a Session for an index entry, using cached display metadata.

        Returns ``(session, was_cached)``; ``was_cached=False`` means the entry
        was (re)computed and the caller should persist the index.
        """
        meta = entry.get("display")
        if isinstance(meta, dict) and meta.get("mtime") == entry.get("mtime"):
            cached = True
        else:
            meta = self._scan_display_metadata(path)
            if meta is None:
                return None, True
            meta["mtime"] = entry.get("mtime")
            entry["display"] = meta
            cached = False
        session = Session(
            id=entry.get("session_id") or path.stem,
            path=path,
            message_count=int(meta.get("message_count", 0)),
            timestamp=_parse_ts(entry.get("ts")) or _parse_ts(meta.get("first_ts")),
            preview=meta.get("preview", ""),
            ai_title="",  # Codex has no auto-title equivalent
            last_timestamp=_parse_ts(meta.get("last_ts")),
        )
        return session, cached

    @staticmethod
    def _scan_display_metadata(path: Path) -> dict | None:
        """One defensive streaming pass: preview, message count, last activity."""
        preview = ""
        count = 0
        first_ts = ""
        last_ts = ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    ts = data.get("timestamp")
                    if isinstance(ts, str) and ts:
                        first_ts = first_ts or ts
                        last_ts = ts
                    payload = data.get("payload")
                    if data.get("type") != "response_item" or not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "message":
                        continue
                    role = payload.get("role")
                    text = _content_text(payload.get("content"))
                    if role == "user" and text and not _is_wrapper_text(text):
                        count += 1
                        if not preview:
                            preview = text.strip().split("\n", 1)[0][:200]
                    elif role == "assistant" and text:
                        count += 1
        except OSError:
            return None
        return {
            "preview": preview,
            "message_count": count,
            "first_ts": first_ts,
            "last_ts": last_ts,
        }

    def load_session_content(self, session: Session) -> SessionContent:
        """Full parse of a rollout into chronological messages."""
        messages: list[Message] = []
        pending_tools: dict[str, ContentBlock] = {}
        current: Message | None = None
        last_line = ""

        def assistant_message(ts) -> Message:
            nonlocal current
            if current is None or current.role is not MessageRole.ASSISTANT:
                current = Message(role=MessageRole.ASSISTANT, timestamp=ts)
                messages.append(current)
            return current

        try:
            handle = open(session.path, "r", encoding="utf-8")
        except OSError:
            return SessionContent(messages=[])

        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                last_line = line
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict) or data.get("type") != "response_item":
                    continue
                payload = data.get("payload")
                if not isinstance(payload, dict):
                    continue
                ts = _parse_ts(data.get("timestamp"))
                ptype = payload.get("type")

                if ptype == "message":
                    role = payload.get("role")
                    text = _content_text(payload.get("content"))
                    if role == "user":
                        if not text or _is_wrapper_text(text):
                            continue
                        current = Message(
                            role=MessageRole.USER,
                            timestamp=ts,
                            content_blocks=[
                                ContentBlock(type=ContentType.TEXT, text=text)
                            ],
                        )
                        messages.append(current)
                    elif role == "assistant" and text:
                        assistant_message(ts).content_blocks.append(
                            ContentBlock(type=ContentType.TEXT, text=text)
                        )
                elif ptype == "reasoning":
                    # Codex encrypts reasoning; only the summary (when present)
                    # is readable.
                    text = _content_text(payload.get("summary"))
                    if text:
                        assistant_message(ts).content_blocks.append(
                            ContentBlock(type=ContentType.THINKING, text=text)
                        )
                elif ptype in _TOOL_CALL_TYPES:
                    block = self._tool_use_block(ptype, payload)
                    assistant_message(ts).content_blocks.append(block)
                    if block.tool_id:
                        pending_tools[block.tool_id] = block
                elif ptype in _TOOL_OUTPUT_TYPES:
                    call_id = str(payload.get("call_id", ""))
                    block = pending_tools.get(call_id)
                    if block is not None:
                        block.tool_output = _content_text(payload.get("output"))

        in_progress = False
        if last_line:
            try:
                json.loads(last_line)
            except json.JSONDecodeError:
                in_progress = True
        return SessionContent(messages=messages, in_progress=in_progress)

    @staticmethod
    def _tool_use_block(ptype: str, payload: dict) -> ContentBlock:
        """A TOOL_USE block from any of the Codex call payload shapes."""
        name = str(payload.get("name") or ptype)
        tool_input: dict = {}
        raw_args = payload.get("arguments")
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                tool_input = parsed if isinstance(parsed, dict) else {"arguments": raw_args}
            except json.JSONDecodeError:
                tool_input = {"arguments": raw_args}
        elif isinstance(payload.get("input"), str):
            tool_input = {"input": payload["input"]}
        elif isinstance(payload.get("action"), dict):
            tool_input = payload["action"]
        return ContentBlock(
            type=ContentType.TOOL_USE,
            tool_name=name,
            tool_input=tool_input,
            tool_id=str(payload.get("call_id") or payload.get("id") or ""),
        )

    def parse_session_insight(self, session_file: Path) -> SessionInsight:
        """One streaming pass: tokens, model, timing, prompts.

        Token attribution: Codex reports *cumulative* session totals in
        ``token_count`` events; the final total is attributed to the last model
        seen in ``turn_context`` (sessions rarely mix models — acceptable
        first-pass semantics, mirrors what the totals actually are).
        """
        insight = SessionInsight(session_id=session_file.stem, path=session_file)
        model = ""
        totals: dict | None = None
        last_turn: dict | None = None

        try:
            handle = open(session_file, "r", encoding="utf-8")
        except OSError:
            return insight

        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                ts = _parse_ts(data.get("timestamp"))
                if ts is not None:
                    if insight.first_ts is None:
                        insight.first_ts = ts
                    insight.last_ts = ts
                dtype = data.get("type")
                payload = data.get("payload")
                if not isinstance(payload, dict):
                    continue

                if dtype == "session_meta":
                    sid = payload.get("id") or payload.get("session_id")
                    if sid:
                        insight.session_id = str(sid)
                elif dtype == "turn_context":
                    value = payload.get("model")
                    if isinstance(value, str) and value:
                        model = value
                elif dtype == "event_msg":
                    etype = payload.get("type")
                    if etype == "token_count":
                        info = payload.get("info")
                        if isinstance(info, dict):
                            total = info.get("total_token_usage")
                            if isinstance(total, dict):
                                totals = total
                            last = info.get("last_token_usage")
                            if isinstance(last, dict):
                                last_turn = last
                elif dtype == "response_item" and payload.get("type") == "message":
                    role = payload.get("role")
                    text = _content_text(payload.get("content"))
                    if role == "user" and text and not _is_wrapper_text(text):
                        insight.message_count += 1
                        if not insight.first_prompt:
                            insight.first_prompt = text.strip()[:500]
                    elif role == "assistant" and text:
                        insight.message_count += 1
                        insight.last_assistant_text = text.strip()[:500]

        if totals:
            input_tokens = int(totals.get("input_tokens", 0) or 0)
            cached = int(totals.get("cached_input_tokens", 0) or 0)
            usage = TokenUsage(
                input=max(input_tokens - cached, 0),
                output=int(totals.get("output_tokens", 0) or 0),
                cache_creation=0,  # OpenAI caching has no write bucket
                cache_read=cached,
            )
            insight.usage_by_model[model or "codex-unknown"] = usage
        if last_turn:
            # Input side of the most recent turn = current context occupancy
            # (OpenAI input_tokens already include the cached share).
            insight.last_context_tokens = int(last_turn.get("input_tokens", 0) or 0)
        return insight
