# Plan: Refactor to Code Companion

## Overview

Rename project from "Claude Companion" to "Code Companion" and abstract the history service to support multiple AI CLI tools in the future.

## Goals

1. Abstract `HistoryService` into interface + adapters
2. Create `ClaudeHistoryAdapter` for `~/.claude/`
3. Add AI provider selection in Settings (only "Claude Code" for now)
4. Rename project to "Code Companion"
5. Update all documentation

## Phase 1: Abstract History Service

### 1.1 Create base interface

**New file: `src/services/history_adapter.py`**

```python
from abc import ABC, abstractmethod
from pathlib import Path
from ..models import Session, Message

class HistoryAdapter(ABC):
    """Base interface for AI CLI history adapters."""

    # Adapter metadata
    name: str = "Unknown"
    cli_command: str = ""  # e.g., "claude", "gemini"
    config_dir: Path  # e.g., ~/.claude

    @abstractmethod
    def find_project_history_dir(self, project_path: Path) -> Path | None:
        """Find history directory for a project."""
        pass

    @abstractmethod
    def get_sessions_for_path(self, project_path: Path) -> list[Session]:
        """Get all sessions for a project path."""
        pass

    @abstractmethod
    def load_session_content(self, session: Session) -> list[Message]:
        """Load full session content."""
        pass

    @classmethod
    def is_available(cls) -> bool:
        """Check if this adapter's CLI tool is installed."""
        pass
```

### 1.2 Create Claude adapter

**New file: `src/services/adapters/claude_adapter.py`**

```python
from pathlib import Path
from ..history_adapter import HistoryAdapter
from ..history import HistoryService  # Reuse existing logic

class ClaudeHistoryAdapter(HistoryAdapter):
    name = "Claude Code"
    cli_command = "claude"

    def __init__(self):
        self.config_dir = Path.home() / ".claude"
        self._service = HistoryService(self.config_dir)

    # Delegate to existing HistoryService
    def find_project_history_dir(self, project_path: Path) -> Path | None:
        return self._service.find_project_history_dir(project_path)

    def get_sessions_for_path(self, project_path: Path) -> list[Session]:
        return self._service.get_sessions_for_path(project_path)

    def load_session_content(self, session: Session) -> list[Message]:
        return self._service.load_session_content(session)

    @classmethod
    def is_available(cls) -> bool:
        return (Path.home() / ".claude").exists()
```

### 1.3 Create adapter registry

**New file: `src/services/adapter_registry.py`**

```python
from .adapters.claude_adapter import ClaudeHistoryAdapter

# Register all available adapters
ADAPTERS = {
    "claude": ClaudeHistoryAdapter,
    # Future: "gemini": GeminiHistoryAdapter,
    # Future: "codex": CodexHistoryAdapter,
}

def get_adapter(name: str) -> HistoryAdapter:
    """Get adapter by name."""
    adapter_class = ADAPTERS.get(name)
    if adapter_class:
        return adapter_class()
    raise ValueError(f"Unknown adapter: {name}")

def get_available_adapters() -> list[str]:
    """Get list of available adapters."""
    return [name for name, cls in ADAPTERS.items() if cls.is_available()]
```

## Phase 2: Settings Integration

### 2.1 Add setting for AI provider

**Update `src/services/settings_service.py`:**

Add new setting:
```python
| `ai.provider` | `"claude"` | AI CLI provider (claude/gemini/codex) |
```

### 2.2 Add to Preferences dialog

**Update `src/widgets/preferences_dialog.py`:**

Add new row in Appearance or new "AI" page:
```python
# AI Provider dropdown
provider_row = Adw.ComboRow()
provider_row.set_title("AI Provider")
provider_row.set_subtitle("Select AI CLI tool")
# Options: Claude Code (more coming soon)
```

## Phase 3: Update Components

### 3.1 Update ClaudeHistoryPanel

**Rename to `AIHistoryPanel`** or keep name but use adapter:

```python
class ClaudeHistoryPanel(Gtk.Box):
    def __init__(self, project_path: str, ...):
        # Get adapter from settings
        provider = settings.get("ai.provider", "claude")
        self.adapter = get_adapter(provider)

        # Use adapter instead of HistoryService directly
        sessions = self.adapter.get_sessions_for_path(path)
```

### 3.2 Update SessionView

No changes needed - it works with generic `Session` and `Message` models.

### 3.3 Update Claude button in header

```python
def _on_claude_clicked(self):
    adapter = get_adapter(settings.get("ai.provider"))
    # Launch adapter.cli_command instead of hardcoded "claude"
    self._spawn_terminal(adapter.cli_command)
```

## Phase 4: Rename Project

### 4.1 Update project files

| File | Changes |
|------|---------|
| `pyproject.toml` | name = "code-companion" |
| `README.md` | Title, description |
| `CLAUDE.md` | Project name references |
| `INSTALL.md` | App name, paths |
| `src/project_manager.py` | About dialog, window title |
| `src/project_window.py` | Window title |
| `data/code-companion.desktop` | Rename from claude-companion |
| `bin/code-companion` | Rename launcher |

### 4.2 Update paths

| Old | New |
|-----|-----|
| `~/.config/claude-companion/` | `~/.config/code-companion/` |
| `/tmp/claude-companion-locks/` | `/tmp/code-companion-locks/` |
| `claude-companion.desktop` | `code-companion.desktop` |

### 4.3 Migration

Add migration logic for existing users:
```python
def migrate_config():
    old_dir = Path.home() / ".config/claude-companion"
    new_dir = Path.home() / ".config/code-companion"
    if old_dir.exists() and not new_dir.exists():
        old_dir.rename(new_dir)
```

## Phase 5: Documentation

- [ ] Update README.md with new name and features
- [ ] Update CLAUDE.md
- [ ] Update INSTALL.md
- [ ] Update About dialog credits

## File Structure After Refactor

```
src/services/
├── __init__.py
├── history.py              # Keep as internal implementation
├── history_adapter.py      # NEW: Base interface
├── adapter_registry.py     # NEW: Adapter registration
├── adapters/               # NEW: Adapter implementations
│   ├── __init__.py
│   └── claude_adapter.py
├── settings_service.py     # Updated with ai.provider
└── ...
```

## Implementation Order

1. [ ] Create `history_adapter.py` interface
2. [ ] Create `adapters/claude_adapter.py`
3. [ ] Create `adapter_registry.py`
4. [ ] Add `ai.provider` setting
5. [ ] Update `ClaudeHistoryPanel` to use adapter
6. [ ] Update Claude button to use adapter
7. [ ] Add provider selection to Preferences
8. [ ] Rename project files and paths
9. [ ] Add config migration
10. [ ] Update all documentation

## Future Adapters (not in scope)

When Gemini CLI or Codex CLI become available:

```python
class GeminiHistoryAdapter(HistoryAdapter):
    name = "Gemini CLI"
    cli_command = "gemini"
    config_dir = Path.home() / ".gemini"
    # ... implement methods

class CodexHistoryAdapter(HistoryAdapter):
    name = "Codex CLI"
    cli_command = "codex"
    config_dir = Path.home() / ".codex"
    # ... implement methods
```

## Notes

- Keep existing `HistoryService` as internal implementation detail
- `ClaudeHistoryAdapter` wraps `HistoryService` for backward compatibility
- Session and Message models remain unchanged
- UI components work with abstract adapter interface
