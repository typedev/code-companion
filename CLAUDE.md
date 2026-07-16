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
6. **Commits**: Don't try to create your own commit without my approval of the changes.

## Project Overview

Code Companion is a native GTK4/libadwaita desktop application for working with AI coding assistants (starting with Claude Code). It provides session history viewing, active session management, Git integration, and project notes. The app uses an adapter pattern to support multiple AI CLI tools.

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
│   ├── snippets_bar.py  # Snippet buttons in the Query Editor header (right-click to delete)
│   ├── problems_panel.py  # Problems sidebar (ruff/mypy file list)
│   ├── problems_detail_view.py  # Problems detail (list + code preview)
│   ├── script_toolbar.py  # Script toolbar (Run + Outline for .py/.sh/.md files)
│   ├── markdown_preview.py  # WebKit-based markdown preview with highlight.js
│   └── ...
├── services/            # Business logic
│   ├── history.py       # Claude session history reader (low-level JSONL)
│   ├── history_adapter.py   # Abstract AI-CLI adapter interface
│   ├── adapter_registry.py  # Adapter registration/lookup (multi-provider)
│   ├── adapters/claude_adapter.py  # Claude Code history adapter
│   ├── config_path.py   # Config dir resolution (+ legacy claude-companion migration)
│   ├── project_registry.py  # Registered projects storage (v2 {path,name})
│   ├── project_lock.py  # FlockLock base + Project/Manager locks (single-instance)
│   ├── project_status_service.py  # PM card status (dirty/ahead/behind/PR/issue) + cache
│   ├── git_service.py   # Git ops via git CLI (build_git_env): status/commit/branch/
│   │                    #   push/pull/amend/stash/clone; pygit2 only for diff/stage now
│   ├── credential_service.py  # Git creds in the libsecret keyring (opt-in; plaintext fallback)
│   ├── issues_service.py  # GitHub Issues via REST (urllib), PAT from the keyring
│   ├── tasks_service.py # VSCode tasks.json parser
│   ├── toast_service.py # Toast notifications singleton
│   ├── settings_service.py  # App settings singleton (JSON storage)
│   ├── snippets_service.py  # Text snippets (~/.config/code-companion/snippets/)
│   ├── rules_service.py # CLAUDE.md rules management
│   ├── file_monitor_service.py  # Centralized file monitoring (git, tree, notes, tasks)
│   ├── run_registry.py      # Single-file runners (ext → run command; polyglot Run)
│   ├── env_registry.py      # Terminal env activators (venv/direnv/mise)
│   ├── linter_registry.py   # Multi-language linter descriptors + parsers (registry)
│   ├── problems_model.py     # Problem/FileProblems/LinterStatus data model
│   ├── problems_service.py  # Linter runner (registry-driven: ruff/mypy/yamllint/pymarkdown/shellcheck/eslint)
│   ├── async_runner.py  # run_async: off-thread worker + generation token + liveness guard
│   ├── icon_cache.py    # Material Design icons cache (O(1) lookup)
│   ├── python_outline.py / markdown_outline.py  # Outline parsers
│   ├── mcp_server.py    # Per-window MCP control surface (FastMCP, streamable-HTTP)
│   ├── gui_harness.py / gui_agent.py  # Headless GUI test harness (cage + AT-SPI + grim)
│   ├── session_summary_service.py  # Per-project session handoff summaries (synced)
│   ├── session_notify.py  # Claude Notification-hook markers -> PM desktop notifications
│   ├── project_catalog.py  # Cross-project catalog + hint resolver (coordination hub A)
│   ├── message_store.py # Event-sourced inter-project mailbox (coordination hub B)
│   └── sync_*.py        # Cross-machine sync: sync_service/engine/repo/recovery/
│                        #   lock/state_store (git-backed 3-way merge to a private remote)
├── resources/
│   └── icons/           # Material Design SVG icons (from vscode-material-icon-theme)
└── utils/               # Helpers: paths, project_identity (canonical remote id),
                         #   claude_session (tmux), claude_paths, git_auth, atomic_write,
                         #   relative_time, text_files, markdown_markup
