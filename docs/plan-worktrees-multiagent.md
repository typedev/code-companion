# Worktrees & Multi-Agent Orchestration (Phase 6 — redesigned)

## Context

The v1.0 headline feature: run several Claude agents in parallel on one project,
each in its own **git worktree** (own branch, own directory), without them
stepping on each other's files.

The original roadmap (`docs/plan-stability-roadmap.md` §Phase 6) assumed a
**single parent window** that aggregates all worktrees behind a switcher. In
discussion (2026-07-09) we rejected that: the author's tasks are short and need
**frequent replies to agents**, so a switcher adds friction exactly where speed
matters. Our stack already makes the better model nearly free:

- **tmux supervisor** → windows are disposable; a session survives window close.
- **multi-process + `NON_UNIQUE` app + per-path locks** (`project_lock.py`) →
  parent and worktrees already open concurrently, each in its **own window**.

So a worktree is "just another project at a different path", opened in its own
window via the existing flow. **No new window architecture.** Coordination moves
to the **Project Manager**, which becomes the mission-control dashboard for all
agents (across projects *and* worktrees). Merge/integration is handled by the
message system + an agent, not an in-app merge UI.

### What this drops from the original Phase 6
- ❌ In-window "Worktrees" popover + aggregated sessions in a parent window (all
  of the original 6.3 in-window parts) — replaced by PM enhancements.
- ❌ In-app merge/conflict UI as a hard requirement (original 6.4 / deferred 4.4)
  — conflicts are resolved by an agent in the terminal, with a human gate.

### What we keep / add
- Worktree correctness base (`.git`-as-file support, self-detection, sync exclusion).
- Worktree provisioning (create / remove) + registry model.
- **PM as mission control** (sorting, nested rows, attention-first).
- **Completion → review → message → merge** flow built on existing infra.

## Reused infrastructure (almost all of this exists)
- Live/attention dots: `claude_session.live_session_names()`, `session_notify` markers.
- Inter-project mailbox: `message_store` + MCP `send_message`/`list_messages`/
  `reply_message`/`resolve_message`; PM message badges (`_render_message_badges`/`_scan_messages`).
- Per-project handoff: `session_summary_service`; recency: `Updated <relative>` + session mtimes.
- Registry + per-path locks: `ProjectRegistry`, `project_lock.py`.
- "What changed this session" diff (8.3): `SessionView` Changes + `GitService.get_commit_range_diff`.
- Review + verify primitives: the `/code-review` skill, the `verify` skill, the headless GUI harness.

---

## Design decisions (locked in discussion)

1. **One window per worktree**, not a parent switcher. Reuses the existing
   per-path multi-process flow.
2. **PM is the coordination hub.** Live/attention dots become the primary "which
   agent needs me" signal; message badges become the "agent finished, ready to
   integrate" signal.
3. **Merge risk ≠ feature-correctness risk.** Integration is the *safer* step
   (git detects conflicts; a clean merge rarely breaks existing behavior). The
   real risk is whether the new feature is correct — a per-branch review problem,
   handled *before* completion.
4. **Test coverage is incomplete** → no single signal is trusted. The gate is a
   **layered net**: tests (where they exist) + a real behavioral run + an
   independent reviewer's findings + change-shape review + a human gate at
   conflicts/flags.
5. **Reviewer runs as a separate step before completion** (the worktree agent
   triggers it and attaches findings to the completion message) — so the human
   reads a short findings report, not the whole diff.
6. **Manual "Merge back" button** = fast path for clean merges; **non-priority**
   (the agent path is built first).
7. **Structural leverage:** a worktree = one narrow feature = a small, reviewable
   diff; review incrementally per session (8.3), not all at once at the end.

---

## Stage 1 — PM sorting: "Working" on top, MRU below (shippable, worktree-independent)

The most-wanted PM improvement. Refined after real use: pure MRU wrongly bumped a
"just opened to look" project above one with a live agent. Fix: **two groups** —
projects with a **live Claude session on top ("Working"), everything else MRU
below ("All projects")**, as sections in one `Gtk.ListBox`.

- [x] Stamp `last_opened` **on `ProjectWindow` startup** (`ProjectRegistry.mark_opened`,
      after `register_project`); atomic `_save`. Covers CLI `--project` opens.
- [x] Sort func `_sort_rows` key = `(not is_live, is_missing, not is_attention,
      -last_opened)`: live group first (attention-first within), then MRU; missing sink.
      `set_header_func` draws "Working" / "All projects" section headers (only when
      something is live). `_load_projects` reads `get_projects()` + stamps `row.last_opened`.
- [x] `_refresh_live_indicators` stamps `row.is_live`/`is_attention` and re-groups
      (`invalidate_sort`/`invalidate_headers`) **only when the working set changed** —
      never on the bare 4s dot tick. Focus (`notify::is-active`) re-reads open times.
- [x] "Working" = a live tmux Claude session (`claude_session.live_session_names()`),
      so a backgrounded agent (window closed) stays on top. Tests:
      `tests/test_project_registry_mru.py` (5) + live GTK grouping smoke. DONE.

