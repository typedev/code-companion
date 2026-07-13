# Local Dispatch — attach to a live desktop session from a laptop (app-native)

Continue a Claude session on the couch: the `claude` process keeps running inside
its tmux session on the desktop; the laptop is a thin client that drives it and
mirrors read-only state over the LAN. **Live attach (takeover)**, not migration —
tool execution stays on the desktop, so the working tree/git stay consistent.

Fully **app-native, no ssh**: authorization is in-app (Allow / Revoke / per-device
token) and it works out of the box after a package install. Full design +
rationale: `~/.claude/plans/lexical-wibbling-pudding.md`.

## Architecture

```
Desktop PM (holds ManagerLock)
  ├─ zeroconf advertise  _codecompanion._tcp  TXT{device_id, hostname, port}   [Ph4]
  └─ dispatch broker (per-device token gated)
       ├─ HTTP  :dispatch.port        POST /pair · GET /sessions               [done]
       ├─ raw-TCP :dispatch.port+1     PTY bridge (framed, stdlib)             [done]
       └─ HTTP  /{name}/mcp            read-only MCP proxy                     [Ph3]

Laptop PM
  ├─ zeroconf browse → "Machines on this network" (paired + free sessions)     [Ph4]
  └─ click free session → RemoteSessionWindow                                  [Ph5]
        ├─ VTE = TerminalView(argv=[python -m src.dispatch_client host port token name])
        └─ read-only panels via mcp_client → broker proxy
```

**Transport note (deviation from the approved plan):** the PTY channel is a **raw
framed TCP protocol** (`src/dispatch/protocol.py`), stdlib-only on both ends — so
**no `wsproto`/`websockets` dependency is vendored**. The HTTP control API + MCP
proxy run on the already-vendored uvicorn/starlette. Two ports: `dispatch.port`
(HTTP) and `dispatch.port + 1` (PTY).

## Checkpoints

- [x] **Phase 0 — identity + settings.** `src/services/device_identity.py`
  (`get_device_id`/`get_device_name`, persisted uuid4 in `<config>/device.json`);
  `dispatch.*` block in `DEFAULT_SETTINGS`. *Verified: stable id across restarts.*
- [x] **Phase 1 — PTY bridge + relay client.** `src/dispatch/pty_bridge.py`
  (asyncio: `tmux attach` on a PTY with a controlling tty via `TIOCSCTTY`, byte
  pumps, RESIZE→`TIOCSWINSZ`, detach-not-kill) + `src/dispatch_client.py`
  (select loop, raw mode restored on every exit, self-pipe SIGWINCH) +
  `src/dispatch/protocol.py` (framing). *Verified end-to-end against a real
  `cc-*` session: bidirectional I/O, size propagation, live resize, clean client
  exit, **session survives detach**, auth rejects (bad token/session).*
- [x] **Phase 2 — broker + pairing/allowlist.** `src/services/dispatch_broker.py`
  (uvicorn HTTP + raw-TCP PTY on one background loop; `POST /pair` with injected
  Allow-prompt; `GET /sessions` bearer-gated with held-state from
  `tmux list-clients`) + `src/services/paired_devices.py` (per-device token
  store, `0600`). New helpers in `claude_session.py`: `managed_tmux_conf()`,
  `session_clients()`. *Verified headless: 401 without token, pairing +
  idempotent re-pair, held-state flip on attach, PTY attach through the broker,
  wrong-token reject, `0600` perms, graceful shutdown.*
- [ ] **Phase 3 — MCP reverse-proxy + `mcp_client`.** Broker `/{name}/mcp` reaches
  the session's loopback MCP (`CC_MCP_PORT`/`CC_MCP_TOKEN` via
  `claude_session.session_env`); `src/services/mcp_client.py` for the read-only
  tools. *Open design choice: transparent SSE proxy vs broker-as-MCP-client
  exposing simple JSON.*
- [~] **Phase 4 — zeroconf + laptop discovery.** *Service layer done & proven
  headless:* `src/services/dispatch_discovery.py` (`DispatchAdvertiser` /
  `DispatchBrowser`, `_codecompanion._tcp`) — two instances discover each other
  with real LAN IP + clean add/remove. `src/services/dispatch_api.py` (client
  `pair`/`list_sessions`, urllib) + `src/services/remote_tokens.py` (client-side
  token store, `0600`) — proven: pair→store→list→revoke-rejects→forget.
  `zeroconf` added (dev dep; **distro dep `python3-zeroconf`** for packaging — 18
  compiled `.so`, not vendorable; pulls pure-python `ifaddr`). *Remaining: PM UI
  wiring (below).*
- [x] **Phase 5 — RemoteSessionWindow (terminal) + `--remote`.**
  `src/remote_session_window.py` (terminal via `dispatch_client` argv +
  reconnect page; no local services) + `main.py --remote host:port:token:session`.
  *Verified: imports, robust `--remote` parse (url-safe token survives), and the
  full relay→broker→tmux chain (Phases 1-2). GTK render itself = user's VM test
  (headless cage collides with the live compositor).*
- [x] **PM GUI wiring (finishes Phase 4).** `src/widgets/dispatch_panel.py` —
  self-contained `DispatchPanel` the PM embeds: owns broker+advertiser (server,
  gated on ManagerLock owner) + browser (client); "Machines on this network"
  expander list → expand peer → pair (if needed) / list sessions → activate a free
  session → `Popen(main.py --remote …)`; incoming "Allow this device?" Adw dialog
  bridged from the broker loop via `loop.call_soon_threadsafe`. PM changes:
  captured `self._manager_owner = self._manager_lock.acquire()` (**fixed
  `project_manager.py:135`**), embed panel after the project list, `stop()` on
  destroy. Preferences: "Local Dispatch" enable switch (AI page). *Verified
  headless: widget construction, peer/session/error rendering, preferences group,
  and full panel start/stop lifecycle (broker binds, browser+advertiser up, clean
  teardown + port freed). Live PM render + true two-machine flow = user's VM test.*
- [ ] **Phase 6 — deferred.** True distributed lock; output forwarding
  (ports/artefacts/screenshots); wss/TLS with per-device pinned cert.

## Pitfalls (still live)
- Broker start must gate on **ManagerLock held** and `dispatch.enabled` (one
  broker/machine); `project_manager.py:135` currently ignores `acquire()`.
- `pair_prompt` shows a GTK dialog on the main thread while the broker awaits on
  its own loop → bridge with a future via `loop.call_soon_threadsafe`.
- Broker binds `0.0.0.0` → note the host firewall in user docs.
- Never recompute a remote session name locally (`session_name` is realpath-keyed).

## Verify (two machines on 192.168.1.x)
Desktop: live `cc-*` session + `dispatch.enabled`. Laptop PM: desktop appears →
click free session → first connect raises "Allow this device?" → Allow → remote
window with live terminal + read-only panels; open the desktop workspace for that
project → shows "attached remotely"; close laptop window → desktop session
survives; Revoke on desktop → laptop `/sessions` returns 401.
