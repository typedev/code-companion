"""Session model representing a Claude Code session."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class TokenUsage:
    """Token counts for one model within a session (Anthropic ``usage`` buckets)."""

    input: int = 0
    output: int = 0
    cache_creation: int = 0
    cache_read: int = 0

    @property
    def total(self) -> int:
        """Sum of all four usage buckets (input + output + cache write + cache read).

        Note: cache-read usually dominates for a long-running session (the whole
        context is re-read every turn), so this raw total runs far ahead of the
        real input/output volume. The $ estimate weights the buckets properly;
        this does not. Prefer the per-bucket breakdown when surfacing to users.
        """
        return self.input + self.output + self.cache_creation + self.cache_read

    def add(self, other: "TokenUsage") -> None:
        """Accumulate ``other`` into this bucket in place."""
        self.input += other.input
        self.output += other.output
        self.cache_creation += other.cache_creation
        self.cache_read += other.cache_read

    def to_dict(self) -> dict:
        return {
            "input": self.input,
            "output": self.output,
            "cache_creation": self.cache_creation,
            "cache_read": self.cache_read,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenUsage":
        return cls(
            input=int(data.get("input", 0)),
            output=int(data.get("output", 0)),
            cache_creation=int(data.get("cache_creation", 0)),
            cache_read=int(data.get("cache_read", 0)),
        )


@dataclass
class SessionInsight:
    """Derived, cacheable observability data for a single session (Phase 8.1).

    Extracted in one streaming pass over the JSONL. Token usage is bucketed
    **per model** because a session can mix models (main agent + subagents) and
    each has its own price, which the $ estimate (8.2) needs.
    """

    session_id: str
    path: Path
    usage_by_model: dict[str, TokenUsage] = field(default_factory=dict)
    files_touched: list[str] = field(default_factory=list)
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    first_prompt: str = ""
    last_assistant_text: str = ""
    message_count: int = 0
    # Input-side occupancy of the most recent assistant turn (input + cache read +
    # cache write). This is "how full is the context window right now", a different
    # quantity from the cumulative usage sums above (which are dominated by output +
    # per-turn cache). Used for the live context meter on PM cards.
    last_context_tokens: int = 0

    @property
    def models(self) -> list[str]:
        """Model ids seen in this session, sorted."""
        return sorted(self.usage_by_model)

    @property
    def total_tokens(self) -> int:
        """Total tokens across all models."""
        return sum(u.total for u in self.usage_by_model.values())

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "usage_by_model": {m: u.to_dict() for m, u in self.usage_by_model.items()},
            "files_touched": self.files_touched,
            "first_ts": self.first_ts.isoformat() if self.first_ts else None,
            "last_ts": self.last_ts.isoformat() if self.last_ts else None,
            "first_prompt": self.first_prompt,
            "last_assistant_text": self.last_assistant_text,
            "message_count": self.message_count,
            "last_context_tokens": self.last_context_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict, path: Path) -> "SessionInsight":
        def _ts(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None

        return cls(
            session_id=data.get("session_id", path.stem),
            path=path,
            usage_by_model={
                m: TokenUsage.from_dict(u)
                for m, u in (data.get("usage_by_model") or {}).items()
            },
            files_touched=list(data.get("files_touched") or []),
            first_ts=_ts(data.get("first_ts")),
            last_ts=_ts(data.get("last_ts")),
            first_prompt=data.get("first_prompt", ""),
            last_assistant_text=data.get("last_assistant_text", ""),
            message_count=int(data.get("message_count", 0)),
            last_context_tokens=int(data.get("last_context_tokens", 0)),
        )


@dataclass
class Session:
    """A Claude Code session parsed from JSONL file."""

    id: str  # Session UUID (filename without extension)
    path: Path  # Full path to JSONL file
    message_count: int = 0
    timestamp: datetime | None = None
    preview: str = ""  # First user message preview

    @property
    def display_date(self) -> str:
        """Return formatted date for display."""
        if self.timestamp is None:
            return "Unknown"
        return self.timestamp.strftime("%Y-%m-%d %H:%M")

    @property
    def short_preview(self) -> str:
        """Return truncated preview text."""
        max_len = 50
        if len(self.preview) <= max_len:
            return self.preview
        return self.preview[: max_len - 3] + "..."

    def __str__(self) -> str:
        return f"{self.display_date} ({self.message_count} msgs)"
