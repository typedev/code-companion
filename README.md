# Claude Companion

A native GTK4/libadwaita desktop application for working with [Claude Code](https://claude.ai/code). Provides a visual IDE-like environment with session history, file editing, terminal, Git integration, and project notes.

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.12+-green.svg)
![GTK](https://img.shields.io/badge/GTK-4.0-orange.svg)

## Features

### Project Manager
- Register and manage multiple projects
- Single-click to select, double-click to open
- Lock mechanism prevents opening the same project twice

### Project Workspace

**Vertical toolbar with 5 tabs:**

#### Files Tab (F)
- **File Tree** — Browse project files with Material Design icons and git status indicators
- **Unified Search** — Search files by name and content, with find & replace
- **Tasks Panel** — Run VSCode tasks from `.vscode/tasks.json`

#### Git Tab (G)
- **Changes Panel:**
  - View staged and unstaged changes
  - Stage/unstage individual files or all at once
  - Commit with message
  - Push/Pull with HTTPS authentication dialog and credential storage
  - Auto-refresh on file changes (centralized monitoring)
- **History Panel:**
  - Browse commit history with filtering (by message/author/hash)
  - View commit details (files list + full message + per-file diff)
  - Checkout, reset, revert commits
- **Branch Management:**
  - Create, switch, delete branches
  - Branch popover with quick access

#### Claude Tab (C)
- Browse past Claude Code sessions (lazy loading for performance)
- Filter by preview text or date
- View messages with tool calls, thinking blocks, code/diff display
- One-click Claude Code session launch

#### Notes Tab (N)
- **My Notes** — Personal notes in `notes/` folder with New Note button
- **Docs** — Documentation from `docs/` folder and `CLAUDE.md`
- **TODOs** — Auto-extracted from code (`TODO:`, `FIXME:`, `HACK:`, `XXX:`, `NOTE:`)

#### Problems Tab (P)
- **Linter Integration** — Run ruff and mypy on project files
- **File Grouping** — Problems grouped by file with error counts
- **Code Preview** — View problem location with syntax highlighting
- **Copy to Clipboard** — Copy single problem or all problems

### Main Area
- **File Editor** — Syntax highlighting via GtkSourceView 5, autosave on focus loss, go-to-line
- **Script Toolbar** — Run button with arguments dialog, code outline for .py/.sh/.md files
- **Markdown Preview** — WebKit-based preview with syntax highlighting (highlight.js)
- **Terminal Tabs** — Embedded VTE terminal with Dracula theme, left padding, auto `.venv` activation
- **Session View** — Claude session content with Markdown support
- **Commit Detail View** — Files list + commit message + unified diff
- **Snippets Bar** — Quick-access text snippets

### Settings & Preferences
- **Theme:** System/Light/Dark via libadwaita
- **Syntax Scheme:** All GtkSourceView schemes available
- **Font:** Family, size, line height (shared by editor + terminal)
- **Editor:** Tab size, insert spaces, word wrap
- **File Tree:** Show/hide hidden files
- **Linters:** Enable/disable ruff and mypy, ignore specific codes
- **Window State:** Auto-saves size, position, sidebar width, maximized state

## Screenshots

*Coming soon*

## Installation

### Requirements

**System dependencies (Fedora):**
```bash
sudo dnf install gtk4-devel libadwaita-devel gtksourceview5-devel \
    vte291-gtk4-devel python3-gobject libgit2-devel webkit2gtk5.0-devel
```

**System dependencies (Ubuntu/Debian):**
```bash
sudo apt install libgtk-4-dev libadwaita-1-dev libgtksourceview-5-dev \
    libvte-2.91-gtk4-dev python3-gi libgit2-dev libwebkitgtk-6.0-dev
```

### Python Setup

```bash
# Clone the repository
git clone https://github.com/typedev/claude-companion.git
cd claude-companion

# Install Python dependencies with uv
uv sync

# Run the application
uv run python -m src.main
```

## Usage

### Open Project Manager
```bash
uv run python -m src.main
```

### Open a specific project directly
```bash
uv run python -m src.main --project /path/to/your/project
```

## Architecture

```
src/
├── main.py                  # Entry point, NON_UNIQUE application
├── project_manager.py       # Project selection window
├── project_window.py        # Main workspace window
├── models/                  # Data models
│   ├── project.py           # Project model
│   ├── session.py           # Session model
│   ├── message.py           # Message model
│   └── tool_call.py         # Tool call model
├── widgets/                 # UI components
│   ├── file_tree.py         # File browser with git status, gitignore filtering
│   ├── file_editor.py       # Code editor with autosave, go-to-line
│   ├── unified_search.py    # Unified search (files + content) with replace
│   ├── terminal_view.py     # VTE terminal with Dracula theme
│   ├── session_view.py      # Claude session content viewer
│   ├── claude_history_panel.py   # Claude sessions list with filtering
│   ├── code_view.py         # Read-only code display + DiffView
│   ├── git_changes_panel.py # Git changes (stage/commit/push/pull)
│   ├── git_history_panel.py # Git commit history with filtering
│   ├── commit_detail_view.py# Commit details (files + message + diff)
│   ├── branch_popover.py    # Branch management popover
│   ├── tasks_panel.py       # VSCode tasks.json runner
│   ├── notes_panel.py       # Notes panel (My Notes + Docs + TODOs)
│   ├── problems_panel.py    # Problems sidebar (ruff/mypy)
│   ├── problems_detail_view.py  # Problems detail with code preview
│   ├── script_toolbar.py    # Run button + Outline for scripts
│   ├── markdown_preview.py  # WebKit markdown preview
│   ├── snippets_bar.py      # Quick-access snippets
│   ├── preferences_dialog.py# Settings dialog
│   └── ...
├── services/                # Business logic
│   ├── history.py           # Claude session reader
│   ├── project_registry.py  # Registered projects storage
│   ├── project_lock.py      # Lock files for single-instance per project
│   ├── git_service.py       # Git operations via pygit2 (with HTTPS auth)
│   ├── tasks_service.py     # tasks.json parser
│   ├── toast_service.py     # Toast notifications singleton
│   ├── settings_service.py  # App settings (JSON storage)
│   ├── snippets_service.py  # Text snippets management
│   ├── file_monitor_service.py  # Centralized file monitoring
│   ├── problems_service.py  # Linter runner (ruff/mypy)
│   ├── icon_cache.py        # Material Design icons cache
│   ├── python_outline.py    # Python AST parser for outline
│   └── markdown_outline.py  # Markdown heading parser
└── resources/
    └── icons/               # Material Design SVG icons
```

### Key Design Decisions

- **Multi-process architecture** — Each project opens in a separate process (`Gio.ApplicationFlags.NON_UNIQUE`)
- **Lock files** — `/tmp/claude-companion-locks/` prevents duplicate project instances
- **Project registry** — `~/.config/claude-companion/projects.json` stores registered projects
- **Settings** — `~/.config/claude-companion/settings.json` stores user preferences
- **Claude data** — Reads session history from `~/.claude/projects/[encoded-path]/`
- **Material Design Icons** — Pre-loaded SVG icons from vscode-material-icon-theme with O(1) lookup
- **Centralized file monitoring** — `FileMonitorService` handles all file watching with debouncing
- **Lazy loading** — Claude history and problems load only when needed (background thread)
- **Git authentication** — HTTPS credentials dialog with git credential storage

## Roadmap

- [x] v0.1: History viewer
- [x] v0.2: Session content (tool calls, thinking blocks)
- [x] v0.3: Embedded terminal
- [x] v0.4: Project workspace (file tree, editor)
- [x] v0.4.1: VSCode tasks support
- [x] v0.5: Git integration (stage/commit/push/pull, unified diff)
- [x] v0.5.1: Git history (commit list, checkout/reset/revert)
- [x] v0.5.2: Material Design icons
- [x] v0.5.3: UX improvements (toast notifications, branch management, auto-refresh)
- [x] v0.6: Search & Notes (unified search, notes panel, filtering)
- [x] v0.7: Settings & Preferences
- [x] v0.7.1: Performance & UX (lazy loading, file monitoring, terminal enhancements, git auth)
- [x] v0.7.2: Problems Panel (ruff/mypy integration, vertical toolbar)
- [x] v0.7.3: Script Toolbar (Run button, Python outline)
- [x] v0.7.4: Markdown Support (outline, WebKit preview)
- [ ] v0.8: Code Companion (multi-provider support, rename)
- [ ] v0.9: Packaging (Flatpak, .desktop file)
- [ ] v1.0: Multi-agent orchestration with Git worktrees

## Third-Party Credits

- **Icons:** [Material Icon Theme](https://github.com/material-extensions/vscode-material-icon-theme) (MIT License)
- **Markdown:** [mistune](https://github.com/lepture/mistune) (BSD-3 License)
- **Syntax Highlighting:** [highlight.js](https://highlightjs.org) (BSD-3 License)

## License

Apache License 2.0

## Author

Alexander Lubovenko ([@typedev](https://github.com/typedev))
