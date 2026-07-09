# Claude Companion

> **⚠️ Historical document.** This is the original design vision from before the project was
> renamed to **Code Companion** and grew a multi-provider adapter architecture, an MCP control
> surface, a tmux session supervisor, cross-machine sync, and the cross-project coordination hub.
> It is kept for context only. For the current state see **[README.md](README.md)** and
> **[CLAUDE.md](CLAUDE.md)**; for the architecture and milestones, CLAUDE.md; for design history,
> the `docs/` plans (indexed in [docs/README.md](docs/README.md)).

A native GTK4/libadwaita application for working with Claude Code — session history viewer, active session management, Git integration, and project notes.

## Problem

Claude Code is a powerful CLI tool, but working with it in a terminal has limitations:

- Session history is inconvenient to navigate (only `claude --resume` and Ctrl+R)
- Thinking blocks, tool calls, and diffs merge into a stream of text
- No way to quickly see which files were modified
- No Git integration for viewing changes
- No place to keep notes and TODOs for the project

## Solution

A desktop application that:

1. Reads and displays Claude Code session history
2. Structures output: messages, thinking, tool calls as separate visual blocks
3. Shows diffs of modified files
4. Allows launching new sessions with a convenient UI
5. Maintains TODO notes linked to projects

## Tech Stack

- **Language:** Python 3.11+
- **UI Framework:** GTK4 + libadwaita
- **Code highlighting:** GtkSourceView 5
- **Git:** libgit2-glib or subprocess
- **Build system:** Meson
- **Package format:** Flatpak (optional)

## Claude Code Data

### File Locations

```
~/.claude/
├── projects/                    # History by project
│   └── [encoded-path]/          # Project path encoded (/ → -)
│       ├── [session-uuid].jsonl # Full session history
│       └── [summary-uuid].jsonl # Session summaries
├── history.jsonl                # Global session index
├── settings.json                # User settings
└── todos/                       # Claude Code TODOs
```

### Session JSONL Format

Each line is a JSON object with an event:

```jsonl
{"type":"user","message":{"role":"user","content":"Fix the login bug"},"timestamp":"2025-01-15T10:30:00.000Z"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I'll analyze..."}]},"timestamp":"2025-01-15T10:30:05.000Z"}
{"type":"tool_use","tool":"Read","input":{"file_path":"src/auth.py"},"timestamp":"..."}
{"type":"tool_result","output":"...file contents...","timestamp":"..."}
{"type":"tool_use","tool":"Edit","input":{"file_path":"src/auth.py","old_string":"...","new_string":"..."},"timestamp":"..."}
```

### Event Types for Parsing

| type | Description |
|------|-------------|
| `user` | User message |
| `assistant` | Claude response (may contain text, thinking) |
| `tool_use` | Tool invocation (Read, Write, Edit, Bash, Glob, Grep, etc.) |
| `tool_result` | Tool execution result |

### assistant.content Fields

```json
{
  "content": [
    {"type": "thinking", "thinking": "Let me analyze..."},
    {"type": "text", "text": "I found the issue..."},
    {"type": "tool_use", "id": "...", "name": "Edit", "input": {...}}
  ]
}
```

## Application Architecture

```
claude-companion/
├── src/
│   ├── main.py                 # Entry point
│   ├── application.py          # Gtk.Application subclass
│   ├── window.py               # Main window
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── project.py          # Project model
│   │   ├── session.py          # Session model, JSONL parsing
│   │   ├── message.py          # Base message class
│   │   └── tool_call.py        # Models for tool_use/tool_result
│   │
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── sidebar.py          # Project and session list
│   │   ├── session_view.py     # Session display
│   │   ├── message_row.py      # Single message widget
│   │   ├── thinking_block.py   # Collapsible thinking block
│   │   ├── tool_call_card.py   # Tool call card
│   │   ├── diff_view.py        # Diff viewer
│   │   ├── code_view.py        # Code view with highlighting
│   │   └── todo_panel.py       # TODO panel
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── history_service.py  # Reading ~/.claude/
│   │   ├── claude_runner.py    # Running claude CLI
│   │   ├── git_service.py      # Git operations
│   │   └── todo_service.py     # TODO storage
│   │
│   └── utils/
│       ├── __init__.py
│       └── path_encoder.py     # Path encoding like Claude Code
│
├── data/
│   ├── com.github.user.ClaudeCompanion.desktop
│   ├── com.github.user.ClaudeCompanion.metainfo.xml
│   ├── com.github.user.ClaudeCompanion.gschema.xml
│   ├── icons/
│   │   └── hicolor/
│   │       └── scalable/apps/
│   │           └── com.github.user.ClaudeCompanion.svg
│   └── styles/
│       └── style.css
│
├── po/                         # Translations (optional)
├── meson.build
├── meson_options.txt
└── README.md
```

