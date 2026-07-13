# Plan: LAN project file-sync (`.shared` include, directional mirror)

Status: **approved**, implementing. Phase 1 (local core) first.

## Context

Cross-machine sync of Claude context/sessions/memory works, but the user still can't fully continue
work on the second machine because **many needed files are gitignored** (build inputs, generated
assets, and especially UFO font sources — a single `.ufo` is a directory of *hundreds of thousands*
of tiny `.glif` files). GitHub moves only tracked files; the working set doesn't travel.

Constraints (confirmed with the user):
- **Never edits one project on two machines concurrently** — a clean handoff, not a live merge.
- **Both machines online on the same LAN at sync time** → realtime P2P, no cloud store; reuse the
  "local dispatch / remote client" transport (zeroconf + pairing + auth).
- **Explicit opt-in** via a `.shared` file (gitignore-style *allowlist*, committed to git) + a
  user-created `shared/` folder.
- **Explicit direction, one button + preview** — the user picks Get / Give and sees the diff first.
  No automatic merge, no conflict files.

## Model — directional mirror (no 3-way, no base)

- **Get (⭠ peer→local)**: make the local shared-set match the peer's — pull differing/missing files,
  remove local-only files. Anything overwritten/removed is first moved to `<project>/.deleted/<stamp>/<rel>`.
- **Give (⭒ local→peer)**: symmetric, implemented as a **pull-request** to the peer (both online) —
  the peer performs a Get from us. Each machine only writes its own disk; remote stays read-only.

No base state, no `SyncStateStore`, no `.remote` conflict files — the chosen direction wins,
`.deleted/` is the safety net.

## Scope of the shared set

`resolve(project)` = (matched by `.shared` allowlist ∪ everything under `shared/`) **minus** git-tracked
files (`git ls-files`), `.git`, `.deleted/`, and `problems_service._SKIP_DIRS`. `.shared` is committed
to git; `shared/` is user-created; `.deleted/` is auto-added to `.gitignore`.

## Components (new module `src/services/file_sync/`)

1. **`share_spec.py`** — include resolver: `pathspec` allowlist (negation ok) + `shared/`; `os.walk`
   with `_SKIP_DIRS` pruning; subtract `git ls-files -z`; always exclude `.git`/`.deleted/`.
2. **`file_index.py`** — persistent `{rel: {mtime_ns,size,sha256}}` per project under `get_config_dir()`;
   re-hash only on `stat_differs`; schema-versioned JSON via `atomic_write_text`; `manifest()->{rel:sha256}`.
3. **`file_sync_engine.py`** — pure `diff(local,remote)->{only_remote,only_local,changed}`; per-direction
   preview; apply (Get): fetch + `atomic_write_bytes`, back up overwritten/removed to `.deleted/<stamp>/`.
   Retention manual ("Empty `.deleted/`"). Reuse `hash_file`/`hash_bytes`; NOT `decide_*`/`SyncStateStore`.
4. **Transport** (`dispatch_broker.py`+`dispatch_api.py`): bearer-guarded, `project_id`-addressed routes —
   `/filesync/manifest`, `/filesync/fetch` (tar `StreamingResponse`, sandboxed), `/filesync/pull-request`
   (triggers Give). Reuse zeroconf + paired tokens.
5. **UI** (`file_sync_service.py` + Files-section button): "Sync files" → peer select → preview
   (counts, **destructive count flagged**, direction) → progress bar → apply off-thread. Off `sync_repo`.
6. **Settings**: `file_sync.enabled` (per-project), optional peer pin. `.shared` re-resolve via
   `FileMonitorService`.

## Git interaction
- Only untracked files sync (scope subtracts `git ls-files`) — never touches `.git`/index/tracked.
- Branch-independent; no sync during an in-progress git op.

## Robustness
- Both machines online; no peer → "peer not found", nothing changes. User-initiated only.
- Pairing is the identity guard. Atomic + resumable; `.deleted/` recovers wrong-direction mistakes.
- **Serving requires the peer's Project Manager running** (dispatch on) — the machine-level broker
  lives in the PM; the project's workspace need not be open (serves any registered project by id).

## Phasing
1. **Local core** — `share_spec` + `file_index` + mirror `diff`/apply + `.deleted/`. Unit-testable.
2. **Transport** — manifest + fetch tar-stream + pull-request + sandbox, `project_id`-addressed.
3. **UI** — Files-section button → preview (peer/direction/counts) → progress → apply.
4. **Polish** — `.deleted/` `.gitignore` guard + "Empty" action, statuses, `.shared` live re-resolve,
   in-git-op guard.

## Verification
- **Unit**: resolver (negation, `shared/`, `.git`/`.deleted`/tracked excluded, `_SKIP_DIRS` pruned);
  index (stat-cache hit / rehash / schema bump); `diff`+apply (Get pulls, only_local→`.deleted/`,
  overwrite backed up, recovery restores); loopback round-trip both directions.
- **E2E (canary)**: `shared/` codeword file A→B via Sync files (Get); delete on A → lands in B's
  `.deleted/`; Give A→B; tracked/`node_modules`/`.venv`/`.git` never transfer; `.deleted/` gitignored.
- **Offline**: peer off → "peer not found", no change.
- **Regression**: `tests/test_sync_*` stay green (untouched `sync_repo`/metadata base).
