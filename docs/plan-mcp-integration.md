# MCP Integration & Native GUI Test Harness — Implementation Plan

**Status**: **Part A COMPLETE** (A1–A5, incl. `mcp.enabled` Preferences toggle) and
**Part B COMPLETE** — the GUI test harness does launch / screenshot / snapshot_tree /
click / type / do_action / pointer / key / stop against a headless cage compositor.
Coordinate + keyboard input landed 2026-07-10 via the **wlroots virtual-input
protocols**: a built-in raw-wire Wayland client in `gui_agent.py` holds ONE
persistent `zwlr_virtual_pointer_v1` (absolute motion), and `wtype` drives the
virtual keyboard — NOT the originally planned `ydotool`: uinput injection can never
reach a `WLR_BACKENDS=headless` compositor because it reads no input devices at all.
`wlrctl` also doesn't work for the pointer: it creates/destroys the device per
invocation, the seat capability flaps, and the GTK client never has a bound
`wl_pointer` at event time — hence the persistent in-agent device.
cage 0.3.0 exposes `wlr_virtual_pointer_manager_v1` / `wlr_virtual_keyboard_manager_v1`.
The role/name matcher now collects all matches, prefers actionable nodes (wrapper
widgets no longer shadow the real target), supports `nth` and role aliases
("push button" → "button"). (Updated 2026-07-10.)
**Depends on**: Phase 1 (data safety) ✅ and Phase 2 (async layer) ✅ — both code-complete.
**Parent roadmap**: `docs/plan-stability-roadmap.md` (Phase 7). This document is the
detailed, de-risked implementation plan for that phase, **plus** a new capability
(Native GUI test harness) that reuses the same MCP infrastructure.

> **Why one document**: the local MCP server (Part A) and the GUI test harness
> (Part B) are not two projects — Part B is a set of MCP tools hosted by the same
> server, sharing its lifecycle, threading discipline, and auth. Build A first;
> B plugs into it.

---

## Guiding principles (unchanged from roadmap Phase 7)

- **Scope**: only tools that act on the *running app* or on *another project's live
  GUI*. No generic file/git/search tools — Claude already has those natively.
- **Transport**: Streamable HTTP on `127.0.0.1`, one server per `ProjectWindow`
  (NON_UNIQUE process → own random port → free per-window isolation).
- **Threading**: server in a background thread with its own asyncio loop; every tool
  handler marshals to the GTK main thread via a `call_on_main(fn, timeout)` helper
  (reuse the Phase 2 async layer). Never touch GTK from the server thread; never
  block the main loop on the server.
- **No in-app confirmation UI** for mutating tools — Claude Code's own MCP
  permission system covers that.
- **No commits without the user's approval** (project rule).

---

# Part A — MCP Control Surface

## A0. Confirmed auth design (de-risked 2026-07-07)

Verified against the December 2025 Claude Code docs: the `--mcp-config` schema for
`type: "http"` **supports custom `headers` with `${VAR}` env interpolation**, and
`--strict-mcp-config` loads *only* our config (ignoring user/project scopes).

Therefore the per-window auth is:

1. On Claude-tab launch generate `token = secrets.token_urlsafe(32)` and pick a free
   port (`socket.bind(('127.0.0.1', 0))`).