```

Newer widgets not in the tree above: `issues_panel.py`/`issue_detail_view.py` (GitHub Issues),
`messages_panel.py`/`message_thread_view.py` (inter-project mailbox), `query_editor.py`
(GtkSourceView editor with spellcheck), `image_viewer.py`/`svg_editor.py`/`binary_file_view.py`,
`markdown_view.py`, `thinking_block.py`, `tool_call_card.py`, `github_auth.py`.

**UI Structure**:
```
┌─────────────────────────────────────────────────────────────────────┐
│ Header: [Sidebar] [Claude] [Terminal+]    Title      [⚙️] [Term]    │
├───┬──────────────────┬──────────────────────────────────────────────┤
│ F │ Sidebar          │  Main Area (Tabs)                            │
│ G │ (resizable)      │  [Session] [Commit] [Problems] [file.py]     │
│ C │                  │                                              │
│ N │ Files tab:       │  Content view:                               │
│ P │  - Unified search│  - Session details                           │
│   │  - File tree     │  - Commit details (files + message + diff)   │
│   │  - Tasks panel   │  - Problems detail (list + code preview)     │
│   │                  │  - File editor                               │
│   │ Git tab:         │  - Terminal                                  │
│   │  [Changes][Hist] │                                              │
│   │                  │                                              │
│   │ Claude tab:      │                                              │
│   │  - Sessions list │                                              │
│   │                  │                                              │
│   │ Notes tab:       │                                              │
│   │  - My Notes      │                                              │
│   │  - Docs / TODOs  │                                              │
│   │                  │                                              │
│   │ Problems tab:    │                                              │
│   │  - Files w/issues│                                              │
│   │  - Error counts  │                                              │
└───┴──────────────────┴──────────────────────────────────────────────┘
```

Key patterns:
- **Multi-process**: Each project runs in separate process (`Gio.ApplicationFlags.NON_UNIQUE`)
- **Lock files**: `/tmp/code-companion-locks/` prevents opening same project twice
- **Project registry**: `~/.config/code-companion/projects.json` stores user's projects
- **Resizable pane**: `Gtk.Paned` for sidebar/content split (min 260px sidebar, 370px default)
- **Vertical toolbar**: Left sidebar with F/G/C/N/P toggle buttons for tab switching
- **Unified search**: Single search box for both filenames and content (ripgrep/grep)
- **Single tab reuse**: Commit details and session details reuse single tab (no duplicates)
- **Icon cache**: Pre-loaded Material Design SVG icons with O(1) lookup by extension/filename
- **Centralized file monitoring**: `FileMonitorService` handles all file watching (git, working tree, notes, tasks) with debouncing
- **Toast notifications**: `ToastService` singleton for app-wide feedback
- **Selection preservation**: Lists preserve selection across refresh (git history, claude sessions)
- **Settings service**: `SettingsService` singleton with JSON storage at `~/.config/code-companion/settings.json`
- **Lazy loading**: Claude history panel loads sessions only when tab is shown (background thread)
- **Git authentication**: HTTPS credentials dialog with git credential storage
- **Terminal enhancements**: Left padding for readability, auto `.venv` activation on launch
- **Problems panel**: Linter integration (ruff, mypy) with file grouping and copy functionality
- Parse Claude Code JSONL session files from `~/.claude/projects/[encoded-path]/`
- Project paths are encoded by replacing `/` with `-`
- **Async layer**: `run_async(widget, worker, on_done, key=…)` runs blocking work off the GTK
  thread with a per-(widget,key) generation token + liveness guard (never touch a dead widget)
- **MCP control surface**: one FastMCP streamable-HTTP server per `ProjectWindow` (bearer token,
  worker-thread tools marshalled to the main loop via `call_on_main`); lets the embedded Claude
  session read/act on the window (open files, diffs, notes, issues) and reach cross-project tools
- **Session supervisor**: `claude` runs as the root of a per-project **tmux** session
  (`cc-<sha1(path)>`), so restarting the IDE window re-attaches instead of killing the session;
  stable `(port, token)` recovered from the tmux env; PM shows live/attention dots
- **Cross-machine sync**: git-backed 3-way merge (`sync_service`/`sync_engine`) of Claude history,
  memory, plans, session summaries, snippets/rules and the message store to a private remote;
  keyed by
  `resolve_project_identity` (canonical git remote → stable `project_id`)
- **Coordination hub**: `project_catalog` (list/resolve sibling projects) + `message_store`
  (event-sourced, synced inter-project mailbox); both exposed as MCP tools and a GUI Messages panel
- **Credential keyring**: `CredentialService` stores git/GitHub PATs in libsecret (opt-in), with a
  graceful fallback to git's plaintext store helper when unavailable
- **Locks**: all locks (Project/Manager/Sync) share the `fcntl.flock`-based `FlockLock` base
  (kernel auto-release on process death → no stale locks)

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
- [x] v0.7.2: Problems Panel & Vertical Toolbar:
  - Vertical toolbar (F/G/C/N/P) replacing horizontal tab switcher
  - Problems panel with ruff/mypy integration
  - Problems detail view (list + code preview with highlighted lines)
  - Copy problems to clipboard (single/all)
  - Lazy loading for problems (runs linters on tab show)
- [x] v0.7.3: Script Toolbar:
  - Script toolbar for .py/.sh files (Run button + Outline)
  - Run with arguments dialog (`Adw.SplitButton` with menu)
  - Python outline parser (`ast` module) with classes/methods/functions
  - Hierarchical outline popover with line navigation
  - Color coding: classes (`@accent_color`), methods (`@success_color`), functions (`@warning_color`)
  - Cursor sync: auto-select current element in outline based on cursor position
  - TODO: Save last used arguments per file
- [x] v0.7.4: Markdown Support:
  - Markdown outline navigation (headings # to ######)
  - Markdown preview with WebKit (highlight.js for code blocks)
  - Preview toggle button in toolbar for .md files
  - Theme-aware preview (light/dark)
  - Editor find/replace: Ctrl+F search bar (case/word/regex, match counter) + Ctrl+H replace
- [x] v0.7.5: Git-Centric Project Manager:
  - Per-project git status badges: dirty ●, ahead ↑N, behind ↓N, PR count, Issue count
  - Local markers (repo/remote/dirty/ahead) auto-computed in background thread
  - Network markers (fetch→behind, PR/Issue counts) via Refresh button + disk cache
  - Smart "Updated <relative>" label (`utils/relative_time.humanize_relative`)
  - `ProjectStatusService` (`services/project_status_service.py`) with JSON cache
  - `ProjectRegistry` v2 format `{path, name}` with transparent legacy migration
  - Rename project label (custom name, folder untouched)
  - New Project button (folder picker + name → `git init` → register + open)
  - See `docs/plan-git-centric-project-manager.md`
- [x] v0.8: Code Companion Refactor:
  - Renamed to "Code Companion"; `HistoryService` abstracted into `HistoryAdapter` interface +
    `adapter_registry`; `ClaudeHistoryAdapter` for `~/.claude/`; groundwork for Gemini/Codex
  - See `docs/plan-code-companion-refactor.md`
- [x] GitHub Issues: sidebar Issues panel + detail view (REST via urllib, PAT from keyring),
  MCP `create_issue`; see `docs/plan-github-issues.md`
- [x] Rules management: edit CLAUDE.md guideline rules; see `docs/plan-rules.md`
- [x] Query editor: GtkSourceView editor with libspelling spellcheck (`editor.spellcheck_language`)
- [x] Persistent Claude pane: bottom Claude pane + header activity bar (F/G/C/N/P/Issues/Messages);
  see `docs/plan-ui-persistent-claude-pane.md`
- [x] MCP integration (Part A): per-window FastMCP server + read/act tools (workspace state,
  selection, open file, diff, commit, problems, list/run/create tasks, notes, issues, session summary) + `/refresh`
  hook; Preference `mcp.enabled`. GUI test harness (Part B). Tool reference: `docs/mcp.md`;
  design: `docs/plan-mcp-integration.md`
- [x] Cross-machine sync: git-backed 3-way merge of history/memory/plans/summaries to a private
  remote (`sync.*` settings, selected/backup modes); see `docs/plan-sync-across-machines.md`
- [x] Session supervisor (Tier 1): Claude survives IDE-window restart via tmux; live/attention dots,
  kill/orphan reconcile, stable reserved MCP port; see `docs/plan-session-supervisor.md`
- [x] Coordination hub (A→D): cross-project catalog + resolver MCP (`list_projects`/`resolve_project`)
  and a synced event-sourced inter-project mailbox (GUI Messages panel + `send`/`list`/`reply`/
  `resolve_message` MCP tools); design in `memory/project_coordination_hub.md`
- [~] Stability roadmap: 6-phase hardening (async layer, git status unification, file-monitor gaps,
  session-viewer freeze fix, keyring, port reservation) — mostly done; `docs/plan-stability-roadmap.md`
- [x] Phase 4 git features (all except the deferred merge/conflict UI 4.4): commit/branch migrated to
  the git CLI (closes 3.1/3.9); amend + multiline commit; publish/upstream visibility; remote-branch
  checkout; New-Project polish (default branch + initial commit); SSH-key awareness; stash; clone-from-URL
- [x] Agent observability: live token spend + cost estimate + context-window meter on PM cards,
  per-session token usage in the history panel; `session_insight_service`/`model_pricing`
- [x] v1.0: Multi-agent orchestration with Git worktrees — nested worktree cards in the PM,
  create/remove, merge-back with conflict preview, completion reports (⑂ N ready badge), and MCP
  orchestration (`create_worktree`/`merge_worktree`/`report_worktree_complete`); see
  `docs/plan-worktrees-multiagent.md`
- [x] Worktree delegation (v1, manual): main delegates a task to the worktree agent via a
  brief (`send_message` + optional `docs/plan-<branch>.md`); two human-gates (intake
  "Take this into development?" + delivery confirmation before `report_worktree_complete`),
  encoded in the worktree session's appended system prompt; see
  `docs/plan-worktree-delegation.md`. When delegating from a main window, follow that
  protocol: write a detailed brief (subject naming the target branch) and stay autonomous.
- [ ] v0.9: Packaging (Flatpak manifest; `.desktop` file already ships in `data/`)

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
| `editor.spellcheck_language` | `"auto"` | libspelling language for the query editor |
| `terminal.auto_activate_env` | `true` | Auto-activate the project env on terminal spawn (venv/direnv/mise) |
| `terminal.touchpad_pixels_per_click` | `25` | Touchpad travel per scroll step in the Claude pane (1–100; `1` = VTE's raw behaviour). Works around GNOME/vte#2720 — VTE ignores `GdkScrollUnit` and feeds the app one wheel click per *pixel*. Mouse wheel unaffected |
| `window.width/height` | `1200/800` | Window size |
| `window.maximized` | `false` | Maximized state |
| `window.sidebar_width` | `370` | Sidebar pane width |
| `window.workspace_split_position` | `260` | Height of the tabs area above the Claude pane |
| `window.workspace_collapsed` | `false` | Tabs area collapsed to the tab bar |
| `window.claude_collapsed` | `false` | Claude pane fully hidden (reclaims its 220px minimum) |
| `linters.<id>_enabled` | `true` | Enable a linter by registry id: `ruff`, `mypy`, `yamllint`, `pymarkdown`, `shellcheck`, `eslint` |
| `linters.ignored_codes` | `""` | Comma-separated codes to ignore; bare = all linters, `linter:code` = scoped (e.g. "E402, shellcheck:SC2086") |
| `mcp.enabled` | `true` | Per-window MCP control surface for the embedded session |
| `sessions.notifications` | `true` | Desktop notifications from Claude Notification hooks |
| `sync.enabled` | `false` | Cross-machine sync of history/memory/plans/summaries/messages |
| `sync.repo_url` | `""` | Private git remote that backs sync |
| `sync.mode` | `"selected"` | `selected` (chosen projects) or `backup` (registry-wide) |
| `ai.provider` | `"claude"` | Active AI-CLI adapter |

> Config artifacts under `~/.config/code-companion/`: `settings.json`, `projects.json`,
> `snippets/`, `session-summaries/`, `messages/` + `messages-seen.json`, `notify/`, `sync/` +
> `sync_state.json` + `sync_status_cache.json`. Git/GitHub credentials live in the libsecret
> keyring (not on disk) when available.

## Running the Application

```bash
# Open Project Manager (select/add projects)
uv run python -m src.main

# Open specific project directly
uv run python -m src.main --project /path/to/project
```
