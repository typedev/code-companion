# MCP control surface — reference

Code Companion runs a **Model Context Protocol (MCP) server per project window**, so the
embedded Claude session (or any MCP client) can read and act on *that* window: inspect the
workspace, open files, show diffs, run linters/tasks, file issues, coordinate with sibling
projects, orchestrate worktrees, and drive a headless GUI test harness.

This is the user/agent-facing reference. For the implementation history and design rationale
see [plan-mcp-integration.md](plan-mcp-integration.md).

## Enabling

- Preference **`mcp.enabled`** (default `true`) — toggles the per-window server. Disable it in
  Settings if you don't want the session acting on the window.
- No setup is needed for the embedded session: when Code Companion launches `claude` it injects
  the server's URL + token via `--mcp-config`, so the tools are available automatically.

## Transport & auth

- One **FastMCP streamable-HTTP** server per `ProjectWindow`, bound to `127.0.0.1` on a stable,
  reserved port (recovered from the project's tmux session env as `CC_MCP_PORT`/`CC_MCP_TOKEN`,
  so it survives IDE-window restarts).
- **Bearer-token** auth (`secrets.token_urlsafe(32)`); the token is never written to disk in
  plaintext beyond the session's `--mcp-config`.
- Worker-thread tools are marshalled to the GTK main loop (`call_on_main`); long/blocking tools
  (network, linters) run on the worker thread instead to avoid the main-loop timeout.
- A `POST /refresh` hook (wired as Claude's PostToolUse hook) lets the app re-read window state
  after the agent acts.

## Tool catalog (38 tools)

### Workspace & files
| Tool | Signature | Purpose |
|------|-----------|---------|
| `notify` | `(message)` | Show a message to the user in the window |
| `get_workspace_state` | `()` | Active file, cursor line, open editor tabs |
| `get_selection` | `()` | Current selection in the active editor or terminal |
| `open_file` | `(path, line=None, end_line=None)` | Open a file, optionally scroll to / highlight a range |
| `show_diff` | `(path)` | Open the working-tree diff for a file |
| `show_commit` | `(commit_hash)` | Open the commit-detail tab for a hash |

### Problems & linters
| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_problems` | `(path=None)` | Current linter findings shown in the Problems panel (read-only) |
| `list_linters` | `()` | Known linters + status (available / not_installed) for this project |
| `run_linter` | `(linter_id, paths=None)` | Run one linter now; returns structured findings and refreshes the panel |

### Tasks (`.vscode/tasks.json`)
| Tool | Signature | Purpose |
|------|-----------|---------|
| `list_tasks` | `()` | Tasks defined in the project's tasks.json |
| `run_task` | `(name)` | Run a task by label in a new terminal tab |
| `create_task` | `(label, command, type="shell", group=None, args=None)` | Add/update a task (VSCode format); update-in-place by label |

### Notes, issues, session summaries
| Tool | Signature | Purpose |
|------|-----------|---------|
| `add_note` | `(name, content)` | Create or append to `notes/<name>.md` |
| `create_issue` | `(title, body="")` | Create a GitHub issue and refresh the Issues panel |
| `set_session_summary` | `(content, title="")` | Save a résumé / next-session handoff for this project |
| `get_session_summary` | `()` | Return the last saved session summary |

### Cross-project coordination
| Tool | Signature | Purpose |
|------|-----------|---------|
| `list_projects` | `()` | All projects registered on this machine |
| `resolve_project` | `(hint)` | Resolve a name/hint to local path + remote identity |
| `send_message` | `(to, subject, body, refs=None)` | Send an inter-project message |
| `list_messages` | `(box="inbox", status=None)` | List message threads involving this project |
| `reply_message` | `(thread_id, body)` | Reply to a thread |
| `resolve_message` | `(thread_id, status="done")` | Set a thread's status (open/in_progress/done/rejected) |

### Worktrees (multi-agent orchestration)
| Tool | Signature | Purpose |
|------|-----------|---------|
| `create_worktree` | `(task_name, branch="", base="")` | Create + register a worktree of this project for a task |
| `list_worktrees` | `()` | This project's linked worktrees |
| `preview_merge` | `(branch)` | Check whether a branch merges cleanly (no working-tree touch) |
| `merge_worktree` | `(branch)` | Merge a worktree branch into the current branch |
| `report_worktree_complete` | `(summary, tests="", review="")` | Report a worktree's task complete to its parent |
| `list_worktree_reports` | `()` | Completion reports from this project's worktrees |
| `resolve_worktree_report` | `(branch)` | Clear a worktree's report after merging |

### GUI test harness (headless)
Drive and screenshot **another** project's GTK/Qt GUI in an isolated headless Wayland
compositor (see [plan-mcp-integration.md](plan-mcp-integration.md) Part B).

| Tool | Signature | Purpose |
|------|-----------|---------|
| `gui_launch` | `(cmd, width=1280, height=800)` | Launch a GUI app in a headless compositor → `handle` |
| `gui_screenshot` | `(handle)` | Capture the current frame as PNG |
| `gui_stop` | `(handle)` | Tear down the app + compositor |
| `gui_snapshot_tree` | `(handle)` | Accessibility tree (roles, names, extents) |
| `gui_click` | `(handle, role=None, name=None, nth=0)` | Click a widget by role/name |
| `gui_type` | `(handle, text, role=None, name=None, nth=0)` | Set an editable widget's text |
| `gui_do_action` | `(handle, role=None, name=None, action=None, nth=0)` | Invoke a named a11y action |
| `gui_pointer` | `(handle, x, y, button="left", action="click", dy=0)` | Pointer action at screenshot coordinates |
| `gui_key` | `(handle, combo=None, text=None)` | Send a key combo or type text |

## Notes

- Tools act on the **window that owns the server** — "this project". Cross-project reach is only
  via the coordination tools (`list_projects` / messages) and the worktree tools.
- The tool set evolves; the authoritative list is the `@mcp.tool()` functions in
  `src/services/mcp_server.py`. Regenerate this table's signatures from there if it drifts.
