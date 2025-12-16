"""Message model representing a conversation message."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MessageRole(Enum):
    """Role of the message sender."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"  # System events like conversation compaction


class ContentType(Enum):
    """Type of content block in a message."""
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"  # System notifications (compaction, etc.)


@dataclass
class ContentBlock:
    """A single block of content within a message."""
    type: ContentType
    text: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    tool_id: str = ""
    tool_output: str = ""  # Result from tool execution
    tool_is_error: bool = False  # Whether the tool call resulted in an error


@dataclass
class Message:
    """A conversation message with its content blocks."""
    role: MessageRole
    timestamp: datetime | None = None
    content_blocks: list[ContentBlock] = field(default_factory=list)

    @property
    def text_content(self) -> str:
        """Get combined text content from all text blocks."""
        texts = [
            block.text
            for block in self.content_blocks
            if block.type == ContentType.TEXT
        ]
        return "\n".join(texts)

    @property
    def has_thinking(self) -> bool:
        """Check if message contains thinking blocks."""
        return any(
            block.type == ContentType.THINKING
            for block in self.content_blocks
        )

    @property
    def thinking_content(self) -> str:
        """Get combined thinking content."""
        thoughts = [
            block.text
            for block in self.content_blocks
            if block.type == ContentType.THINKING
        ]
        return "\n".join(thoughts)

    @property
    def tool_uses(self) -> list[ContentBlock]:
        """Get all tool use blocks."""
        return [
            block
            for block in self.content_blocks
            if block.type == ContentType.TOOL_USE
        ]
