# Migration Hardening (survive OS reinstall + auto-sync)

## Progress

- [x] Stage 1 — Registry export always (also retired the dead "backup mode" toggle:
  registry export was the only thing `sync.mode` ever controlled)
- [x] Stage 2 — Auto-sync: startup + periodic (`sync.auto`, `sync.auto_interval_minutes`;
  silent runs never pop dialogs — auth miss parks auto-sync until a manual Sync)
- [x] Stage 3 — App settings sync (allowlisted slice, file-level merge, conflict-free
  first contact; also fixed a latent `SettingsService._deep_merge` bug that let `set()`
  mutate module-level DEFAULT_SETTINGS via shared nested dicts)
- [x] Stage 4 — `SyncState.NOT_SYNCABLE` + "⚠ not backed up" PM badge
- [x] Stage 5 — First-run restore wizard (verified in the GUI harness: fresh HOME →
  wizard; Start fresh → dismissed persists)
- [x] Stage 6 — Docs

## Pre-reinstall checklist (what sync still does NOT carry)

Before wiping the OS, note that after restoring you must manually:
1. **Re-authenticate git/GitHub once** — credentials live in the libsecret keyring /
   `~/.git-credentials`, never in sync. The first manual Sync will prompt.
2. **Re-pair dispatch / LAN file-sync devices** — `device.json`,
   `paired-devices.json`, `dispatch-tokens.json` are per-machine secrets.
3. **`~/.claude.json` beyond the 3 synced fields** (OAuth/account, global MCP
   servers) — re-login to Claude Code; other per-project config re-syncs.
4. **Press Sync (push) once on the old machine before wiping.** The startup run is
   pull-only and every push is manual, so the last local state only leaves the
   machine when you click Sync. (Claude history AND Codex rollouts under
   `~/.codex/sessions` are both carried by that push.)

## Context

The user is planning an OS reinstall. An audit showed the built-in cross-machine sync
covers history/memory/plans/summaries/snippets/rules/messages, but a fresh machine
cannot bootstrap itself: app settings and the project registry don't sync (registry
only in `mode=backup`), and projects without git identity are silently never backed
up. Separately, a live diagnosis showed sync runs ONLY from the manual PM button —
machines chronically diverge (the laptop missed a summary export by 4 minutes and
stayed stale). User-approved scope: (1) auto-sync at PM start + periodic, (2) a
first-run restore wizard, (3) always export the registry, (4) sync the app's own
settings (minus per-machine keys), (5) a "not backed up" badge for identity-less
projects.

Key discovery: the restore flow ALREADY exists end-to-end (PM menu "Restore from
backup…" → `list_restorable` → checkbox dialog → folder picker → `restore_project`
clones + registers → auto-sync materializes history). The wizard is mostly
*sequencing* of existing pieces.

## Stages (independently landable; 1 first, 2/3/4 independent, 5 last)

Step 0: mirror this plan to `docs/plan-migration-hardening.md`; update progress there.

