"""Cross-project search over Claude user prompts (Phase 8.5).

Searches every session JSONL under ``~/.claude/projects/`` for user prompts
containing a query, using ripgrep (grep fallback). Matches are confirmed against
the parsed *user* message text (not arbitrary JSON fields) and returned grouped-
friendly, newest first, so the Project Manager can offer "where did I ask about X".
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..utils import claude_paths
from ..utils.paths import decode_project_path

_SNIPPET_MAX = 200

# Text prefixes of harness-injected "user" events that aren't real prompts
# (slash-command expansions, task notifications, compaction continuations, hooks).
_INJECTED_PREFIXES = (
    "<task-notification", "<command-name", "<command-message", "<command-args",
    "<local-command", "<system-reminder", "<user-prompt-submit-hook",
    "caveat: the messages below",
    "this session is being continued from a previous conversation",
)


def _is_injected(event: dict, text: str) -> bool:
    """True for harness/system-injected user events (not something the user typed)."""
    if (event.get("isMeta") or event.get("isCompactSummary")
            or event.get("isVisibleInTranscriptOnly")):
        return True
    return text.lstrip()[:60].lower().startswith(_INJECTED_PREFIXES)


@dataclass
class PromptHit:
    project_path: str      # the session's cwd (real local path)
    project_name: str      # basename of project_path
    session_id: str        # jsonl stem
    session_file: str
    snippet: str           # the user prompt text (trimmed)
    timestamp: datetime | None


def _extract_user_text(event: dict) -> str:
    content = event.get("message", {}).get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return (block.get("text") or "").strip()
    return ""


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _command(query: str, root: Path) -> list[str]:
    """A ripgrep (or grep) command listing matching JSONL lines with filenames."""
    if shutil.which("rg"):
        return [
            "rg", "--no-heading", "--with-filename", "--color=never",
            "--ignore-case", "--fixed-strings", "--max-count=50",
            "--glob", "*.jsonl", query, str(root),
        ]
    return ["grep", "-rHi", "-m", "50", "--include=*.jsonl", query, str(root)]


def search_prompts(query: str, limit: int = 300) -> list[PromptHit]:
    """User prompts across all projects containing ``query`` (newest first)."""
    query = query.strip()
    if len(query) < 2:
        return []
    root = claude_paths.projects_root()
    if not root.exists():
        return []

    try:
        result = subprocess.run(
            _command(query, root), capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return []

    needle = query.lower()
    hits: list[PromptHit] = []
    seen: set[tuple[str, str]] = set()  # (session_id, snippet) de-dupe
    for line in result.stdout.splitlines():
        path, sep, payload = line.partition(":")
        if not sep or not path.endswith(".jsonl"):
            continue
        try:
            event = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "user":
            continue
        text = _extract_user_text(event)
        if not text or needle not in text.lower():
            continue
        if _is_injected(event, text):
            continue

        session_file = path
        session_id = Path(path).stem
        snippet = " ".join(text.split())[:_SNIPPET_MAX]
        key = (session_id, snippet)
        if key in seen:
            continue
        seen.add(key)

        cwd = event.get("cwd")
        if not cwd:
            cwd = decode_project_path(Path(path).parent.name)
        hits.append(PromptHit(
            project_path=cwd,
            project_name=Path(cwd).name,
            session_id=session_id,
            session_file=session_file,
            snippet=snippet,
            timestamp=_parse_ts(event.get("timestamp")),
        ))
        if len(hits) >= limit:
            break

    hits.sort(key=lambda h: h.timestamp or datetime.min, reverse=True)
    return hits
