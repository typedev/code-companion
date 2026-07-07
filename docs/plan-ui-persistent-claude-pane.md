# UI Restructure — Persistent Claude Pane (bottom dock)

**Status**: Implemented & verified headless (2026-07-07). Pending user review/commit.
**Precedes**: MCP integration Part A (`docs/plan-mcp-integration.md`). This layout
change must ship before A1 so the future context-tools have an unambiguous "active
editor".

## Context / Why

Today the Claude terminal is one `Adw.TabPage` among the file tabs in a single
`self.tab_view` (`src/project_window.py:880`), created lazily in `_on_claude_clicked`
(line 940). When you type to Claude its tab is selected and every editor is hidden
behind it — so "what did the user select / which file is active" has no spatial
answer. Part A's MCP tools (`get_selection`, `get_workspace_state`, `open_file`,
`show_diff`, …) implicitly assume a VS Code topology where terminal and editor are
visible together.

This change extracts the Claude terminal into a **persistent bottom pane** (always
visible), with the tabs area above collapsible down to just the tab bar. Standalone
UX win (code + Claude output at once) and the topology the MCP tools need.

## Decisions (locked with user)

- **Bottom dock**, VS Code style: tabs on top, Claude pane at bottom.
- **Claude pane always visible**, with a minimum height it can't shrink below.
- **Top (tabs) area collapsible fully** → only `Adw.TabBar` stays visible.
- **Lazy CLI start**: pane always present; `claude` starts on a Start action, not at
  window open. Keeps the future MCP server **session-scoped** (start on Start, stop
  on exit/close). Multi-project isolation is free: NON_UNIQUE → one process per
  project → own ephemeral port + token + `--strict-mcp-config`.

## Target layout

```
content Gtk.Box (VERTICAL)
├── Adw.TabBar            ← always visible (+ collapse chevron end-action)
└── Gtk.Paned (VERTICAL)
    ├── start: Adw.TabView (self.tab_view)   ← collapsible to 0
    └── end:   claude_container              ← min height, always visible
```

Vertical paned: `shrink_start_child=True`, `shrink_end_child=False`,
`resize_start_child=True`, `resize_end_child=False`;
`claude_container.set_size_request(-1, ~220)`.

## Checkpoints

- [x] **1. Settings** (`services/settings_service.py`): added
  `window.workspace_split_position` (260) and `window.workspace_collapsed` (False).
- [x] **2. `_build_content()`**: TabBar stays in outer box; added
  `self.content_vpaned` with `tab_view` (start) + `_build_claude_pane()` (end);
  wired `notify::position` → `_on_workspace_split_changed`; restore split +
  collapsed state.
- [x] **3. Lazy Claude pane**: `_build_claude_pane()` → `self.claude_container` with
  a "Start {adapter}" placeholder. `_start_claude_session()` builds terminal + query
  + snippets into the container. `_on_claude_clicked` → start-or-focus; dropped
  `set_sensitive(False)`. *MCP seam commented in `_start_claude_session`.*
- [x] **4. Exit rework** (`_on_claude_exited`): restores placeholder, resets
  `claude_terminal=None`, closes nothing. *MCP seam commented.* Also added
  `claude_terminal.cleanup()` in `_on_destroy` (persistent pane → kill on window close).
- [x] **5. Remove Claude-as-tab coupling**: deleted `claude_tab_page`, Claude close
  special-case, and `_on_claude_close_response`. `claude_terminal` refs audited.
- [x] **6. Collapse/expand**: `collapse_workspace` / `expand_workspace` /
  `toggle_workspace`; chevron via `tab_bar.set_end_action_widget`; auto-expand on
  `tab_view` `notify::selected-page`.
- [x] **7. Verify** headless (cage + AT-SPI + grim): initial placeholder, collapse
  (tab bar stays, chevron flips), expand, Start (pane swaps to terminal + query +
  snippets, CLI launches), settings persisted. Steps 4/5/6 (auto-expand-on-open,
  resize, exit→placeholder) not scripted but logic is trivial/covered.

## Non-goals

- Vertical-toolbar "C" button unchanged (it toggles the sidebar Claude *history*
  panel, `_on_tab_toggled` line 563 — not the terminal).
- New terminals / commit / session / problems / issue-detail stay regular tabs. Only
  the Claude companion is extracted.

## Follow-on: activity bar moved into the header (2026-07-07)

With the persistent Claude pane in place, the green "Start {adapter}" header button
became redundant (Start lives in the pane; focus via clicking the terminal). At the
user's request:

- Removed the green Claude header button (`self.claude_btn`). `_on_claude_clicked`
  stays (still called from the make-issue flow) and just delegates to
  `_start_claude_session()`.
- Removed the left vertical toolbar strip entirely (`_build_vertical_toolbar` +
  `main_box` no longer holds it) — sidebar now starts at the window edge.
- `_build_activity_bar()` builds Files/Git/Claude/Notes/Problems/Issues as plain
  square flat toggle buttons (2px spacing, not a `.linked` segment), packed into the
  header after the sidebar toggle. Same `_create_toolbar_button` / `_tab_buttons` /
  badge machinery — badges (Git red/yellow, Issues blue) render unchanged.
- `_on_tab_toggled`: clicking an activity button now **auto-shows the sidebar** if it
  was hidden (via `self.sidebar_btn`), since the buttons no longer sit next to it.
- Fixed a pre-existing invalid CSS property in `_setup_badge_css` (`margin-end` →
  `margin-right`; GTK4 CSS only supports physical margins) — the app log is now clean.

Verified headless: new header renders with the linked group + Git badge, no left
strip, no green button; clicking Git/Issues switches the sidebar stack; hiding the
sidebar then clicking Issues re-shows it.

## Verification (real app)

`uv run python -m src.main --project <path>`:
1. Claude pane visible at bottom on open, "Start Claude" placeholder, no process yet.
2. Start → `claude` launches; query editor + snippets appear.
3. Collapse chevron → tabs content hides, tab bar stays; Claude never below min height.
4. With tabs collapsed, open a file → workspace auto-expands.
5. Resize window → Claude keeps ~its height, editor takes the extra.
6. `claude` exits → pane stays, placeholder returns, Start works again.
7. Restart app → split position + collapsed state restored.