### Stage 1 — Registry export always
- `src/services/sync_service.py` ~:259: move the `_export_registry(repo, syncable)`
  call out of the `sync.mode == "backup"` guard — run on every sync. Semantics stay
  additive-union keyed by project_id (other machines' entries preserved).
- Tests (`tests/test_sync_service.py` harness: `fresh_service`/`make_project`/bare
  repo/HOME swap): sync in default `selected` mode → `global/registry.json` exists in
  the repo with this machine's entries; a second machine's entries survive.

### Stage 2 — Auto-sync (PM start + periodic)
- New settings (defaults in `settings_service.py` + CLAUDE.md table): `sync.auto`
  (true), `sync.auto_interval_minutes` (30).
- `src/project_manager.py`: refactor `_on_sync_clicked` (:1472) into
  `_start_sync(credentials=None, *, silent=False)` — the manual button calls it with
  `silent=False` (unchanged behavior). Silent mode differences ONLY: on
  `AuthenticationRequired` do NOT open the credentials dialog — set
  `self._auto_sync_blocked = True` and show a calm hint in `updated_label`
  ("Sync needs auth — press Sync once"); a successful manual sync clears the flag.
  `result.error == "busy"` in silent mode: ignore quietly (SyncLock already made it a
  no-op). Progress badges/label updates stay as-is (informative, dialog-free).
- Triggers: one-shot `GLib.timeout_add_seconds(5, …)` after `_load_projects` (startup
  sync), plus a repeating timer that fires every 60s and runs `_maybe_auto_sync()`
  when `now - last_run >= sync.auto_interval_minutes` (re-reads the setting each
  fire; no timer re-plumbing on settings change).
- Guards in `_maybe_auto_sync`: `is_configured()`, `sync.auto`, not `self._syncing`,
  not `_auto_sync_blocked`.
- Factor the decision as a pure function (module-level in project_manager or a small
  helper in sync_service): `should_auto_sync(configured, auto_enabled, syncing,
  blocked, minutes_since_last, interval) -> bool` + unit test.

### Stage 3 — Sync the app's own settings
- `src/services/settings_service.py`: two helpers —
  `export_syncable_bytes() -> bytes` (deterministic `json.dumps(..., sort_keys=True)`
  of the ALLOWLISTED slice) and `apply_syncable_bytes(data: bytes)` (merge allowlisted
  keys into current settings via `set()` so the `changed` signal fires and live-apply
  works).
- Allowlist (top groups): `appearance.*`, `editor.*`, `git.*`, `mcp.*`, `ai.*`,
  `sessions.*`, `linters.*`, plus `terminal.auto_activate_env`.
  NEVER synced: `window.*`, `sync.*` (feedback loop!), `dispatch.*`, `manager.*`,
  `terminal.touchpad_pixels_per_click`, `app.version`.
- `src/services/sync_service.py` `_sync_global` (:361-496): add whole-file entry
  `"app-settings.json"` alongside the existing `"settings.json"` (Claude's) —
  same hashed 3-way LWW via `SyncStateStore`/`decide_import`/`decide_export`;
  local bytes come from `export_syncable_bytes()`, import applies via
  `apply_syncable_bytes()` (not a raw file copy — per-machine keys must survive).
  Conflict handling inherits snapshots + `.remote` stash for free.
- Tests (two-machine sim): theme set on A → sync A → sync B → B's
  `appearance.theme` updated AND B's `window.width`/`sync.repo_url` untouched;
  determinism (two exports hash equal).

### Stage 4 — "Not backed up" badge
- `src/models/sync.py`: new `SyncState.NOT_SYNCABLE`.
- `src/services/sync_service.py` `_run` per-project loop (:224-238): when
  `resolve_project_identity(path)` is None and it's not a linked worktree → emit
  `NOT_SYNCABLE` with detail "No git identity — make a commit (or add a remote) so
  this project can be backed up". Worktrees keep NOT_CONFIGURED + existing detail.
- `src/project_manager.py` `_render_sync_badges` (:1439): NOT_SYNCABLE → badge
  "⚠ not backed up" (reuse `cc-badge-syncoff` styling or a muted warn class),
  tooltip = detail. NOT_CONFIGURED still renders nothing.
- Check `ProjectSyncStatus` (de)serialization in the status cache tolerates the new
  state (enum round-trip in `models/sync.py` / `sync_status_cache`).
- Tests: sync over a non-git project dir → NOT_SYNCABLE emitted + cached; worktree
  unchanged.

### Stage 5 — First-run restore wizard
- Trigger in PM after `_load_projects`: 0 registered projects AND
  `manager.restore_wizard_dismissed` not set → present the wizard.
  "Start fresh" sets the dismissed flag (also set it after a successful restore).
- Flow = chain of EXISTING pieces:
  1. Adw.AlertDialog "Set up this machine": body explains restore-from-sync;
     responses [Start fresh] / [Restore from sync…].
  2. If not `is_configured()`: reuse `_show_sync_config_dialog` (:1544) refactored to
     accept an `on_success` callback (entry via `set_extra_child` per the dialog
     text-input gotcha in CLAUDE.md).
  3. Run a sync (bootstraps the clone, fetches `global/registry.json` — Stage 1 makes
     it exist regardless of mode), then invoke the existing `_on_restore_clicked`
     (:1624) path: `list_restorable` → checkbox dialog → folder picker →
     `_run_restore` → `_on_restore_done` auto-fires a sync to materialize history.
- Edge cases: empty/missing registry.json → toast "Backup has no project list yet —
  run sync once on the old machine first" (check `list_restorable` empty-path);
  wizard cancelled mid-flow → nothing persisted except `sync.*` config already
  entered (fine — the menu path remains); auth failure → existing dialogs (wizard is
  user-driven, dialogs allowed).
- Tests: pure `should_offer_wizard(registered_count, dismissed)`; the dialog chain is
  verified via the GUI harness (fresh HOME → wizard appears; "Start fresh" → never
  again).

### Stage 6 — Docs
- CLAUDE.md settings table: `sync.auto`, `sync.auto_interval_minutes`.
- `docs/plan-migration-hardening.md`: add a short "manual pre-reinstall checklist"
  section (what still does NOT survive: keyring/git credentials → re-auth once;
  dispatch/LAN pairings → re-pair; `~/.claude.json` beyond the 3 synced fields).

## Reuse map (don't reinvent)
- Restore engine+UI: `sync_service.py:502,534,555,566`; `project_manager.py:1592-1719`.
- Bootstrap: `sync()` self-clones a missing repo (`sync_service.py:202-208`);
  `is_configured()` = `sync.enabled` + `repo_url`.
- 3-way whole-file mechanism for settings: `_sync_global` entries + `SyncStateStore`.
- Sync concurrency: `SyncLock` (flock; busy → PAUSED, no git work) + `_syncing` flag.
- Test harness: `tests/test_sync_service.py` (`fresh_service`, `make_project`,
  `seed_summary`, `make_bare`, HOME swap + singleton reset).

## Verification
1. `uv run pytest tests/` green (new: registry-always, settings roundtrip+exclusions,
   NOT_SYNCABLE, should_auto_sync, should_offer_wizard).
2. Two-machine sim already covers export/import; extend one test to assert a summary
   saved on A appears on B after A-sync + B-sync (regression for the live bug).
3. GUI harness: fresh config dir → wizard appears; Start fresh → dismissed persists.
   Normal launch (existing config): no wizard, auto-sync fires ~5s after start
   (observe `updated_label` + sync repo git log gains a commit).
4. Live: leave PM open >interval → periodic commit appears; on the laptop just open
   PM and wait — the missing summary must arrive without touching the Sync button.
