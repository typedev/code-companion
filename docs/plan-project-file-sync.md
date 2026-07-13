# LAN project file-sync (`.shared` include, one-way Get)

Status: **shipped** on `main` (13 commits, `957a85d`…`0ffc74c`). GUI verified live
(15k+ files transferred fast). This doc reflects what actually shipped.

## Why

Cross-machine sync of Claude context/sessions/memory worked, but many files needed
for work are **gitignored** (build inputs, generated assets, UFO font sources — a
single `.ufo` is hundreds of thousands of tiny `.glif` files). GitHub moves only
tracked files, so the working set didn't travel. This adds an opt-in, LAN-only,
one-way file mirror so the machine you sit down at can pull the working files it
lacks — for both the user and Claude.

## Model

- **One-way Get**, per project, on demand from the Files toolbar. The machine that
  has the files serves; the one that needs them pulls. (Matches the dispatch
  topology: the server advertises, the client browses.) Give was designed then
  dropped — one-way fits the workflow and the transport.
- **Directional mirror, no 3-way/base**: fetch a peer's manifest, diff against the
  local one, pull the difference. Anything overwritten/removed locally is moved to
  `<project>/.deleted/<stamp>/` first (recoverable), never hard-deleted.
- **Scope** = (`.shared` allowlist ∪ `shared/`) **minus** git-tracked files
  (`git ls-files`), `.git`, `.deleted/`, and heavy dirs (`node_modules`, `.venv`, …).
  `.shared` is a committed, gitignore-syntax file (negation supported); it's re-read
  every sync (no cache), so edits take effect on the next Get.

## Pairing (mutual trust)

- Pairing is **mutual**: one Allow trusts both directions forever (`pair_mutual` —
  the client pre-issues the peer a callback token so the peer can call back). Used
  by both file-sync and the dispatch/remote-client panel.
- Preferences lists **one row per trusted device** with a single **Forget** (drops
  trust both ways). The "Sync files" button appears on its own once the machine has
  any trusted device — no setting/toggle.
- **Auto re-pair on revoke**: if the peer did Forget, our token 401s on the next
  Get; we drop the stale token and re-pair on demand (peer shows Allow). The 401
  hits the first call (manifest), before any file/trash change, so the retry is clean.

## Transport (reuses dispatch)

Bearer-authorized, `project_id`-addressed routes on the existing dispatch broker
(zeroconf discovery + per-device tokens reused unchanged):
- `POST /filesync/manifest` → `{rel: sha256}` for the resolved shared set.
- `POST /filesync/fetch` → a streamed frame of the requested files (one request/one
  stream → collapses the 100k-tiny-file overhead), **sandboxed** to the shared set
  (blocks path escapes / non-shared / tracked files).
Serving needs the peer's Project Manager running (dispatch on); the project's window
need not be open — the broker serves any registered project by id.

## Layout (`src/services/file_sync/` + `file_sync_service.py`)

- `share_spec.py` — resolver (`.shared` pathspec allowlist + `shared/`, minus tracked/
  skip-dirs).
- `file_index.py` — persistent `{rel:(mtime,size,sha256)}` cache; builds the manifest,
  re-hashing only stat-changed files.
- `file_sync_engine.py` — pure `diff` → `plan_get` (with `destructive_count`) →
  `prepare_trash`/`write_file`; `.deleted/` backup.
- `file_sync_service.py` — `build_preview`, `run_get` (streamed apply + progress),
  `git_operation_in_progress` / `ensure_deleted_gitignored` / `count_trash` /
  `empty_trash`.
- `wire.py` — framed file stream shared by broker + client. `project_resolver.py` —
  `project_id` → path via the registry.
- `dispatch_broker.py` / `dispatch_api.py` — the routes + client (`fetch_manifest`,
  `fetch_files`, `pair_mutual`).
- `src/widgets/file_sync_dialog.py` — "Sync files" dialog (peer picker, preview with a
  prominent destructive-count warning, `Gtk.ProgressBar`, "Empty .deleted/").
- `src/project_window.py` — the toolbar button (auto-shown when a trusted device exists).
- `scripts/file_sync_probe.py` — headless serve/preview/get probe for cross-machine
  validation.

## Safety

- Only untracked files sync — never fights git or touches `.git`/index/tracked files.
- Skips a Get while a git merge/rebase/cherry-pick/revert is in progress.
- `.deleted/` is auto-added to `.gitignore` and never synced; "Empty .deleted/" clears it.
- Per-file atomic apply + resumable; pairing (bearer token) is the identity guard;
  transfers are LAN-only.

## Tests

20 file-sync tests (`tests/test_file_sync*.py`): resolver (negation / skip-dirs /
git-tracked / `.git`&`.deleted` exclusion), index stat-cache, diff/plan/apply +
`.deleted` recovery, wire round-trip, a live loopback broker (manifest / streamed
fetch / sandbox / auth), full loopback Get (mirror + `.deleted`), mutual-pairing
callback storage, and the git-guard / gitignore / trash helpers. Suite green apart
from a pre-existing keyring env failure.