2. Write a temp MCP config (per window, in the app's runtime dir) that references
   the secret **by env var**, so the plaintext token never lands on disk:
   ```json
   {
     "mcpServers": {
       "code-companion": {
         "type": "http",
         "url": "http://127.0.0.1:${CC_MCP_PORT}/mcp",
         "headers": { "Authorization": "Bearer ${CC_MCP_TOKEN}" }
       }
     }
   }
   ```
3. Launch `claude --strict-mcp-config --mcp-config <file>` with `CC_MCP_TOKEN` and
   `CC_MCP_PORT` exported in the child's environment.
4. Server middleware checks `Authorization: Bearer` on every request → 401 on
   mismatch.
5. On window close / Claude exit: delete the temp config, stop the server, free the
   port.

**Launch-mechanism note**: today the app doesn't `spawn` claude directly — it feeds
`claude\n` into a VTE shell after 500 ms (`terminal_view.py:61-62`,
`project_window.py:952-955`). So the env vars must reach the **VTE shell**, not just
the config file. Options (pick at 7.2): pass env at `spawn_async` PTY setup, or
`export CC_MCP_TOKEN=… CC_MCP_PORT=…` before the `claude …` line. Prefer PTY env so
the secret isn't echoed into the terminal scrollback.

## A1. Server infrastructure — `src/services/mcp_server.py` ✅ (commit `1f6d78b`)

- [x] FastMCP (Python MCP SDK), streamable HTTP, background thread + asyncio loop.
- [x] Lifecycle bound to `ProjectWindow`: start on window init (if `mcp.enabled`),
      stop on destroy.
- [x] `call_on_main(fn, timeout=5)` marshaling helper (via `GLib.idle_add` +
      `concurrent.futures.Future`), used by every tool handler.
- [x] Bearer-token middleware.
- [x] **New dependency**: `mcp>=1.28,<2` added to `pyproject.toml`.
- **Acceptance**: ✅ verified — real MCP-client round-trip (401 without token, tools
  listed, both return); clean `stop()` frees the port in ~0.1s; a raising tool maps to
  an MCP error, not a hang. Locked by `tests/test_mcp_server.py`.

## A2. Registration & settings ✅

- [x] Generate the temp MCP config + token/port at Claude-tab launch; wire env into
      the VTE PTY; launch with `--strict-mcp-config --mcp-config`. (commit `1f6d78b`)
- [x] Setting `mcp.enabled` (default `true`); when off, launch `claude` bare.
- [x] **Preferences toggle** for `mcp.enabled` — `Adw.SwitchRow` in the AI page
      (`preferences_dialog.py`, "MCP Control Surface" group); applies on the next Claude
      session start (read at `project_window.py:988`).
- **Acceptance**: ✅ env/config wiring verified in a headless GUI run; `/mcp` lists
  `code-companion`; the toggle writes `mcp.enabled` and off → bare `claude`.

## A3. Tools v1 — read & present ✅ (increments 2–3)

| Tool | Effect | Status |
|------|--------|--------|
| `get_workspace_state()` | Active file, cursor line, open tabs + dirty flags | ✅ inc1 |
| `notify(message)` | Toast + desktop notification if unfocused | ✅ inc1 |
| `get_selection()` | Current editor selection (path, range, text) | ✅ inc2 |
| `get_problems(path?)` | Current ruff/mypy findings (cached; `has_run` flag) | ✅ inc2 |
| `list_tasks()` | Names from tasks.json | ✅ inc2 |
| `open_file(path, line?, end_line?)` | Open tab, scroll, highlight range | ✅ inc3 |
| `show_diff(path)` | Open working-tree diff view | ✅ inc3 |
| `show_commit(hash)` | Open commit detail tab | ✅ inc3 |

- **Acceptance**: ✅ each tool covered by `tests/test_mcp_server.py` (33 tests). Read
  tools also proven end-to-end against **real GTK widgets** via an official MCP client
  (scratchpad harness); `select_line_range` verified on a real `GtkSource.Buffer`.
- **Notes**: read tools never block the main loop (no linter re-runs — `get_problems`
  reads the panel cache). UI tools return `{"ok": bool, ...}` and normalize paths
  per-tool (`open_file` absolute, `show_diff` project-relative).

## A4. Tools v1 — mutating (explicit, few) ✅ (increment 4, commit `53a314d`)

- [x] `create_issue(title, body)` — via `IssuesService.create_issue`; refreshes the
      Issues panel. Blocking network runs on the FastMCP worker thread (not
      `call_on_main`); catches `AuthenticationRequired` / `GitHubError`.
- [x] `run_task(name)` — resolves the label via `TasksService`, applies
      `substitute_variables`, reuses `ProjectWindow._on_task_run` (new terminal tab).
- [x] `add_note(name, content)` — create-or-append `notes/<name>.md` via
      `atomic_write_text`; `NotesPanel` auto-refreshes via `FileMonitorService`.
- **Acceptance**: ✅ covered by `tests/test_mcp_server.py`; all 11 Part-A tools verified
  via an official MCP-client `tools/list`.

## A5. Hook hybrid ✅ (increment 5)

- [x] Plain-HTTP `POST /refresh` endpoint alongside MCP on the same port. Implemented as
      a pure-ASGI `_RefreshEndpoint` composed under the bearer-auth layer
      (`uvicorn → _BearerAuthMiddleware → _RefreshEndpoint → mcp_app`), so it inherits the
      token auth and leaves the MCP session lifespan intact. Body `{"target": "git" |
      "issues" | "notes" | "all"}` (default `all`) → refreshes the matching panels on the
      GTK main thread; responds `{"refreshed": [...]}`.
- **Acceptance**: ✅ `POST /refresh` with the token → 200 + refreshed list; without →
      401; `/mcp` still round-trips (delegation/lifespan intact). Locked by
      `tests/test_mcp_server.py`.

### PostToolUse hook snippet

Panels also refresh when the model changes state directly in the terminal (no tool call).
Add to the project's `.claude/settings.json` — it uses the `CC_MCP_PORT` / `CC_MCP_TOKEN`
env vars already injected into the `claude` process:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "cmd=$(jq -r '.tool_input.command // empty'); case \"$cmd\" in *\"git commit\"*) t=git;; *\"gh issue create\"*) t=issues;; *) exit 0;; esac; curl -s -m 2 -X POST -H \"Authorization: Bearer $CC_MCP_TOKEN\" -H 'Content-Type: application/json' -d \"{\\\"target\\\":\\\"$t\\\"}\" \"http://127.0.0.1:$CC_MCP_PORT/refresh\" >/dev/null 2>&1 || true"
          }
        ]
      }
    ]
  }
}
```

The hook reads the Bash command from the PostToolUse payload (`jq`), maps `git commit` →
`git` and `gh issue create` → `issues`, and POSTs to `/refresh`; anything else exits 0.
It fails open (`|| true`, `-m 2`) so a stopped server never blocks the tool. v1 is
**documentation only** — the app does not write this hook itself.

---

# Part B — Native GUI Test Harness

**Goal**: let the assistant launch, drive, and *visually inspect* another project's
native GUI (GTK/Qt) — the desktop equivalent of what Playwright gives web UIs. This
is unique IDE territory: Claude has no native way to run and "look at" a GTK4 app.

**Status of the approach**: **validated end-to-end** on Fedora 44 / GNOME 50 Wayland
(working spike in `scratchpad/gui_spike/`, 2026-07-07). The full loop — launch →
screenshot → read AT-SPI tree → semantic action → re-screenshot → verify — works.

## B0. Why "own the compositor"

Wayland isolates clients: no app may screenshot or inject input into another's
window. GNOME 50 (the dev machine) is the most locked-down case — `gnome-screenshot
-w` is dead since GNOME 49, private screenshot APIs are caller-restricted, and
`wlr-screencopy` / virtual-keyboard protocols are refused. The only robust, portable
path is to run the app-under-test **inside a headless wlroots compositor the harness
owns** (`cage`). Inside it we are the authority — no portal prompts — and it's
identical across Fedora/Ubuntu/Arch, so the harness is distro-agnostic.

## B1. The validated launch recipe (each step is a confirmed gotcha)

Encode this in a service (e.g. `src/services/gui_harness.py`), not shell scripts:

1. `dbus-run-session` → private session bus (isolates from host GNOME).
2. `cage` with `WLR_BACKENDS=headless`, `WLR_LIBINPUT_NO_DEVICES=1`,
   `WLR_HEADLESS_OUTPUTS=1`, and a unique `WAYLAND_DISPLAY=wayland-cc-<pid>`.
   cage kiosk-fullscreens its client → **set output resolution to the desired
   window size** so screenshots frame the window, not a 1280×720 letterbox.
3. Launch `at-spi-bus-launcher --launch-immediately --a11y=1` **manually** — D-Bus
   autostart of `org.a11y.Bus` fails under the private bus (service uses
   `SystemdService=`).
4. Launch `at-spi2-registryd` **bare** — its .service uses `--use-gnome-session`,
   which hangs headless. Without it, `Atspi.get_desktop(0)` reports -1 children.
5. Launch the target app with `GDK_BACKEND=wayland`. **Do NOT set
   `GTK_A11Y=atspi`** — GTK 4.22 rejects the explicit value; atspi is the default
   once the a11y bus is up.

## B2. Two-channel control

- **Semantic (primary) — `gi.repository.Atspi`**: read the widget tree (roles,
  names, extents), act via `Atspi.Action.do_action(node, i)` and
  `Atspi.EditableText.set_text_contents(node, txt)`. These invoke the widget's own
  callback (no coordinates) and work identically on Wayland/X11. Gotchas: identify
  the app by CONTENTS (it registers as the process name, e.g. "python3"); role is
  `button` not `push button`; use the GI interface idiom `Atspi.Iface.method(node,
  …)`, not pyatspi `node.queryAction()`.
- **Vision — `grim`**: capture the headless output to PNG; the assistant reads it to
  judge layout/spacing/contrast/truncation (usability lives only in pixels, not in
  the a11y tree) and to reason about coordinates when needed. Cap render width
  (~1280px) to keep the grounding tax low.
- **Coordinate fallback — `ydotool`** (uinput): for canvas/custom-drawn widgets and
  GTK4's missing Table/TableCell/Image AT-SPI interfaces. Needs `/dev/uinput`
  access (`input` group + udev rule + `ydotoold`); document, don't hard-require.

## B3. MCP tools (hosted by the Part A server)

**Implemented** in `src/services/gui_harness.py` + `src/services/gui_agent.py`
(in-cage session agent, D2), wired as MCP tools in `src/services/mcp_server.py`.
Increment 1 (commit `92eb44b`) = launch/screenshot/stop lifecycle; increment 2
(commit `0226ce3`) = the AT-SPI semantic layer.

- [x] `gui_launch(cmd, width?, height?)` — bring up the headless stack + app; return
      a handle. Auto-teardown via `McpServer.stop()` → `GuiHarnessManager.stop_all()`.
- [x] `gui_screenshot(handle)` — grim PNG returned as a FastMCP `Image`.
- [x] `gui_snapshot_tree(handle)` — recursive AT-SPI dump (role, name, extents).
- [x] `gui_click(handle, role?, name?)` / `gui_type(handle, text, role?, name?)` —
      semantic via AT-SPI (flat role/name params).
- [x] `gui_do_action(handle, role?, name?, action?)` — explicit action invocation.
- [x] `gui_stop(handle)` — teardown (kills app, registryd, a11y bus, cage tree).
- [ ] Coordinate fallback (extents + `ydotool`) for canvas/custom widgets — **deferred**
      to a B3-impl increment; `ydotool` is not installed on the dev box.
- **Acceptance**: ✅ verified end-to-end through the real `GuiHarnessManager` against a
  headless cage: launched a GTK4 sample app, `gui_click` by role+name flipped its label
  to `Clicked!` (confirmed by a follow-up `gui_snapshot_tree`), teardown left the host
  session untouched. Logic-level unit tests in `tests/test_gui_harness.py`.

## B4. Dependencies (already added to installer, optional/non-fatal)

`cage`, `grim`, `ydotool` — added to `install.sh` (`install_gui_test_deps`, all three
package managers), `INSTALL.md`, `README.md`, plus `wlr-randr` (D3 output sizing).
Ubuntu caveat: install from repos, not Snap
(confinement blocks `/dev/uinput` + Wayland sockets). `at-spi2-core` (bus launcher +
registryd) and `gi.repository.Atspi` ship with the GTK stack.

---

## Suggested sequencing

1. **Part A** (MCP server) first — everything else hangs off it.
   - v0.8.3 / v0.9 per the roadmap's version mapping.
2. **Part B** (GUI harness) as a follow-on capability once A1/A2 are solid.
   - Slots in as a new roadmap phase; does **not** depend on Phase 6 (worktrees).
3. Worktree-orchestration tools (roadmap 7.6) remain deferred with Phase 6.

## Resolved decisions (2026-07-07)

### D1 — Token delivery: VTE `envv`, merged with `os.environ`
`Vte.Terminal.spawn_async` takes `envv: Optional[list[str]]` (confirmed via GI
introspection). Pass `CC_MCP_TOKEN` / `CC_MCP_PORT` there — the secret lives in the
process environment, never typed, so it can't leak into terminal scrollback.
**Caveat**: `envv` *replaces* the environment (exec semantics), it does not merge —
build it as `[f"{k}={v}" for k,v in os.environ.items()] + ["CC_MCP_TOKEN=…",
"CC_MCP_PORT=…"]`, or the shell loses `PATH` etc. **Lifetime**: generate token+port
at `ProjectWindow` init (same lifetime as the server), so they exist before the
`TerminalView` spawns its shell. Per-window token is enough isolation (each
NON_UNIQUE process = one window); no per-session rotation needed.

### D2 — Part B runtime: one persistent "session agent" per target
Per-screenshot relaunch would reset app state every call — unusable for
drive→inspect→drive loops. Instead, one long-lived subprocess tree per `gui_launch`
handle: `dbus-run-session → cage → session-agent`. The **session-agent runs inside
the compositor + a11y bus** (where the spike proved everything works) and listens on
a local control channel (unix socket, line-delimited JSON). MCP tools send it
commands (`{screenshot}`, `{tree}`, `{click,target}`); it runs grim + Atspi locally
and returns PNG bytes / tree JSON. This avoids the fragile alternative of threading
`WAYLAND_DISPLAY` + `DBUS_SESSION_BUS_ADDRESS` + a11y-bus env into one-shot external
grim/Atspi calls from the server thread. **Teardown**: `gui_stop` or window destroy
kills the `dbus-run-session` PID → the whole tree cascades.

### D3 — Part B output sizing: cage + `wlr-randr` custom-mode (configurable canvas)
cage is a kiosk compositor: it fullscreens its single client to the output size, so
"window size" == "output size". v1: after startup the agent sets the headless output
mode via `wlr-randr --output HEADLESS-1 --custom-mode <WxH>` to a **configurable
canvas** (default e.g. 1280×800); libadwaita apps lay out responsively to fill it —
which is arguably better (tests a real target resolution), and the screenshot is
exactly the window. Add `wlr-randr` to the optional deps. **Upgrade path** (deferred
until a project needs natural-size/multi-window/dialog testing): swap cage →
`labwc`/`sway` (both in repo, stacking/tiling, keep natural window size) and crop the
grim capture to the frame's AT-SPI `Component.get_extents()`.
**Verified 2026-07-07**: on this cage/wlroots build the output is `HEADLESS-1`;
`wlr-randr --output HEADLESS-1 --custom-mode 1000x640` applied cleanly, the app
rendered responsively at that size, and grim produced a 1000×640 PNG. No residual
unknowns.

### D4 — MCP SDK: official `mcp` (FastMCP) under programmatic uvicorn
Use the official `mcp` package (latest **1.28.1**, 2026-06-26, requires Python ≥3.10;
we're 3.12+), which bundles FastMCP + streamable-HTTP. Pin `mcp>=1.28,<2` in
`pyproject.toml` via `uv`. Rather than `FastMCP.run()` (which owns its own blocking
loop), take the ASGI app (`streamable_http_app()`), wrap it with the bearer-token
middleware, and run it under **programmatic uvicorn** in the server's background
thread bound to `127.0.0.1:<port>` — this gives clean control over the thread's event
loop and graceful shutdown (freeing the port on window close is an A1 acceptance
criterion).
