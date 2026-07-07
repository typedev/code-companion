# Session Supervisor — Decoupling Claude Sessions from IDE Windows

**Status**: Design discussion captured 2026-07-07. **Deferred** — parked to continue
MCP Part A / A3 first. Revisit when window-restart-loses-session friction justifies it.
**Related**: `docs/plan-mcp-integration.md` (this revises decision D1), the persistent
Claude pane (commit `b37fab6`), the MCP control surface (commit `1f6d78b`).

---

## The problem

The embedded Claude session lives in a VTE PTY child of the `ProjectWindow` process.
Closing/restarting the window → PTY gets SIGHUP → `claude` dies → session context is
lost. The per-window MCP server is bound to the same process, so a GUI restart drops
both the session **and** the MCP server. This hurts self-development: you can't iterate
on the app without severing your own working session each restart.

## Grounding facts (verified against the code, 2026-07-07)

- `project_manager.py:1004` launches a project via
  `subprocess.Popen([python, -m, src.main, --project, path], start_new_session=True)`.
  `start_new_session=True` **deliberately detaches** the child — the window outlives PM
  and there is **zero** PM↔window link (no IPC, no parent-child lifecycle). So today PM
  is a **fire-and-forget launcher, not a supervisor**.
- The `claude` process runs in a VTE PTY inside the `ProjectWindow` process. Window
  process dies → `claude` dies.
- `project_lock.py` already enforces **one window per project**. This makes "the window
  that owns project X" unambiguous — the invariant any routing/registration needs.

## Key insight (the crux)

The "brain" to detach from the window is **two separable things**:
1. the `claude` **process** (interactive, needs a PTY);
2. the **MCP server**.

Gift of the current design: the **MCP transport is already HTTP over `127.0.0.1`** — it
is network-transparent, so `claude` and the MCP server need not share a process, only a
`port` + `token`.

**Catch:** `claude` reads `--mcp-config` (URL + token) **once at launch**. Restart the
window → new port + new token → the surviving session still points at the dead endpoint,
and Claude Code cannot re-point an MCP server URL mid-session. Therefore *"session
survives restart"* and *"MCP still works"* are both true **only if the endpoint is stable
across restarts**. That single constraint drives the whole design.

## PTY sub-problem

A VTE widget must attach to a PTY in its own process; you cannot cleanly hand a PTY
master fd across processes. The proven answer is a **terminal multiplexer**: run `claude`
under `tmux`/`dtach`, and each window's VTE runs `tmux attach`. The multiplexer keeps the
PTY + process alive independent of any attached client; on window restart the VTE simply
re-attaches (scrollback intact).

## The three tiers (cost/benefit ladder)

### Tier 0 — `/resume` only
Zero code. Restart loses the live session; context is reconstructed from the on-disk
JSONL via `claude --resume` / `--continue`. Baseline fallback.

### Tier 1 — tmux detach + stable (port, token)  ← recommended target
- `claude` runs under tmux (survives window restart).
- A **stable per-session `port` + `token`** is allocated once (owned by PM, or a
  per-project on-disk record). `claude` connects to it once.
- The MCP server **stays in the window** and on restart **re-binds the same port +
  token**. Claude's config stays valid.
- **No IPC routing; `_do_*` handlers do not move** — the window still touches its own GTK
  directly.
- Cost: during the window's restart, tool calls get connection-refused. That maps to an
  MCP **tool error, not a hang** (A1 acceptance criterion, locked by
  `test_call_on_main_times_out`). Window comes back → tools resume.
- PM's role is light: a persistent **session registry** (allocate stable endpoints, list
  live sessions, start/stop). It need not be always-on (state can live on disk), but
  making PM the owner/dashboard matches the original instinct.
- Reuses essentially everything. Touches: (a) PM's launch model, (b) env injection —
  today `CC_MCP_TOKEN/PORT` go into the VTE `envv` (`terminal_view.py`); with tmux they
  must be set in the tmux **session's** environment at create time (where `claude` is
  spawned), not in the attaching VTE.

### Tier 2 — PM as full supervisor with MCP routing  ← the "serious refactor"
- PM hosts a **stable MCP endpoint** and **forwards each tool call over IPC** to whichever
  window currently owns the project. Window restarts → re-registers with PM → PM updates
  its routing target → `claude` never notices → **zero gap**.
- What moves: MCP hosting → into PM; the `_do_*` tool bodies (open_file,
  get_workspace_state, …) → to the window side of an IPC boundary, invoked by the
  forwarder. The code **already** isolates GTK work in `_do_*` methods — the IPC boundary
  falls exactly on that seam.
- Adds a single cross-project dashboard controlling all sessions.
- Cost: a new PM↔window IPC protocol; revises plan decision **D1** ("one server per
  window"). Big, but not a rewrite.

## Recommendation

Target **Tier 1**: it realizes the core idea (PM owns/shows/controls sessions), removes
the pain, and keeps the door to Tier 2 open — the Tier 1 → Tier 2 move is **additive**
(add IPC forwarding on top of an already-stable endpoint), not a rewrite. The key
differentiator to decide before Tier 2 is whether the brief **restart gap** is
acceptable.

## Shared blockers (both tiers)

- `tmux`/`dtach` dependency → add to `install.sh` / INSTALL.md / README (same as the
  earlier "detach" option B).
- `start_new_session=True` currently severs PM↔window; any supervisor role needs PM to
  keep a registry/IPC (light in Tier 1, full in Tier 2).
- Minor VTE+tmux UX: scrollback/mouse handling moves to tmux.
- **Revises `plan-mcp-integration.md` D1**: per-window token → per-**session** stable
  token (Tier 1), or PM-hosted endpoint (Tier 2).

## Open questions to resolve on revisit

1. Tier 1 as target with a Tier 2 seam, or go straight to Tier 2?
2. Is the restart-gap acceptable (decides Tier 1 vs Tier 2)?
3. tmux vs dtach vs abduco (feature vs footprint).
4. Does PM need to be always-on, or is an on-disk session registry enough for Tier 1?
