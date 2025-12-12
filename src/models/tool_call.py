"""Tool call model representing a tool invocation and its result."""

from dataclasses import dataclass
from datetime import datetime


# Tool name to icon mapping
TOOL_ICONS = {
    "Read": "document-open-symbolic",
    "Edit": "document-edit-symbolic",
    "Write": "document-new-symbolic",
    "Bash": "utilities-terminal-symbolic",
    "Glob": "folder-saved-search-symbolic",
    "Grep": "edit-find-symbolic",
    "Task": "system-run-symbolic",
    "WebFetch": "web-browser-symbolic",
    "WebSearch": "web-browser-symbolic",
    "TodoWrite": "view-list-symbolic",
    "NotebookEdit": "accessories-text-editor-symbolic",
}

DEFAULT_TOOL_ICON = "application-x-executable-symbolic"


@dataclass
class ToolCall:
    """A tool invocation with its input and result."""
    id: str
    name: str
    input: dict
    output: str = ""
    timestamp: datetime | None = None
    is_error: bool = False

    @property
    def icon_name(self) -> str:
        """Get the icon name for this tool."""
        return TOOL_ICONS.get(self.name, DEFAULT_TOOL_ICON)

    @property
    def display_subtitle(self) -> str:
        """Get a short description for display."""
        if self.name == "Read":
            return self.input.get("file_path", "")
        elif self.name in ("Edit", "Write"):
            return self.input.get("file_path", "")
        elif self.name == "Bash":
            cmd = self.input.get("command", "")
            if len(cmd) > 50:
                return cmd[:47] + "..."
            return cmd
        elif self.name in ("Glob", "Grep"):
            pattern = self.input.get("pattern", "")
            return pattern
        elif self.name == "Task":
            return self.input.get("description", "")
        elif self.name == "WebFetch":
            return self.input.get("url", "")
        elif self.name == "WebSearch":
            return self.input.get("query", "")
        return ""