> MRU-by-open, not frequency and not session-activity recency — the author wants
> "the one I just opened is on top." A single `last_opened` field is enough.

## Stage 2 — Worktree correctness base — DONE

- [x] Shared helper `src/utils/git_worktree.py`: `is_linked_worktree`,
      `resolve_worktree_dirs` (→ per-worktree gitdir + common dir, by reading the
      `.git` pointer + `commondir` file, no subprocess), `worktree_parent_root`.
      `tests/test_git_worktree.py` (real `git worktree add`).
- [x] `FileMonitorService` worktree support: resolves the two gitdirs in `__init__`
      (and `_maybe_attach_git_monitors`); per-worktree `HEAD`/`index`/`logs/HEAD`
      watched under the gitdir, shared `refs/`/`packed-refs` under the common dir.
      Verified live: a worktree resolves + attaches monitors (was silently skipped).
- [x] Worktree self-detection in `ProjectWindow` (`_build_sidebar` sets
      `_is_worktree`/`_worktree_parent`); `_update_window_title` shows a
      "⑂ worktree of <parent> · <branch>" subtitle.
- [x] Sync exclusion at the `SyncService._run` choke point + `list_restorable`
      (skip `is_linked_worktree` paths) — surgical, leaves `resolve_project_identity`
      untouched (mailbox identity unaffected). A test documents the id collision.

## Stage 3 — Worktree lifecycle & provisioning — DONE

**Registry-model decision:** the separate `worktrees: {parent: [...]}` structure
from the original roadmap is **skipped** — a worktree is registered as a normal
project (opens in its own window, gets MRU/live-grouping for free) and the
parent↔worktree relationship is **derived** from the filesystem via the Stage 2
helpers (`is_linked_worktree` / `worktree_parent_root`). No schema change, no
migration, always accurate. Stage 4 nesting groups by the derived parent.

- [x] `GitService.add_worktree` / `remove_worktree` / `list_worktrees` (git CLI);
      `slugify` in `git_worktree.py`. `tests/test_git_worktree_ops.py` (4).
- [x] **New Worktree** from the parent card ⋮: dialog derives branch `feature/<slug>`
      + sibling path `<parent>--<slug>` from the task name (both editable; auto-fill
      stops once you edit them). Background `add_worktree` → register + optionally
      open its window. Live smoke: dialog builds + derive correct.