## UI/UX Specification

### Main Window

```
┌─────────────────────────────────────────────────────────────────────┐
│  Claude Companion                                    [−] [□] [×]    │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────┐  ┌──────────────────────────────────────────┐ │
│  │ 🔍 Search...     │  │ 📁 my-project                            │ │
│  ├──────────────────┤  │ Session: Dec 6, 2025 14:30               │ │
│  │                  │  ├──────────────────────────────────────────┤ │
│  │ PROJECTS         │  │                                          │ │
│  │                  │  │ ┌────────────────────────────────────┐   │ │
│  │ 📁 my-project    │  │ │ 👤 User                            │   │ │
│  │    Today (2)     │  │ │ Fix the authentication bug in the │   │ │
│  │    Yesterday (5) │  │ │ login form                         │   │ │
│  │                  │  │ └────────────────────────────────────┘   │ │
│  │ 📁 font-tools    │  │                                          │ │
│  │    Dec 4 (3)     │  │ ┌────────────────────────────────────┐   │ │
│  │                  │  │ │ 🤖 Claude              ▼ Thinking  │   │ │
│  │ 📁 api-server    │  │ │                                    │   │ │
│  │    Dec 1 (1)     │  │ │ I'll analyze the auth module...    │   │ │
│  │                  │  │ └────────────────────────────────────┘   │ │
│  ├──────────────────┤  │                                          │ │
│  │                  │  │ ┌────────────────────────────────────┐   │ │
│  │ 📝 TODO (3)      │  │ │ 📖 Read: src/auth.py               │   │ │
│  │                  │  │ │ [View File]                        │   │ │
│  │ ☐ Refactor API   │  │ └────────────────────────────────────┘   │ │
│  │ ☐ Add tests      │  │                                          │ │
│  │ ☐ Update docs    │  │ ┌────────────────────────────────────┐   │ │
│  │                  │  │ │ ✏️ Edit: src/auth.py                │   │ │
│  │ [+ Add TODO]     │  │ │ [View Diff] [Open File]            │   │ │
│  │                  │  │ └────────────────────────────────────┘   │ │
│  └──────────────────┘  │                                          │ │
│                        ├──────────────────────────────────────────┤ │
│                        │ 💬 Message input...              [Send]  │ │
│                        └──────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### libadwaita Components

| Component | libadwaita/GTK4 Widget |
|-----------|------------------------|
| Main layout | `Adw.NavigationSplitView` |
| Sidebar | `Adw.NavigationPage` + `Gtk.ListView` |
| Project list | `Adw.ExpanderRow` |
| Session | `Gtk.ScrolledWindow` + `Gtk.Box` |
| Message | `Adw.PreferencesGroup` or custom widget |
| Thinking block | `Adw.ExpanderRow` |
| Tool call | `Adw.ActionRow` with buttons |
| Diff view | `GtkSourceView` with diff language |
| TODO list | `Gtk.ListBox` + `Adw.ActionRow` |
| Input | `Adw.EntryRow` or `Gtk.TextView` |

### Color Scheme (follows system theme)

- User messages: standard background
- Claude messages: `@card_bg_color`
- Thinking: muted, `@dim_label_color`
- Tool calls: colored icons by type
  - Read: blue
  - Edit/Write: orange
  - Bash: green
  - Error: red

## MVP Roadmap

### v0.1 — History Viewer

- [ ] GTK4/libadwaita application skeleton
- [ ] JSONL session file parsing
- [ ] Project list in sidebar
- [ ] Session list per project
- [ ] Message display (user/assistant)
- [ ] Code highlighting in messages (GtkSourceView)

### v0.2 — Tool Calls and Details

- [ ] Tool calls displayed as cards
- [ ] Collapsible thinking blocks
- [ ] View contents of Read operations
- [ ] View diff for Edit operations
- [ ] "Open in Editor" button (xdg-open)

### v0.3 — Active Session

- [ ] Launch new Claude Code session
- [ ] Parse stream-json in real-time
- [ ] Message input field
- [ ] Stop/pause session

### v0.4 — Git Integration

- [ ] Show current branch
- [ ] List of files modified during session
- [ ] Full diff viewer
- [ ] Open in file manager button

### v0.5 — TODO and Notes

- [ ] TODO panel in sidebar
- [ ] Add/remove/check TODO items
- [ ] Link TODO to project
- [ ] Store in local JSON file

### v0.6 — Polish

- [ ] History search
- [ ] Settings (editor path, theme)
- [ ] Desktop file and icon
- [ ] Flatpak manifest

### v1.0 — Multi-Agent Orchestration

- [ ] Multiple agent sessions in one window
- [ ] Git worktree creation per agent
- [ ] Branch management UI
- [ ] Conflict detection and preview
- [ ] Merge workflow with resolution options
- [ ] "Ask Claude to merge" meta-agent

### v0.7 — Multi-Agent Orchestration (Future)

- [ ] Launch multiple agents for one project
- [ ] Git worktree creation per agent
- [ ] File lock registry and conflict detection
- [ ] Agent queue management
- [ ] Merge preview UI
- [ ] Conflict resolution (manual + Claude-assisted)

See [Multi-Agent Orchestration](#multi-agent-orchestration-v07) section for detailed architecture.

## Running Claude Code from the Application

### For a New Session

```python
import subprocess
import json

