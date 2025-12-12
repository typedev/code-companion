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

## Architecture (Planned)

The spec defines this target structure:

```
src/
├── main.py              # Entry point
├── application.py       # Adw.Application subclass
├── window.py            # Main window (Adw.NavigationSplitView)
├── models/              # Data models (Project, Session, Message, ToolCall)
├── widgets/             # UI components (sidebar, message rows, diff view, etc.)
├── services/            # Business logic (history reading, claude runner, git ops)
└── utils/               # Helpers (path encoding)
```

Key patterns from the spec:
- Use `Gio.Subprocess` and `GLib.io_add_watch()` for async Claude CLI interaction
- Parse Claude Code JSONL session files from `~/.claude/projects/[encoded-path]/`
- Project paths are encoded by replacing `/` with `-`
- Stream JSON output from `claude --output-format stream-json`

## Claude Code Data Format

Session files are JSONL with event types: `user`, `assistant`, `tool_use`, `tool_result`. Assistant content contains `thinking`, `text`, and `tool_use` blocks.

## MVP Milestones

v0.1: History viewer (JSONL parsing, project/session lists, message display)
v0.2: Tool calls and details (cards, thinking blocks, diffs)
v0.3: Active sessions (launch/stream Claude CLI)
v0.4: Git integration
v0.5: TODO notes
v0.6: Polish (search, settings, packaging)
v1.0: Multi-agent orchestration with Git worktrees
