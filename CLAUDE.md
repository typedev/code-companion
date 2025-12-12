# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Info

- **Author**: Alexander Lubovenko (github.com/typedev)
- **License**: Apache 2.0

## Development Rules

1. **Language**: All code comments and documentation in English. Chat in any language.
2. **Planning**: Before implementing new features, create detailed plan with checkpoints in `/docs` folder. Follow and update progress as you go.
3. **Agent usage**: Save user's context — use Task agents for exploration:
   - **Explore agent**: For searching code, finding files, understanding how things work
   - **Plan agent**: For complex multi-step implementations
   - **Direct Read/Grep**: Only when you know exactly which file you need
4. **Documentation**: Always search and verify against December 2025 docs.
5. **Package manager**: Always use `uv` for Python dependencies and running scripts.

## Project Overview

Claude Companion is a native GTK4/libadwaita desktop application for working with Claude Code. It provides session history viewing, active session management, Git integration, and project notes. The app reads Claude Code data from `~/.claude/` (projects, sessions, JSONL files).

## Tech Stack

- Python 3.12+
- GTK4 + libadwaita (via PyGObject)
- GtkSourceView 5 for code highlighting
- VTE 3.91 for terminal emulation
- pygit2 for git operations
- Build system: Meson (planned)

## Development Commands

```bash
# Install dependencies (Fedora)
sudo dnf install gtk4-devel libadwaita-devel gtksourceview5-devel python3-gobject meson ninja-build

# Run the application
uv run main.py
# or
python main.py
```

## Architecture

```
src/
├── main.py              # Entry point, argument parsing, NON_UNIQUE application
├── project_manager.py   # Project Manager window (select/add/remove projects)
├── project_window.py    # Project workspace (file tree, tabs, terminal, git)
├── models/              # Data models (Project, Session, Message, ToolCall)
├── widgets/             # UI components
│   ├── file_tree.py     # File browser with git status, file monitoring, gitignore filtering
│   ├── file_editor.py   # Code editor with autosave
│   ├── terminal_view.py # VTE terminal with Dracula theme
│   ├── session_view.py  # Claude session content viewer
│   ├── claude_history_panel.py  # Claude sessions list (sidebar)
│   ├── code_view.py     # Read-only code display + DiffView
│   ├── git_changes_panel.py  # Git changes (stage/commit/push/pull)
│   ├── git_history_panel.py  # Git commit history list
│   ├── commit_detail_view.py # Commit details (files + message + diff)
│   ├── branch_popover.py     # Branch management popover
│   ├── tasks_panel.py   # VSCode tasks.json runner
│   └── ...
├── services/            # Business logic
│   ├── history.py       # Claude session history reader
│   ├── project_registry.py  # Registered projects storage
│   ├── project_lock.py  # Lock files for single-instance per project
│   ├── git_service.py   # Git operations via pygit2
│   ├── tasks_service.py # VSCode tasks.json parser
│   ├── toast_service.py # Toast notifications singleton
│   └── icon_cache.py    # Material Design icons cache (O(1) lookup)
├── resources/
│   └── icons/           # Material Design SVG icons (from vscode-material-icon-theme)
└── utils/               # Helpers (path encoding)
```

**UI Structure**:
```
┌─────────────────────────────────────────────────────────────────────┐
│ Header: [Sidebar] [Claude] [Terminal+]          Title        [Term] │
├──────────────────────┬──────────────────────────────────────────────┤
│ Sidebar              │  Main Area (Tabs)                            │
│ [Files][Git][Claude] │  [Session] [Commit] [file.py] [Terminal]     │
│                      │                                              │
│ Files tab:           │  Content view:                               │
│  - File tree         │  - Session details                           │
│  - Tasks panel       │  - Commit details (files + message + diff)   │
│                      │  - File editor                               │
│ Git tab:             │  - Terminal                                  │
│  [Changes][History]  │                                              │
│  - Stage/commit/push │                                              │
│  - Commit list       │                                              │
│                      │                                              │
│ Claude tab:          │                                              │
│  - Sessions list     │                                              │
└──────────────────────┴──────────────────────────────────────────────┘
```

Key patterns:
- **Multi-process**: Each project runs in separate process (`Gio.ApplicationFlags.NON_UNIQUE`)
- **Lock files**: `/tmp/claude-companion-locks/` prevents opening same project twice
- **Project registry**: `~/.config/claude-companion/projects.json` stores user's projects
- **Unified navigation**: Sidebar for browsing (Files/Git/Claude), main area for content only
- **Single tab reuse**: Commit details and session details reuse single tab (no duplicates)
- **Icon cache**: Pre-loaded Material Design SVG icons with O(1) lookup by extension/filename
- **File monitoring**: Auto-refresh file tree via `Gio.FileMonitor` with debounce
- **Toast notifications**: `ToastService` singleton for app-wide feedback
- Parse Claude Code JSONL session files from `~/.claude/projects/[encoded-path]/`
- Project paths are encoded by replacing `/` with `-`

## Claude Code Data Format

Session files are JSONL with event types: `user`, `assistant`, `tool_use`, `tool_result`. Assistant content contains `thinking`, `text`, and `tool_use` blocks.

## MVP Milestones

- [x] v0.1: History viewer (JSONL parsing, project/session lists, message display)
- [x] v0.2: Session content viewer (tool calls, thinking blocks, code/diff display)
- [x] v0.2.1: Markdown support, improved code blocks
- [x] v0.3: Embedded VTE terminal with tabs (Terminal/History)
- [x] v0.4: Project workspace (file tree, file editor, multi-process architecture)
- [x] v0.4.1: VSCode tasks.json support (tasks panel in sidebar)
- [x] v0.5: Git integration (pygit2, Files/Changes tabs, stage/commit/push/pull, unified diff view)
- [x] v0.5.1: Git history (commit list, checkout/reset/revert, Files/Git sidebar structure)
- [x] v0.5.2: Material Design icons (vscode-material-icon-theme, cached SVG icons, Claude icon)
- [x] v0.5.3: UX improvements:
  - Toast notifications (ToastService)
  - File tree auto-refresh (Gio.FileMonitor)
  - Gitignore filtering (pathspec)
  - Branch management (create/switch/delete)
  - Commit detail view (files list + full message + per-file diff)
  - Unified sidebar (Files/Git/Claude tabs)
  - Single-tab reuse for sessions and commits
- [ ] v0.6: TODO notes
- [ ] v0.7: Polish (search, settings, packaging)
- [ ] v1.0: Multi-agent orchestration with Git worktrees

## Running the Application

```bash
# Open Project Manager (select/add projects)
uv run python -m src.main

# Open specific project directly
uv run python -m src.main --project /path/to/project
```
