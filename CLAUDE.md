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
│   ├── file_editor.py   # Code editor with autosave, go-to-line with highlighting
│   ├── unified_search.py    # Unified search (files + content) with replace
│   ├── terminal_view.py # VTE terminal with Dracula theme, left padding, auto .venv activation
│   ├── session_view.py  # Claude session content viewer
│   ├── claude_history_panel.py  # Claude sessions list with filtering, lazy loading
│   ├── code_view.py     # Read-only code display + DiffView
│   ├── git_changes_panel.py  # Git changes (stage/commit/push/pull) with auth dialog
│   ├── git_history_panel.py  # Git commit history with filtering
│   ├── commit_detail_view.py # Commit details (files + message + diff)
│   ├── branch_popover.py     # Branch management popover
│   ├── tasks_panel.py   # VSCode tasks.json runner
│   ├── notes_panel.py   # Notes panel (My Notes + Docs + TODOs)
│   ├── preferences_dialog.py  # Settings dialog (Adw.PreferencesDialog)
│   ├── snippets_bar.py  # Snippets buttons bar (right-click to delete)
│   └── ...
├── services/            # Business logic
│   ├── history.py       # Claude session history reader
│   ├── project_registry.py  # Registered projects storage
│   ├── project_lock.py  # Lock files for single-instance per project
│   ├── git_service.py   # Git operations via pygit2 (push/pull via git CLI with auth)
│   ├── tasks_service.py # VSCode tasks.json parser
│   ├── toast_service.py # Toast notifications singleton
│   ├── settings_service.py  # App settings singleton (JSON storage)
│   ├── snippets_service.py  # Text snippets management (files in ~/.config/claude-companion/snippets/)
│   ├── file_monitor_service.py  # Centralized file monitoring (git, working tree, notes, tasks)
│   └── icon_cache.py    # Material Design icons cache (O(1) lookup)
├── resources/
│   └── icons/           # Material Design SVG icons (from vscode-material-icon-theme)
└── utils/               # Helpers (path encoding)
```

**UI Structure**:
```
┌─────────────────────────────────────────────────────────────────────┐
│ Header: [Sidebar] [Claude] [Terminal+]    Title      [⚙️] [Term]    │
├──────────────────────┬──────────────────────────────────────────────┤
│ Sidebar (resizable)  │  Main Area (Tabs)                            │
│ [Files][Git][Claude][Notes]  [Session] [Commit] [file.py] [Terminal]│
│                      │                                              │
│ Files tab:           │  Content view:                               │
│  - Unified search    │  - Session details                           │
│  - File tree         │  - Commit details (files + message + diff)   │
│  - Tasks panel       │  - File editor                               │
│                      │  - Terminal                                  │
│ Git tab:             │                                              │
│  [Changes][History]  │                                              │
│  - Stage/commit/push │                                              │
│  - Commit list       │                                              │
│                      │                                              │
│ Claude tab:          │                                              │
│  - Sessions list     │                                              │
│                      │                                              │
│ Notes tab:           │                                              │
│  - My Notes (notes/) │                                              │
│  - Docs (docs/)      │                                              │
│  - TODOs from code   │                                              │
└──────────────────────┴──────────────────────────────────────────────┘
```

Key patterns:
- **Multi-process**: Each project runs in separate process (`Gio.ApplicationFlags.NON_UNIQUE`)
- **Lock files**: `/tmp/claude-companion-locks/` prevents opening same project twice
- **Project registry**: `~/.config/claude-companion/projects.json` stores user's projects
- **Resizable pane**: `Gtk.Paned` for sidebar/content split (min 370px sidebar)
- **Custom tab switcher**: Linked toggle buttons for Files/Git/Claude/Notes tabs
- **Unified search**: Single search box for both filenames and content (ripgrep/grep)
- **Single tab reuse**: Commit details and session details reuse single tab (no duplicates)
- **Icon cache**: Pre-loaded Material Design SVG icons with O(1) lookup by extension/filename
- **Centralized file monitoring**: `FileMonitorService` handles all file watching (git, working tree, notes, tasks) with debouncing
- **Toast notifications**: `ToastService` singleton for app-wide feedback
- **Selection preservation**: Lists preserve selection across refresh (git history, claude sessions)
- **Settings service**: `SettingsService` singleton with JSON storage at `~/.config/claude-companion/settings.json`
- **Lazy loading**: Claude history panel loads sessions only when tab is shown (background thread)
- **Git authentication**: HTTPS credentials dialog with git credential storage
- **Terminal enhancements**: Left padding for readability, auto `.venv` activation on launch
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
- [x] v0.6: Search & Notes:
  - Unified search in Files tab (files + content + replace)
  - Git history filtering (by message/author/hash)
  - Claude sessions filtering (by preview/date)
  - Notes panel with 3 sections:
    - My Notes (`notes/*.md`) with New Note button
    - Docs (`docs/*.md` + `CLAUDE.md`)
    - TODOs from code (`TODO:`, `FIXME:`, `HACK:`, `XXX:`, `NOTE:`)
  - Resizable sidebar pane (`Gtk.Paned`)
  - Custom tab switcher (linked toggle buttons)
  - Git changes auto-refresh
  - Selection preservation across refresh
- [x] v0.7: Settings & Preferences:
  - `SettingsService` singleton with JSON storage
  - `PreferencesDialog` with 3 pages (Appearance, Editor, Files)
  - Theme: system/light/dark via `Adw.StyleManager`
  - Syntax scheme: all GtkSourceView schemes
  - Font: family, size, line height (shared by editor + terminal)
  - Editor: tab size, insert spaces
  - File tree: show hidden files
  - Window state: size, position, maximized (auto-saved)
  - Live apply (no restart needed)
- [x] v0.7.1: Performance & UX:
  - Lazy loading for Claude history (background thread, loads only when tab shown)
  - Centralized `FileMonitorService` (replaces duplicated monitors across components)
  - Terminal left padding (24px) for better readability
  - Auto `.venv` activation on terminal launch
  - Git HTTPS authentication dialog with credential storage
  - Version system and About dialog
- [ ] v0.8: Packaging (Flatpak, .desktop file)
- [ ] v1.0: Multi-agent orchestration with Git worktrees

## GTK4/libadwaita Gotchas

### Text Input in Dialogs

**CRITICAL**: Text input (Gtk.Entry) can break in dialogs if keyboard events are intercepted.

**Working pattern** (see `tasks_panel.py`, `content_search_dialog.py`):
```python
# Use Adw.AlertDialog with set_extra_child()
dialog = Adw.AlertDialog()
dialog.set_heading("Title")

entry = Gtk.Entry()  # or Gtk.SearchEntry
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
box.append(entry)

dialog.set_extra_child(box)
dialog.present(parent)
```

**For SearchEntry, disable global key capture**:
```python
search_entry = Gtk.SearchEntry()
search_entry.set_key_capture_widget(None)  # IMPORTANT!
```

**NEVER do this**:
```python
# DON'T add EventControllerKey to dialog - it intercepts ALL key presses!
key_controller = Gtk.EventControllerKey()
key_controller.connect("key-pressed", handler)
dialog.add_controller(key_controller)  # BREAKS TEXT INPUT!
```

**For window-level shortcuts**, use `Gtk.ShortcutController` with LOCAL scope:
```python
shortcut_controller = Gtk.ShortcutController()
shortcut_controller.set_scope(Gtk.ShortcutScope.LOCAL)  # Won't interfere with dialogs
shortcut_controller.add_shortcut(Gtk.Shortcut(
    trigger=Gtk.ShortcutTrigger.parse_string("<Control>p"),
    action=Gtk.CallbackAction.new(callback)
))
window.add_controller(shortcut_controller)
```

### Icons

**Use `Gtk.Image.new_from_gicon()` for crisp SVG icons**:
```python
# Good - crisp at any size
gicon = Gio.FileIcon.new(Gio.File.new_for_path(svg_path))
image = Gtk.Image.new_from_gicon(gicon)
image.set_pixel_size(16)

# Bad - blurry because pre-rasterized
texture = Gdk.Texture.new_from_file(file)
image = Gtk.Image.new_from_paintable(texture)
```

### Settings Service

**Using SettingsService for app settings**:
```python
from ..services import SettingsService

# Get singleton instance
settings = SettingsService.get_instance()

# Read settings (dot notation)
theme = settings.get("appearance.theme", "system")
font_size = settings.get("editor.font_size", 12)

# Write settings (auto-saves, emits signal)
settings.set("appearance.theme", "dark")

# Listen for changes
settings.connect("changed", on_setting_changed)

def on_setting_changed(settings, key, value):
    if key == "appearance.theme":
        apply_theme(value)
```

**Available settings**:
| Key | Default | Description |
|-----|---------|-------------|
| `appearance.theme` | `"system"` | Color scheme: system/light/dark |
| `appearance.syntax_scheme` | `"Adwaita-dark"` | GtkSourceView scheme |
| `editor.font_family` | `"Monospace"` | Font family |
| `editor.font_size` | `12` | Font size in pt |
| `editor.line_height` | `1.4` | Line height multiplier |
| `editor.tab_size` | `4` | Tab width |
| `editor.insert_spaces` | `true` | Use spaces for indentation |
| `editor.word_wrap` | `true` | Wrap long lines at word boundaries |
| `window.width/height` | `1200/800` | Window size |
| `window.maximized` | `false` | Maximized state |

## Running the Application

```bash
# Open Project Manager (select/add projects)
uv run python -m src.main

# Open specific project directly
uv run python -m src.main --project /path/to/project
```
