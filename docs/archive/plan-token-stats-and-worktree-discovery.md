# Plan: Token stats detail + external worktree discovery

Small, focused UX pass covering three approved changes.

## 1. Auto-discover externally-created worktrees (PM)

**Problem:** The Project Manager list is built purely from `projects.json`. A worktree
made with a plain `git worktree add` in a terminal (e.g. by a Claude agent) is never
registered, so the PM never shows it. `GitService.list_worktrees()` (parses
`git worktree list --porcelain`) already exists but is only used by the MCP tool.

**Decision:** Auto-register discovered worktrees into `projects.json` (same as the ones
our own `create_worktree` / New-Worktree button create).

**Implementation (`src/project_manager.py`):**
- New `_start_worktree_discovery()` — off-thread, enumerates `list_worktrees()` for every
  registered **parent** repo (skip linked worktrees themselves), collects any worktree
  path not already registered.
- New `_on_worktrees_discovered(found)` — main thread: `registry.register_project(p)` for
  each (idempotent, dedupes by resolved path), then `_load_projects()` once if any added.
- Call sites: after the initial `_load_projects()` in `__init__`, and at the top of
  `_on_active_changed` (so a worktree created while the PM was backgrounded is picked up
  on focus — the existing registry-diff branch can't see it because it's not yet in the
  registry). Discovery is decoupled from `_load_projects` (no recursion).

## 2. Detailed token breakdown in the Sessions panel (not a sum)

**Problem:** The Σ totals line shows one summed number dominated by `cache_read`, which is
misleading. The per-model breakdown already exists in `usage_by_model`.

**Decision:** Expand the Σ line into its parts.

**Implementation (`src/widgets/claude_history_panel.py`):**
- `_update_totals`: build
  `Σ  in <..> · out <..> · cache-w <..> · cache-r <..> · <cost> [· today <..>]`
  by aggregating the four buckets across models (no single grand total shown).
- `totals_label`: enable wrap (line is longer), drop END-ellipsize.
- Per-session row badge stays as-is (summed + tooltip) — only the Σ line changes.
- Fix the misleading `TokenUsage.total` docstring (`src/models/session.py`): it sums all
  four buckets incl. cache, not "input + output".

## 3. Live-session token badge on PM cards

**Problem:** No at-a-glance view of what a running agent is currently burning.

**Decision:** A badge in the git-status badge row, shown **only for live sessions**,
carrying the current (latest) session's tokens + estimated cost. Detailed
in/out/cache-w/cache-r breakdown in the tooltip.

**Implementation (`src/project_manager.py`):**
- `SessionInsightService.get_latest_insight(adapter, path, pid)` already returns the
  newest (currently-active) session's insight — reuse it.
- `_refresh_live_token_badges()` iterates rows: live → background scan, non-live → clear.
- `_start_live_token_scan(row)` off-thread (in-flight guard), `_apply_live_token_badge`
  on main thread, `_render_live_token_badge` / `_clear_live_token_badge`.
- Cadence: driven from the existing 4s `_refresh_live_indicators` tick, throttled to
  ~every 12s **and** fired immediately when the working set changes (session just
  started). Insight parsing is cached by (mtime,size); a growing live file re-parses.
- New `.cc-badge-tokens` CSS pill.

## Verification
- Run the app, create a worktree via terminal `git worktree add`, confirm it appears in PM
  on focus.
- Open a project with history, confirm the Σ line shows the four buckets.
- With a live tmux Claude session, confirm the ⚡ token badge appears on its card and
  clears when the session ends.
