# Worktree Delegation Protocol — v1 (manual)

## Why

Creating a worktree (`create_worktree`) only runs `git worktree add` and registers the
folder as a project. It does **not** start an agent in the worktree and does **not**
tell the main agent to stop — so the main agent tends to just keep working in the new
worktree itself instead of delegating.

This protocol lets the **main-project agent delegate a task** (a new feature or a
hypothesis to check) to an agent running in the **worktree window**, hand over a
detailed brief so the task is not re-discussed from scratch, and stay autonomous
(the main agent is never blocked waiting). The human keeps control at exactly two
points.

## The two human-gates

```
[GATE in]   brief found → agent asks "Take this into development?" → human OK
     ↓
   ←  autonomous worktree work (human does not interrupt)  →
     ↓
[GATE out]  work done → agent asks "Confirm completion?" → human OK → report_worktree_complete
```

Humans gate **intake** and **delivery**; the agent owns the autonomous middle. The
parent's "⑂ N ready" badge therefore means *human-confirmed ready*, not *agent thinks
ready*.

## v1 is manual

No auto-launch, no SessionStart hook. The human opens the worktree window, starts
Claude, and tells it `проверь сообщения` / `check messages`. The worktree session is
launched with an appended system prompt (`_worktree_system_prompt()` in
`src/project_window.py`) that encodes the worktree-side half of this protocol, so the
agent knows to run the intake gate the moment it is asked to look.

## Flow end-to-end

### Main side (delegator)
1. When the discussion converges, write the detailed brief. Two carriers, use either or
   both:
   - **Inline** — put the full brief in the `send_message` body, or
   - **Plan doc** — commit `docs/plan-<branch>.md` on the branch and point the message
     at it. The plan doc travels with the branch into the worktree (git-carried,
     per-branch, no address collision), so it is the better home for heavy detail.
2. `send_message` the brief. Addressing note (see *Known limitation*): a worktree shares
   the parent's remote, so the recipient resolves to **this repo's own canonical
   remote** — the brief is a self-addressed thread. **Put the target branch (or
   worktree folder name) in the subject** so a worktree can tell which brief is for it.
3. `create_worktree` for the task (if not already created).
4. Continue your own work. You are not blocked. Watch for the "⑂ N ready" badge; when it
   appears, `preview_merge` and integrate via **Merge back…** (or the `merge_worktree`
   MCP tool).

### Worktree side (delegate) — encoded in the appended system prompt
1. **INTAKE gate.** On being asked (`check messages`): call `list_messages` (inbox), find
   the brief for this branch (and read any `docs/plan-*.md` on the branch for full
   detail). Summarize the task for the human and ask **"Take this into development?"** —
   do not start until confirmed. On confirmation, mark the brief thread `in_progress`
   via `resolve_message`.
2. If the brief is unclear, ask the parent via `reply_message` (agent-to-agent). Never
   ask the human to re-explain the task — that is what the parent agent is for.
3. Do the work autonomously.
4. **DELIVERY gate.** When complete: (1) run the feature and confirm it works; (2) run
   `/code-review`; (3) commit; (4) present the result and ask the human to confirm —
   **only after the human confirms**, call `report_worktree_complete`.
5. Never merge or switch branches from the worktree — the parent integrates the branch.

## Message addressing (v2 — distinct worktree addresses)

A linked worktree shares its parent's `origin`, so its **sync identity** (`project_id`,
`canonical_remote`) equals the parent's. To keep briefs point-addressed, the **mailbox
address** is split from the sync identity via `resolve_message_address`
(`src/utils/project_identity.py`):

- Parent / main window / normal project → bare `host/owner/repo`.
- Linked worktree → `host/owner/repo#wt:<branch>` (detached HEAD falls back to bare).

Result: each worktree has its **own inbox and pending badge**; a main→worktree brief has
`from != to`, so parent↔worktree replies **notify** again (`scan_activity` no longer
suppresses them); multiple worktrees are individually addressable. `project_id`, sync, and
the `worktree_reports` completion channel are unchanged (they keep the bare remote).

Edge notes: renaming a worktree branch mid-delegation changes its address (an in-flight
thread orphans — rare); a `repo#wt:branch` thread syncs to another machine but is inert
until that worktree is reconstructed there. Still address by the target branch in the
subject as a courtesy so a glance identifies the worktree.

## Deferred

- Auto brief-check on session start (a `SessionStart` hook, or seeding the brief as a
  file into the worktree at creation) instead of the human typing `check messages`.
- Auto-launching Claude in the worktree on `create_worktree`.
- Auto-pushing the worktree branch so a second machine can reconstruct the worktree with
  a single `git worktree add` (separate cross-machine topic).
