# Phase 8 — Agent Observability

**Status**: **Phase 8 complete** — 8.1, 8.2, 8.3, 8.5, 8.6 shipped (283 tests green). 8.4 (PM
resume hint) was built then dropped (duplicated the last-session-summary). Only loose end:
open-at-exact-session preselect (deferred — see end).

**Notes from implementation**:
- Token `usage` in the JSONL is **repeated on every content-block line of one assistant
  message** (thinking/text/tool_use share `message.id`) — counted once per id, else it
  triple-counts. `<synthetic>` messages carry all-zero usage and are dropped.
- The insight cache carries a `_schema` version (`session_insight_service._SCHEMA_VERSION`);
  bump it whenever the parser's extraction changes so stat-only cache entries are discarded.
- Cache rates derive from Anthropic's fixed multipliers (write-5m = 1.25× input, read = 0.1×);
  only list input/output rates are stored per model (`model_pricing._BASE_RATES`).
- 8.4 click-to-open opens the project via the existing card double-click; preselecting the
  exact session in the Claude tab is deferred (needs a `--session` arg through main.py).

## Why

Code Companion can view and drive Claude sessions but shows nothing about what a
session **cost** or **accomplished**: no token usage, no price estimate, no list of
files touched, and — from the Project Manager — no "where did I leave off" per
project. Phase 8 (the last v1.0-track feature set before worktrees) closes this.

Decision (locked with the user): cost shows **tokens as the primary number + an
approximate $ estimate** from a bundled per-model price table, clearly marked
"estimate" (prices drift, tokens don't).

## Reused infrastructure (do not reinvent)

- JSONL `usage`: assistant events carry `message.usage.{input_tokens, output_tokens,
  cache_creation_input_tokens, cache_read_input_tokens}` + `message.model`. A session
  can mix models (main + subagents) → bucket tokens **per model**.
- Streaming metadata pass: `HistoryService._parse_session_metadata` (`src/services/history.py`).
- Cache pattern: `ProjectStatusService` (singleton + JSON under `get_config_dir()` +
  `threading.Lock`); staleness via `text_files.capture_stat` / `stat_differs`; durable
  writes via `atomic_write_text`.
- Off-thread: `run_async(widget, worker, on_done, key=…)` (`src/services/async_runner.py`).
- Path→sessions: `claude_paths.project_dir()` / `project_sessions()`.
- Machine-independent project key: `session_summary_service.project_key()`.

## Milestone A checkpoints

### 8.1 — Session insight index (foundation)
- [x] `SessionInsight` + `TokenUsage` dataclasses in `src/models/session.py` (exported in `__init__`).
- [x] `HistoryService.parse_session_insight(session_file)` — single streaming pass:
      per-model token buckets (usage counted once per `message.id`), files touched
      (Edit/Write/NotebookEdit), first/last timestamps, first prompt, last reply, count.
- [x] `HistoryAdapter.get_session_insight` (abstract) + `ClaudeHistoryAdapter` delegation.
- [x] `src/services/session_insight_service.py`: per-project JSON index under
      `get_config_dir()/session-insights/<project_key>.json`, entries keyed by
      `session_id` + `(mtime_ns, size)` stamp + `_schema` version; `threading.Lock`;
      `atomic_write_text`. `get_insight` / `get_project_insights` / `get_latest_insight`.
- [x] `tests/test_session_insight.py` (14 tests: cache hit/miss, multi-model sum, dedup, partial tail, schema invalidation).

### 8.2 — Cost display
- [x] `src/services/model_pricing.py`: per-model list rates + derived cache rates +
      `estimate_cost` (unknown model → tokens counted, cost flagged partial) + `format_cost`.
- [x] Token badge on session rows (`ClaudeHistoryPanel._create_session_row`), off-thread via
      `run_async`; tooltip breaks down input/output/cache + `~$X (est.)`.
- [x] Project + today aggregate line in the Claude panel header (`totals_label`).
- [x] `tests/test_model_pricing.py`.

### 8.4 — "Where we left off" — DROPPED
Implemented then removed at the user's request: a one-line résumé on the PM card
duplicated the existing **last session summary** (`session_summary_service`, shown via
the card's summary button) and read poorly (single truncated line + a large tooltip).
The session-summary surface is the intended "where we left off" affordance instead.
The 8.1 insight index still powers 8.2; only the PM card hint (label, background scan,
`humanize_relative_terse`) was reverted.

### Verification
- Unit: `uv run pytest tests/test_session_insight.py tests/test_model_pricing.py` + full suite green.
- Real data: sanity-check totals against this repo's own `~/.claude/projects/…` sessions.
- GUI (headless `gui_harness`): badges render, header aggregate shows, PM card shows a
  resume hint, no UI freeze on a project with many/large sessions (parsing off-thread).

## Milestone B

### 8.3 — What changed this session — DONE
- [x] `GitService.get_commits_in_range` (`git log --since/--until`, CLI), `get_commit_range_diff`
      (`<first>^..<last>`, root-safe via empty-tree), `get_paths_diff` (uncommitted, path-filtered).
      `tests/test_git_session_changes.py`.
- [x] `SessionView` gains a collapsible **Changes** section (touched files from 8.1 +
      session-range commits), each commit clickable → existing commit detail tab
      (`commit-selected` signal). "Review session changes" → range diff (or uncommitted
      path diff) shown in a reused `DiffView` tab (`show-diff` signal). Computed off-thread.
- Verified on real sessions: the current session correctly correlated its own 4 commits;
  range diff spans the expected files.

### 8.6 — Plan progress from checkboxes — DONE (Notes panel only)
- [x] `src/utils/markdown_tasks.py` — `count_checkboxes` / `count_checkboxes_in_file`
      (`- [ ]` / `- [x]`, any bullet/indent). `tests/test_markdown_tasks.py`.
- [x] `NotesPanel._add_file_row` shows a `done/total` badge on **docs** rows that have
      task checkboxes (green when complete); cheap inline read (docs are small).
- PM-card badge from the plan (the roadmap's optional second half) **intentionally skipped** —
  it duplicates the lean-card guidance that killed 8.4 (see feedback note in the roadmap memory).

### 8.5 — Cross-project prompt search — DONE
- [x] `src/services/prompt_search.py` — `search_prompts(query)` over
      `claude_paths.projects_root()` via ripgrep (grep fallback). Confirms matches against
      the parsed *user* text and filters harness-injected events (`isMeta` /
      `isCompactSummary` / `isVisibleInTranscriptOnly` flags + injected-tag prefixes like
      `<task-notification>`); real project path taken from the event's `cwd`.
      `tests/test_prompt_search.py`. Cut a live "worktree" query from 26 → 4 clean hits.
- [x] `src/widgets/prompt_search_window.py` — a standalone search window (debounced,
      off-thread), results grouped by project; activating a result opens that project.
      Launched from a search button in the Project Manager header.

## Phase 8 status: DONE
Shipped 8.1, 8.2, 8.3, 8.5, 8.6. Dropped 8.4 (duplicated last-session-summary). The only
remaining loose end is opening a result at the **exact session** (both 8.5 and the old 8.4
open the project, not a preselected session) — needs a `--session` arg through `main.py` →
`ProjectWindow`; deferred as a small separate task if wanted.
