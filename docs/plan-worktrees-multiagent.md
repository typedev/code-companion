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

## Stage 1 — PM sorting (shippable NOW, independent of worktrees)

The single most-wanted PM improvement, useful immediately even before any
worktree work: **most-recently-opened project at the top (MRU), rotating down.**
Simple and predictable — opening a project bumps it to the top; status is shown
by the colored live/attention dots, not by the sort.

- [x] Stamp a `last_opened` timestamp **on `ProjectWindow` startup**
      (`ProjectRegistry.mark_opened`, called at `project_window.py` after
      `register_project`); atomic `_save`. Covers CLI `--project` opens.
- [x] PM sorts via a `Gtk.ListBox` sort func (`_sort_rows`): present before missing,
      then `last_opened` desc; never-opened → bottom (epoch 0). `_load_projects` reads
      `get_projects()` and stamps `row.last_opened`.
- [x] Re-sort in place on PM `notify::is-active` (regaining focus) — **not** on the 4s
      tick. `tests/test_project_registry_mru.py` (5) + live GTK sort-func smoke. DONE.

> MRU-by-open, not frequency and not session-activity recency — the author wants
> "the one I just opened is on top." A single `last_opened` field is enough.

## Stage 2 — Worktree correctness base

- [ ] `FileMonitorService` worktree support: when `project_path/.git` is a **file**,
      read its `gitdir:` pointer; watch `HEAD`/`index`/`logs/HEAD` in the resolved
      per-worktree gitdir and shared refs (`refs/`, `packed-refs`, `logs/`) in the
      common gitdir (`git rev-parse --git-common-dir`). Today it requires `.git` to
      be a directory, so worktree monitors are silently skipped.
- [ ] Worktree self-detection in `ProjectWindow`: header badge
      "worktree of <parent> · <branch>". (pygit2's `discover_repository` already
      handles the `.git` file.)
- [ ] Sync exclusion: `resolve_project_identity` keys on origin/root-commit, so a
      worktree collides with its parent's sync slot — exclude worktree-registered
      projects from sync (detect via `.git`-is-file).

## Stage 3 — Worktree lifecycle & provisioning

- [ ] Registry model: extend `ProjectRegistry` with
      `worktrees: {parent_path: [{path, name, branch}]}` (+ migration). Helpers:
      `get_worktrees`, `is_worktree`, `get_parent_project`.
- [ ] **New Worktree** dialog from the parent card's ⋮ menu: one input = task name
      → derived branch `feature/<slug>` (editable) + sibling path `<parent>--<slug>`
      (editable). On confirm, a background pipeline (`git worktree add -b …`) with
      per-step progress; on success register + optionally open its window.
- [ ] **Remove Worktree** (from the worktree row's ⋮): refuses if dirty or has
      unpushed/unmerged commits unless the user confirms a destructive override;
      then `git worktree remove` + optional branch delete + unregister.

## Stage 4 — PM as mission control (worktree visibility)

- [ ] Worktrees render as **indented child rows (layout A)** under the parent card,
      in **creation order (no sorting)** — there won't be many and the live/attention
      dot + badges are enough to tell them apart. Badges: branch, dirty ●,
      ahead/behind **vs the parent's branch**, last agent-activity time, live/attention dot.
- [ ] With Stage 1's MRU sort, the project you're working floats up **with its
      worktree rows**, so you just watch their dots.
- [ ] Double-click a worktree row → opens it in its **own window** (existing flow;
      per-path locks already allow parent + worktrees open at once).
- [ ] Worktree row ⋮ menu: **Merge back** (Stage 5 button, later), **Remove**, **Open**.

## Stage 5 — Completion → review → message → merge (the core flow)

The integration flow, built on the message system + an agent, with the reviewer
as a **separate step before completion**.

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