process = subprocess.Popen(
    ["claude", "-p", prompt, "--output-format", "stream-json"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd=project_path,
    text=True
)

for line in process.stdout:
    event = json.loads(line)
    # Process event and update UI
```

### For Resuming a Session

```python
subprocess.Popen(
    ["claude", "--resume", session_id, "--output-format", "stream-json"],
    ...
)
```

### Async in GTK

Use `GLib.io_add_watch()` or `Gio.Subprocess` for non-blocking stdout reading:

```python
from gi.repository import Gio, GLib

def on_stdout_ready(source, result, user_data):
    line = source.read_line_finish(result)
    if line:
        event = json.loads(line)
        # Update UI in main thread
        GLib.idle_add(update_ui, event)
        # Read next line
        source.read_line_async(GLib.PRIORITY_DEFAULT, None, on_stdout_ready, None)

subprocess = Gio.Subprocess.new(
    ["claude", "-p", prompt, "--output-format", "stream-json"],
    Gio.SubprocessFlags.STDOUT_PIPE
)
stdout = subprocess.get_stdout_pipe()
data_stream = Gio.DataInputStream.new(stdout)
data_stream.read_line_async(GLib.PRIORITY_DEFAULT, None, on_stdout_ready, None)
```

## TODO Storage

File: `~/.local/share/claude-companion/todos.json`

```json
{
  "projects": {
    "/home/user/my-project": {
      "todos": [
        {
          "id": "uuid",
          "text": "Refactor API endpoints",
          "done": false,
          "created": "2025-01-15T10:00:00Z",
          "session_id": "abc-123"
        }
      ]
    }
  }
}
```

## Multi-Agent Orchestration (v1.0+)

This section describes the architecture for running multiple Claude Code agents in parallel on the same project.

### Problem: File Conflicts

When multiple agents work on the same codebase, several conflict types can occur:

1. **Write-Write Conflict**: Two agents modify the same file simultaneously
2. **Stale Read**: Agent 1 modifies file, Agent 2 reads old version, makes incompatible changes
3. **Semantic Conflict**: Agents modify different files but create incompatible changes

### Solution: Git Worktrees

Each agent operates in an isolated Git worktree with its own branch:

```
my-project/                     # Main working directory
├── .git/
├── src/
└── ...

my-project-agent-1/             # Worktree for Agent 1
├── src/                        # Independent copy
└── ...

my-project-agent-2/             # Worktree for Agent 2
├── src/
└── ...
```

### Worktree Lifecycle

```
1. User creates new agent
   │
   ▼
2. App creates worktree + branch
   $ git worktree add ../project-agent-1 -b agent-1/task-name
   │
   ▼
3. Agent works in isolated directory
   claude -p "..." --cwd ../project-agent-1
   │
   ▼
4. Agent completes task
   │
   ▼
5. App shows merge preview
   - Changed files
   - Potential conflicts
   - Diff preview
   │
   ▼
6. User approves merge
   $ git checkout main
   $ git merge agent-1/task-name
   │
   ▼
7. Cleanup worktree
   $ git worktree remove ../project-agent-1
   $ git branch -d agent-1/task-name
```

### Orchestrator UI

```
┌─────────────────────────────────────────────────────────────────────┐
│  Orchestrator: my-project                          [+ New Agent]    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Base: main (abc1234)                                              │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ 🟢 Agent 1: API Refactoring                        ⏸️ ⏹️ 🔀   │ │
│  │ Branch: agent-1/api-refactor                                  │ │
│  │ Status: Working • 15 min elapsed                              │ │
│  │ Changes: +142 −89 lines (3 files)                             │ │
│  │ Files: src/api/auth.py, users.py, validators.py               │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ 🟢 Agent 2: Write Tests                            ⏸️ ⏹️ 🔀   │ │
│  │ Branch: agent-2/tests                                         │ │
│  │ Status: Working • 8 min elapsed                               │ │
│  │ Changes: +340 −12 lines (2 files)                             │ │
│  │ Files: tests/test_auth.py, tests/test_users.py                │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ ⚠️  Potential Conflicts Detected                               │ │
│  │                                                                │ │
│  │ src/api/auth.py modified by: Agent 1, Agent 2                 │ │
│  │                                                                │ │
│  │ [Preview Changes] [Preview Merge]                              │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  [Merge All] [Merge Selected...] [Refresh Status]                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Merge Preview Dialog

```
┌─────────────────────────────────────────────────────────────────────┐
│  Merge Preview                                              [×]     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Merging: agent-1/api-refactor → main                              │
│                                                                     │
│  Status: ⚠️  1 conflict detected                                    │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ 📄 src/api/auth.py                              ⚠️ CONFLICT   │ │
│  ├───────────────────────────────────────────────────────────────┤ │
│  │                                                               │ │
│  │  def validate_token(token: str):                              │ │
│  │ <<<<<<< main                                                  │ │
│  │      return jwt.decode(token, SECRET)                         │ │
│  │ =======                                                       │ │
│  │      # Added expiry check                                     │ │
│  │      payload = jwt.decode(token, SECRET)                      │ │
│  │      if payload['exp'] < time.time():                         │ │
│  │          raise TokenExpired()                                 │ │
│  │      return payload                                           │ │
│  │ >>>>>>> agent-1/api-refactor                                  │ │
│  │                                                               │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  Resolution:                                                        │
│  ○ Keep main version                                               │
│  ○ Keep agent version                                              │
│  ○ Edit manually                                                   │
│  ● Ask Claude to resolve                                           │
│                                                                     │
│                                    [Cancel] [Resolve & Merge]       │
└─────────────────────────────────────────────────────────────────────┘
```

### Conflict Resolution Agent

When "Ask Claude to resolve" is selected, spawn a special merge agent:

```python
MERGE_AGENT_PROMPT = """
You are a merge conflict resolver. Two development efforts have created 
conflicting changes to the same file.

## Original file (main branch):
```{language}
{original_content}
```

## Changes from Agent 1 ({agent1_task}):
```diff
{agent1_diff}
```

## Changes from Agent 2 ({agent2_task}):
```diff  
{agent2_diff}
```

## Your task:
1. Understand the intent of both changes
2. Create a merged version that preserves both intents
3. Ensure the result is syntactically correct
4. If changes are truly incompatible, explain why and suggest alternatives

Output the complete merged file content.
"""
```

### Data Model for Orchestration

```python
# src/models/orchestrator.py
from dataclasses import dataclass
from pathlib import Path
from enum import Enum
from datetime import datetime

class AgentStatus(Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_MERGE = "waiting_merge"

@dataclass
class AgentInstance:
    id: str
    name: str
    task_description: str
    status: AgentStatus
    
    # Git info
    branch_name: str
    worktree_path: Path
    base_commit: str
    
    # Session info
    session_id: str | None
    started_at: datetime
    
    # Changes tracking
    modified_files: list[str]
    added_lines: int
    removed_lines: int

@dataclass
class MergeConflict:
    file_path: str
    agent_ids: list[str]
    conflict_markers: str  # The actual conflict content
    
@dataclass
class Orchestrator:
    project_path: Path
    base_branch: str
    agents: list[AgentInstance]
    conflicts: list[MergeConflict]
    
    def create_agent(self, name: str, task: str) -> AgentInstance:
        """Create new worktree and agent instance."""
        pass
    
    def detect_conflicts(self) -> list[MergeConflict]:
        """Check for potential conflicts between agent branches."""
        pass
    
    def merge_agent(self, agent_id: str, resolve_conflicts: bool = False):
        """Merge agent's branch into base."""
        pass
    
    def cleanup_agent(self, agent_id: str):
        """Remove worktree and optionally delete branch."""
        pass
```

### Git Service Extensions

```python
# src/services/git_service.py (extended for orchestration)

class GitService:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
    
    def create_worktree(self, worktree_path: Path, branch_name: str) -> bool:
        """Create a new worktree with a new branch."""
        result = subprocess.run(
            ["git", "worktree", "add", str(worktree_path), "-b", branch_name],
            cwd=self.repo_path,
            capture_output=True
        )
        return result.returncode == 0
    
    def remove_worktree(self, worktree_path: Path):
        """Remove a worktree."""
        subprocess.run(
            ["git", "worktree", "remove", str(worktree_path)],
            cwd=self.repo_path
        )
    
    def get_branch_diff(self, branch: str, base: str = "main") -> dict:
        """Get diff statistics between branch and base."""
        result = subprocess.run(
            ["git", "diff", "--stat", f"{base}...{branch}"],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        return self._parse_diff_stat(result.stdout)
    
    def check_merge_conflicts(self, branch: str, base: str = "main") -> list[str]:
        """Check if merging branch would cause conflicts."""
        # Dry-run merge
        result = subprocess.run(
            ["git", "merge", "--no-commit", "--no-ff", branch],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        
        # Abort the merge
        subprocess.run(["git", "merge", "--abort"], cwd=self.repo_path)
        
        if result.returncode != 0:
            return self._parse_conflict_files(result.stderr)
        return []
    
    def get_modified_files(self, branch: str, base: str = "main") -> list[str]:
        """Get list of files modified in branch compared to base."""
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{branch}"],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        return result.stdout.strip().split('\n') if result.stdout.strip() else []
```

### Multiple Instances vs Single Window

For MVP orchestration, support both approaches:

**Option A: Multiple app instances (simple)**
- User opens multiple windows manually
- Each window = one agent
- No coordination, user manages conflicts via Git

**Option B: Single window orchestrator (advanced)**
- One window manages multiple agents
- Built-in conflict detection
- Integrated merge workflow

Recommend starting with Option A for v0.x, then adding Option B in v1.0.

## Project Path Encoding

Claude Code encodes project paths by replacing `/` with `-`:

```python
def encode_project_path(path: str) -> str:
    """Encode project path as Claude Code does."""
    # /home/user/my-project → -home-user-my-project
    return path.replace("/", "-")

def decode_project_path(encoded: str) -> str:
    """Decode project path."""
    # -home-user-my-project → /home/user/my-project
    if encoded.startswith("-"):
        return encoded.replace("-", "/")
    return encoded
```

## Dependencies

### Runtime

```
python >= 3.11
gtk4 >= 4.12
libadwaita-1 >= 1.4
gtksourceview5 >= 5.10
pygobject >= 3.46
```

### Build

```
meson >= 1.0
ninja
```

### Fedora Installation

```bash
sudo dnf install gtk4-devel libadwaita-devel gtksourceview5-devel python3-gobject meson ninja-build
```

## Code Examples

### Session Parsing

```python
# src/models/session.py
from dataclasses import dataclass
from pathlib import Path
import json
from datetime import datetime
from typing import Iterator

@dataclass
class Message:
    type: str  # user, assistant, tool_use, tool_result
    content: dict
    timestamp: datetime
    
@dataclass  
class Session:
    id: str
    path: Path
    messages: list[Message]
    
    @classmethod
    def from_jsonl(cls, path: Path) -> "Session":
        messages = []
        with open(path) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    msg = Message(
                        type=data.get("type", "unknown"),
                        content=data.get("message", data),
                        timestamp=datetime.fromisoformat(
                            data.get("timestamp", "").replace("Z", "+00:00")
                        ) if data.get("timestamp") else None
                    )
                    messages.append(msg)
        
        return cls(
            id=path.stem,
            path=path,
            messages=messages
        )
```

### Application Skeleton

```python
# src/main.py
import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio
from .window import ClaudeCompanionWindow

class ClaudeCompanionApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.github.user.ClaudeCompanion",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        
    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = ClaudeCompanionWindow(application=self)
        win.present()

def main():
    app = ClaudeCompanionApp()
    return app.run(sys.argv)

if __name__ == "__main__":
    sys.exit(main())
```

```python
# src/window.py
from gi.repository import Gtk, Adw

class ClaudeCompanionWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        self.set_title("Claude Companion")
        self.set_default_size(1200, 800)
        
        # Main layout
        split_view = Adw.NavigationSplitView()
        
        # Sidebar
        sidebar = self._create_sidebar()
        split_view.set_sidebar(sidebar)
        
        # Content
        content = self._create_content()
        split_view.set_content(content)
        
        self.set_content(split_view)
    
    def _create_sidebar(self) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Search
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search sessions...")
        box.append(search)
        
        # Projects list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scrolled.set_child(listbox)
        box.append(scrolled)
        
        page = Adw.NavigationPage()
        page.set_title("Projects")
        page.set_child(box)
        
        return page
    
    def _create_content(self) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Session header
        header = Adw.HeaderBar()
        box.append(header)
        
        # Messages area
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        
        messages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        messages_box.set_margin_start(12)
        messages_box.set_margin_end(12)
        messages_box.set_margin_top(12)
        messages_box.set_margin_bottom(12)
        
        scrolled.set_child(messages_box)
        box.append(scrolled)
        
        # Input area
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        input_box.set_margin_start(12)
        input_box.set_margin_end(12)
        input_box.set_margin_bottom(12)
        
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_placeholder_text("Message Claude...")
        input_box.append(entry)
        
        send_btn = Gtk.Button(label="Send")
        send_btn.add_css_class("suggested-action")
        input_box.append(send_btn)
        
        box.append(input_box)
        
        page = Adw.NavigationPage()
        page.set_title("Session")
        page.set_child(box)
        
        return page
```

## References

- [Claude Code CLI Reference](https://docs.anthropic.com/en/docs/claude-code/cli-reference)
- [GTK4 Documentation](https://docs.gtk.org/gtk4/)
- [libadwaita Documentation](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/)
- [GtkSourceView 5](https://gnome.pages.gitlab.gnome.org/gtksourceview/gtksourceview5/)
- [Python GTK4 Tutorial](https://pygobject.gnome.org/tutorials/gtk4/index.html)
- [claude-code-history-viewer](https://github.com/jhlee0409/claude-code-history-viewer) — Tauri reference
- [claude-conversation-extractor](https://github.com/ZeroSumQuant/claude-conversation-extractor) — JSONL parsing

---

## Multi-Agent Orchestration (v0.7+)

This section describes the architecture for running multiple Claude Code agents in parallel on the same project.

### Problem Statement

When multiple agents work on the same codebase simultaneously, several issues arise:

1. **Direct conflicts**: Two agents try to modify the same file at the same time
2. **Stale read conflicts**: Agent 1 reads file, Agent 2 modifies it, Agent 1 writes based on outdated version
3. **Semantic conflicts**: Agents make incompatible changes to different files (e.g., API signature change vs. API usage)

### Recommended Approach: Git Worktrees

Each agent operates in its own Git worktree (isolated working directory with separate branch):

```
my-project/                     # main worktree
├── .git/
├── src/
└── ...

my-project-agent-1/             # agent 1 worktree  
├── src/                        # branch: agent-1/api-refactor
└── ...

my-project-agent-2/             # agent 2 worktree
├── src/                        # branch: agent-2/add-tests
└── ...
```

**Benefits:**
- Complete isolation — no runtime conflicts
- Standard Git workflow — merge, rebase, cherry-pick
- Easy rollback — just delete branch
- Conflict detection at merge time
- Familiar to developers

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Claude Companion                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐     ┌─────────────────────────────────┐   │
│  │  Orchestrator   │────▶│         Agent Manager           │   │
│  │    Service      │     │                                 │   │
│  │                 │     │  ┌───────┐ ┌───────┐ ┌───────┐ │   │
│  │ - Create agents │     │  │Agent 1│ │Agent 2│ │Agent 3│ │   │
│  │ - Manage queue  │     │  │       │ │       │ │       │ │   │
│  │ - Track locks   │     │  │Claude │ │Claude │ │Claude │ │   │
│  │ - Merge control │     │  │Process│ │Process│ │Process│ │   │
│  └─────────────────┘     │  └───┬───┘ └───┬───┘ └───┬───┘ │   │
│           │              │      │         │         │      │   │
│           ▼              └──────┼─────────┼─────────┼──────┘   │
│  ┌─────────────────┐            │         │         │          │
│  │  Git Service    │◀───────────┴─────────┴─────────┘          │
│  │                 │                                            │
│  │ - Worktrees     │     ┌─────────────────────────────────┐   │
│  │ - Branches      │────▶│           File System           │   │
│  │ - Merge/Diff    │     │                                 │   │
│  └─────────────────┘     │  ~/.worktrees/                  │   │
│                          │  ├── my-project-agent-1/        │   │
│                          │  ├── my-project-agent-2/        │   │
│                          │  └── my-project-agent-3/        │   │
│                          └─────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Data Models

```python
# src/models/orchestrator.py
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from datetime import datetime

class AgentStatus(Enum):
    PLANNING = "planning"      # Agent outputting file plan
    WAITING = "waiting"        # Waiting for lock / in queue
    WORKING = "working"        # Actively processing
    PAUSED = "paused"          # User paused
    COMPLETED = "completed"    # Task done
    FAILED = "failed"          # Error occurred
    
@dataclass
class Agent:
    id: str
    name: str                  # User-friendly name, e.g., "API Refactoring"
    task: str                  # Original user prompt
    status: AgentStatus
    worktree_path: Path
    branch_name: str
    session_id: str            # Claude Code session ID
    created_at: datetime
    files_modified: list[str]
    
@dataclass
class FileLock:
    file_path: str
    agent_id: str
    locked_at: datetime
    lock_type: str             # "read" or "write"
    
@dataclass
class MergeConflict:
    file_path: str
    agent_1_id: str
    agent_2_id: str
    base_content: str
    agent_1_content: str
    agent_2_content: str
    
@dataclass
class OrchestratorState:
    project_path: Path
    agents: list[Agent]
    locks: list[FileLock]
    queue: list[str]           # Agent IDs waiting
    conflicts: list[MergeConflict]
```

### Orchestrator Service

```python
# src/services/orchestrator_service.py
from pathlib import Path
import subprocess
from .git_service import GitService

class OrchestratorService:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.git = GitService(project_path)
        self.agents: dict[str, Agent] = {}
        self.state_file = Path.home() / ".local/share/claude-companion/orchestrator.json"
        
    def create_agent(self, name: str, task: str) -> Agent:
        """Create a new agent with its own worktree and branch."""
        agent_id = str(uuid.uuid4())[:8]
        branch_name = f"agent-{agent_id}/{slugify(name)}"
        
        # Create branch from current HEAD
        self.git.create_branch(branch_name)
        
        # Create worktree
        worktree_path = Path.home() / ".worktrees" / f"{self.project_path.name}-{agent_id}"
        self.git.create_worktree(worktree_path, branch_name)
        
        agent = Agent(
            id=agent_id,
            name=name,
            task=task,
            status=AgentStatus.PLANNING,
            worktree_path=worktree_path,
            branch_name=branch_name,
            session_id=None,
            created_at=datetime.now(),
            files_modified=[]
        )
        
        self.agents[agent_id] = agent
        self._save_state()
        
        return agent
        
    def start_agent(self, agent_id: str) -> None:
        """Start Claude Code process for agent."""
        agent = self.agents[agent_id]
        
        # Launch claude in agent's worktree
        process = subprocess.Popen(
            ["claude", "-p", agent.task, "--output-format", "stream-json"],
            cwd=agent.worktree_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Track process, parse output, update UI...
        
    def get_merge_preview(self, agent_id: str) -> dict:
        """Preview what merging this agent's branch would look like."""
        agent = self.agents[agent_id]
        
        return {
            "files_changed": self.git.get_changed_files(agent.branch_name),
            "diff": self.git.get_diff("main", agent.branch_name),
            "conflicts": self.git.check_merge_conflicts("main", agent.branch_name)
        }
        
    def merge_agent(self, agent_id: str, strategy: str = "merge") -> bool:
        """Merge agent's branch into main."""
        agent = self.agents[agent_id]
        
        try:
            if strategy == "merge":
                self.git.merge(agent.branch_name)
            elif strategy == "rebase":
                self.git.rebase(agent.branch_name)
            elif strategy == "squash":
                self.git.merge(agent.branch_name, squash=True)
                
            # Cleanup
            self._cleanup_agent(agent_id)
            return True
            
        except GitMergeConflict as e:
            return False
            
    def _cleanup_agent(self, agent_id: str) -> None:
        """Remove worktree and optionally branch."""
        agent = self.agents[agent_id]
        self.git.remove_worktree(agent.worktree_path)
        # Optionally: self.git.delete_branch(agent.branch_name)
        del self.agents[agent_id]
        self._save_state()
```

### Git Service Extensions

```python
# src/services/git_service.py
class GitService:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        
    def create_worktree(self, path: Path, branch: str) -> None:
        subprocess.run(
            ["git", "worktree", "add", str(path), branch],
            cwd=self.repo_path,
            check=True
        )
        
    def remove_worktree(self, path: Path) -> None:
        subprocess.run(
            ["git", "worktree", "remove", str(path)],
            cwd=self.repo_path,
            check=True
        )
        
    def list_worktrees(self) -> list[dict]:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        # Parse output...
        
    def check_merge_conflicts(self, base: str, branch: str) -> list[str]:
        """Dry-run merge to detect conflicts."""
        result = subprocess.run(
            ["git", "merge-tree", base, branch],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        # Parse for conflict markers...
        
    def get_changed_files(self, branch: str, base: str = "main") -> list[str]:
        result = subprocess.run(
            ["git", "diff", "--name-only", base, branch],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        return result.stdout.strip().split("\n")
```

### UI Components

#### Agent Panel

```
┌─────────────────────────────────────────────────────────────┐
│  Agents                                    [+ New Agent]    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 🟢 API Refactoring                        ⏸️ ⏹️ 🔀  │   │
│  │ Branch: agent-a1b2/api-refactoring                  │   │
│  │ Status: Working                                      │   │
│  │ Modified: auth.py, users.py (+142 −89)              │   │
│  │ ├────────────────────────────────────────┤          │   │
│  │ │ Latest: Refactoring validate_token()   │          │   │
│  │ └────────────────────────────────────────┘          │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 🟢 Add Unit Tests                         ⏸️ ⏹️ 🔀  │   │
│  │ Branch: agent-c3d4/add-unit-tests                   │   │
│  │ Status: Working                                      │   │
│  │ Modified: test_auth.py, conftest.py (+203 −0)       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ⚪ Update Documentation                   ▶️ ⏹️ 🔀  │   │
│  │ Branch: agent-e5f6/update-docs                      │   │
│  │ Status: Paused                                       │   │
│  │ Modified: README.md (+45 −12)                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘

Legend: ⏸️ Pause  ⏹️ Stop  🔀 Merge  ▶️ Resume
```

#### Merge Preview Dialog

```
┌─────────────────────────────────────────────────────────────┐
│  Merge: agent-a1b2/api-refactoring → main          [×]     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Changes: 3 files, +142 −89 lines                          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 📄 src/api/auth.py                        +98 −45   │   │
│  │ 📄 src/api/users.py                       +32 −22   │   │
│  │ 📄 src/api/validators.py                  +12 −22   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ⚠️  Conflicts detected with: agent-c3d4/add-unit-tests    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ src/api/auth.py:42-56                               │   │
│  │                                                      │   │
│  │ <<<<<<< agent-a1b2/api-refactoring                  │   │
│  │ def validate_token(token: str) -> bool:             │   │
│  │     """Refactored validation logic."""              │   │
│  │     return TokenValidator.check(token)              │   │
│  │ =======                                              │   │
│  │ def validate_token(token: str) -> bool:             │   │
│  │     # Added test hook                               │   │
│  │     if TEST_MODE: return True                       │   │
│  │     return _validate_impl(token)                    │   │
│  │ >>>>>>> agent-c3d4/add-unit-tests                   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Resolution strategy:                                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ○ Keep this agent's version                         │   │
│  │ ○ Keep other agent's version                        │   │
│  │ ○ Manual edit                                       │   │
│  │ ● Ask Claude to resolve                             │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│                          [Cancel]  [Resolve & Merge]       │
└─────────────────────────────────────────────────────────────┘
```

#### New Agent Dialog

```
┌─────────────────────────────────────────────────────────────┐
│  Create New Agent                                   [×]     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Name:                                                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ API Refactoring                                     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Task:                                                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Refactor the authentication module to use the new   │   │
│  │ TokenValidator class. Update all endpoints that     │   │
│  │ currently use the legacy validate_token function.   │   │
│  │                                                      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Base branch:                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ main                                            [▼] │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ☑ Auto-start after creation                               │
│  ☐ Wait for other agents to complete first                 │
│                                                             │
│                             [Cancel]  [Create Agent]       │
└─────────────────────────────────────────────────────────────┘
```

### Claude-Assisted Conflict Resolution

When conflicts are detected, user can ask Claude to merge:

```python
MERGE_RESOLUTION_PROMPT = """
You are resolving a Git merge conflict between two AI agents working on the same codebase.

## Context

**Agent 1: {agent_1_name}**
Task: {agent_1_task}

**Agent 2: {agent_2_name}**  
Task: {agent_2_task}

## Conflict in {file_path}

**Base version (before both agents):**
```{language}
{base_content}
```

**Agent 1's version:**
```{language}
{agent_1_content}
```

**Agent 2's version:**
```{language}
{agent_2_content}
```

## Instructions

Create a merged version that:
1. Incorporates the intent of BOTH agents' changes
2. Resolves conflicts logically based on each agent's task
3. Produces valid, working code
4. Adds comments if the merge logic isn't obvious

Output ONLY the merged file content, no explanations.
"""
```

### File Lock Registry (Optional, for Same-Branch Mode)

For users who prefer working without worktrees:

```json
// ~/.local/share/claude-companion/locks/{project-hash}.json
{
  "project": "/home/user/my-project",
  "mode": "same-branch",
  "locks": {
    "src/api/auth.py": {
      "agent_id": "a1b2c3",
      "agent_name": "API Refactoring",
      "type": "write",
      "locked_at": "2025-12-06T14:30:00Z",
      "content_hash": "sha256:abc123..."
    }
  },
  "queue": [
    {
      "agent_id": "d4e5f6",
      "waiting_for": "src/api/auth.py",
      "queued_at": "2025-12-06T14:35:00Z"
    }
  ]
}
```

### Planning Phase Injection

When starting an agent, inject planning requirements:

```python
PLANNING_INJECTION = """
## Multi-Agent Coordination

You are one of several agents working on this project. Before making changes:

1. First, output a file plan:
<file_plan>
READ: file1.py, file2.py
WRITE: file3.py, file4.py  
CREATE: new_file.py
DELETE: old_file.py
</file_plan>

2. Wait for "[APPROVED]" before proceeding.

3. If you need files not in your original plan:
<additional_files>
WRITE: unexpected_file.py
</additional_files>

4. Stay focused on your task: {agent_task}

5. Do not modify files outside your approved plan.
"""
```

### Limitations and Future Work

**Current limitations:**
- No real-time conflict detection (only at merge time)
- Manual merge resolution required for complex conflicts
- Worktree creation has disk space overhead

**Future improvements:**
- Real-time file watching across worktrees
- Automatic rebase when main branch updates
- Agent communication channel (share context between agents)
- Conflict probability prediction based on file plan analysis
- Integration with CI/CD for automated testing of each agent's branch

## Project Name

Options:
- Claude Companion
- Claude Desktop (may conflict with official)
- Claude Cockpit
- Claude Workbench
- Claude Session Manager

Recommended: **Claude Companion** — clear, no conflicts, sounds good.