- [x] **Remove Worktree** from the worktree card ⋮ (`is_linked_worktree` swaps the
      menu item): confirm dialog with a "Force" option (git refuses a dirty worktree
      otherwise); background `remove_worktree` → unregister. Branch is kept
      (it's merged/deleted via the Stage 5 flow).

## Stage 4 — PM as mission control (worktree visibility) — DONE

Worktrees are already first-class rows (own dots, badges, ⋮ menu, double-click-open
— they're registered projects), so Stage 4 is just clustering + nesting:

- [x] `_recompute_units` aggregates live/attention/MRU per **unit** (a parent + its
      worktrees, grouped by the derived `_parent_path`). The sort key keeps a unit
      contiguous (parent above its worktrees in creation order) and floats the whole
      unit into "Working" when **any** member has a live session — so a live worktree
      lifts its parent too. Recomputed on load / focus / live-set change.
- [x] Worktree cards render **indented** (margin 38 vs 14) with a faint accent
      left-border (`cc-worktree-child`). Live smoke: `[P, W1, W2, Q]` — unit P
      (parent+worktrees) on top because W1 is live, Q below; headers Working/All.
- [x] Double-click a worktree row opens its **own window** (existing per-path flow).
- [x] Worktree row ⋮ menu: **Remove Worktree** (Stage 3). "Merge back" button is the
      later Stage 5 fast-path.
- Badges (branch/dirty/ahead-behind, activity) come for free via the existing
      per-project status machinery — no extra work.

## Stage 5 — Completion → review → message → merge (the core flow)

The integration flow, built on the message system + an agent, with the reviewer
as a **separate step before completion**.

> **DONE (Option 1).** Key finding during build: the message system addresses by
> canonical git remote, which a worktree **shares** with its parent — so a
> worktree→main message is a self-addressed thread (no notification). Worktree
> completions are local + ephemeral anyway, so the report travels over a **new
> local channel** (`worktree_reports`), not the synced message store. Shipped:
> `GitService.preview_merge`/`merge_branch` (git merge-tree, 4 tests);
> `services/worktree_reports.py` (markdown+frontmatter, 4 tests); an
> `--append-system-prompt` completion protocol for worktree sessions; MCP
> `report_worktree_complete` / `list_worktree_reports` / `resolve_worktree_report`
> (2 tests); a PM "⑂ N ready" parent badge + a "Merge back" button (preview →
> clean merge / conflict guidance → resolve report). 306 tests green.

**5a. Reviewer step (before completion).** Two complementary signals, both required:
- [ ] **Behavioral run** — the worktree agent runs the feature (the `verify` skill /
      GUI harness) + any existing tests, and records *what it ran and observed*
      ("does it actually work"). Covers the untested paths.
- [ ] **Independent code review** — the worktree agent runs **`/code-review` on its
      branch** (reuse as-is; do NOT build a custom reviewer). `/code-review` spawns
      its own review agents with **fresh context** (independence) and
      **adversarially verifies** findings before reporting, so the output is a short
      ranked list of *real* issues, not a flood of false positives — exactly the
      compensation needed when the human can't read the whole diff. Depth knob:
      normal for most branches, `/code-review ultra` for large/risky ones.
- Division of labor: `/code-review` = "is the code correct" (the test-gap risk);
  the behavioral run = "does it actually work". Neither replaces the other. Caveat:
  the review reads the diff — it won't catch "wrong feature / doesn't fit"; that's
  the behavioral run + the human reading the summary.

**5b. Completion message — structured markdown + frontmatter.**
- [ ] The worktree agent sends a **completion message** to `main` via MCP
      `send_message`. Format = **markdown with a YAML frontmatter header** (same
      pattern as `session_summary_service`: `---\nbranch: …\n---\nbody`). Frontmatter
      carries the machine-parsed fields (`kind: worktree_complete`, `branch`,
      `worktree_path`, `task`, `tests`, `review_verdict`); the body is the
      human-readable summary + behavioral-run notes + the ranked reviewer findings.
      One artifact both the author and the main agent read — no separate JSON view.
- [ ] A short instruction in the worktree's `CLAUDE.md`: "when the task is done and
      self-verified, run the reviewer and send a completion message to main —
      **do not merge from here**." (Explicit send when done, not a Stop hook, which
      fires every turn.)

**5c. Integrate from `main`.**
- [ ] `main`'s card shows the existing message badge. You open `main`, read the
      completion summaries + reviewer findings (short), and decide.
- [ ] Conflict detection is deterministic via **`git merge-tree --write-tree`**
      (in-memory merge, no working-tree side effects): "branch A merges clean /
      branch B conflicts in N files" — git's answer, not the agent's guess. Add a
      `GitService.preview_merge(branch)` helper for this.
- [ ] Clean branches → the agent (or the Stage-5 button) merges, runs tests, and
      shows the merge diff (reuse 8.3 machinery). `resolve_message` marks it done.
- [ ] Conflicting branches → **human gate**: the agent surfaces the specific
      conflicts and a proposed resolution; you review closely here (this is where an
      imperfect agent can quietly go wrong).

**5d. Manual button (non-priority).**
- [ ] A "Merge back" button on the worktree row for the clean-merge fast path
      (guarded by `preview_merge`): merge → run tests, no agent needed. Escalate to
      the agent/terminal only on conflicts. Built after the agent path.

## Stage 6 — MCP orchestration tools (after Stages 2–5)
- [ ] `list_worktrees()` — worktrees with branch/dirty/ahead-behind/last-activity.
- [ ] `create_worktree(task_name)` — runs the Stage 3 pipeline; returns the path.
- [ ] `preview_merge(branch)` / merge helpers exposed so an agent can drive the
      Stage-5 flow programmatically.

---

## The de-risking gate (why this is safe with incomplete tests)

No single signal is trusted. A completed branch passes through a **layered net**,
and the human dives into code only where the net flags something:

1. **Behavioral run** (worktree agent) — it *demonstrates* the feature works
   (ran the app / harness), not just "tests pass". Covers untested paths; hard to fake.
2. **Independent reviewer findings** — a second agent's short report; you read the
   report (a page), not the diff (many pages). Primary compensation for test gaps.
3. **Deterministic conflict detection** — `git merge-tree`; integration safety is
   git's answer.
4. **Change-shape review** — 8.3's touched-files/commit list; sprawl beyond the
   stated scope is a red flag without reading lines.
5. **Human gate** — you read summary + findings + shape, run it if unsure, and
   look closely only at conflicts / flagged spots.

Structural: small per-worktree tasks + incremental per-session review keep the
final integration a *conflict + integrate* step, not a first read of a huge diff.

## Verification (of this feature itself)
- Unit: registry worktree model + migration; `GitService.preview_merge` /
  worktree add/remove (git CLI, `build_git_env`, temp repos like `test_git_features`).
- `FileMonitorService` worktree monitors (`.git`-as-file resolution).
- End-to-end (headless GUI harness): create a worktree from a parent card → it
  appears as an indented row → opens in its own window with the worktree badge →
  a completion message lands on the parent → `preview_merge` reports clean/conflict.

## Resolved (was open)
- **Completion payload** → markdown + YAML frontmatter (5b). Human-readable and
  agent-parseable; no separate JSON view.
- **Reviewer** → reuse `/code-review` on the worktree branch as-is (5a); its
  fresh-context + adversarial-verification is the point. No custom reviewer.
- **PM sort** → MRU by a `last_opened` stamp (Stage 1); re-sort on load/focus, not
  on the 4s tick.

## Still open
- Exact frontmatter field set for the completion message (finalize when building 5b).
- Whether `main`'s merge is driven by a saved prompt/skill the author invokes, or a
  fully manual "tell the agent to integrate" — decide when building 5c.
