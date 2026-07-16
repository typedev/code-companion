# Cross-Machine Sync — Implementation Plan

> On approval, the first implementation action is to copy this plan into
> `docs/plan-sync-across-machines.md` (per CLAUDE.md rule 2) and track checkpoint
> progress there.

## Context

The user works on the same projects from two machines (desktop + laptop, same OS
user `alexander`). Claude Code's per-project **history** (`~/.claude/projects/<encoded>/*.jsonl`)
and **memory** (`<encoded>/memory/*.md`) live under `~/.claude` and today do **not**
travel between machines — only what's committed to each project's own git repo does.
Goal: let the user finish a session on one machine, click **Sync**, and continue on the
other with the conversation history and project memory already loaded.

Design constraints agreed with the user:
- **Backend**: a private git repo — `https://github.com/typedev/code-companion-sync`.
- **Trigger**: a **manual "Sync" button in the Project Manager**, one bidirectional
  `pull --rebase` + `push` per click. No daemon, no auto-sync in MVP.
- **Safety first**: a bad sync (e.g. a future Claude Code layout change) must never break
  the working state; recovery is possible even at the cost of losing the last sync.
- **Concurrency rule** (user-accepted): the *same* project must not be edited on both
  machines simultaneously; *different* projects on the two machines must be safe.

## What syncs (scope) — MVP "selected" mode

| Layer | Source | Into sync repo |
|---|---|---|
| Per-project history | `~/.claude/projects/<encoded>/*.jsonl` | `projects/<id>/sessions/` |
| Per-project memory | `<encoded>/memory/*.md` + `MEMORY.md` | `projects/<id>/memory/` |
| Per-project config slice | whitelisted `~/.claude.json → projects[<abs>]` | `projects/<id>/claude-config.json` |
| Global plans | `~/.claude/plans/*.md` | `global/plans/` |
| Global settings | `~/.claude/settings.json` | `global/settings.json` |
| Session summaries | `<config>/session-summaries/*.md` | `global/session-summaries/` |
| Messages (mailbox) | `<config>/messages/<thread>/*.json` | `global/messages/` |
| Snippets | `<config>/snippets/*.md` | `global/snippets/` |
| Rules | `<config>/rules/*.md` | `global/rules/` |

(`<config>` = `~/.config/code-companion/`, or the legacy `claude-companion` dir.)

**Never synced**: `~/.claude/.credentials.json`, whole `~/.claude.json`, caches,
`shell-snapshots/`, `session-env/`, `file-history/` (100M), lock files.
`hasTrustDialogAccepted` is **excluded** from the config whitelist by default (syncing it
would silently auto-trust the project on machine 2). Default whitelist:
`["allowedTools","mcpServers","enabledMcpjsonServers"]`.

## Sync repo layout (keyed by machine-independent project_id)

```
manifest.json                     # {schemaVersion, appVersion}
projects/<id>/meta.json           # {project_id, id_source, canonical_remote, display_name}
projects/<id>/memory/*.md, MEMORY.md
projects/<id>/sessions/*.jsonl
projects/<id>/claude-config.json  # whitelisted .claude.json slice
global/settings.json
global/plans/*.md
global/session-summaries/*.md
global/messages/<thread_id>/*.json
global/snippets/*.md
global/rules/*.md
```

Materialized on each machine into the **local** `~/.claude/projects/<local-encoded>/`
where `local-encoded = encode_project_path(local_abs_path)` — this id→path remap is what
makes different repo locations across machines work.

## project_id resolution (`src/utils/project_identity.py`)

`resolve_project_identity(path) -> ProjectIdentity | None`:
1. **Has git `origin`** → `id = slug(normalize_remote_url(origin))`
   (`github.com/typedev/code-companion` → `github.com_typedev_code-companion`). Deterministic,
   identical on both machines.
2. **Git repo, no remote** → **root-commit hash** (`git rev-list --max-parents=0 HEAD`,
   lexicographically smallest if several). Stable across clones, **no writes to the repo**.
3. **Git repo, no commits** → opt-in committed `.code-companion/project-id` (UUID) — last resort only.
4. **Not a git repo** → `None` → **not syncable** (badge "no sync"). A path-derived id would
   differ per machine and defeat sync; surfaced as a limitation.

## The correctness core — hash-based 3-way merge

The hazard the user named ("two different projects on two machines") is solved by treating
**this machine's last-synced file hashes as the merge base**. Never uses mtime — only
`sha256(bytes)`.

- **State store** `~/.config/code-companion/sync_state.json`: per project_id, `{rel_path: sha256, last_synced}`.
- **Export (local → repo), dirty-only**: a file is written to the repo only if
  `h_local != h_base` (locally changed since last sync). Files where `h_local == h_base`
  are left as whatever `pull --rebase` brought (adopt the other machine's version). So a
  machine that didn't touch project X never overwrites X.
  - **First-contact adoption rule (mandatory, the real risk)**: when `h_base` is absent,
    export only files **absent** in the repo (seed genuinely-new); never overwrite divergent
    repo files — reconcile as conflicts on import. Prevents the first sync from clobbering a
    populated repo.
  - **Sessions** (`*.jsonl`, unique names, append-only) = **union**; on same-name/diff-content
    prefer the longer byte length (append-only ⇒ superset). Never deleted.
  - **Deletions are not exported** (additive-only); cross-machine delete propagation is
    out of scope for MVP (would need tombstones) — documented.
- **Import (repo → local), safe additive**: `validate → snapshot the file we're about to
  overwrite → atomic rename`. Both sides changed same file ⇒ **CONFLICT**: keep local, stash repo
  copy in the snapshot as `<name>.remote`, mark badge — never destructive. Additive: never deletes
  a local file absent from the payload. `.claude.json` applied by surgical field patch, never
  whole-file. Validation: every `*.json` parses; `*.jsonl` tolerant of a partial trailing line.

## Safety / recovery

- An import snapshots to `~/.config/code-companion/sync-snapshots/<iso>/` (machine-local, **not**
  synced) — the escape hatch that a bad sync cannot corrupt. It captures **only** what a run can
  destroy: a file it overwrites, plus both sides of a conflict (`<name>.remote` next to the local
  copy, so a manual merge has everything in one place). Raw on-disk bytes, not the normalized read
  path: a session restored with a placeholder `cwd` would be silently invisible to native
  `/resume`. A run that changes nothing writes nothing.
- **The "can this write lose bytes?" test is done in raw on-disk bytes**, the space the escape
  hatch restores into — not repo-space, which would misjudge every session (its `cwd` is encoded
  differently on each side). If the bytes about to land start with the bytes already there, the
  write is identical or a pure append: nothing to preserve. This matters because
  `merge_session_pair` is a wholesale replace, not a merge, so a catch-up import swaps the whole
  multi-MB file even when the other machine only appended. Measured on a live 34-project run that
  re-imported 93 sessions: 366MB → 0.
- Snapshots are an escape hatch, **not** an archive — the durable copies are the local files and
  the repo's git history.
- **Retention**: the newest `_SNAPSHOT_RETENTION` (3) run dirs are kept; older ones are pruned in
  `sync()`'s `finally`, so a failed or auth-blocked run still cleans up. Snapshotting the whole
  subtree per run previously grew this dir to 5.3GB of pure duplication.
- **Schema guard**: if repo `manifest.schemaVersion > SCHEMA_VERSION`, quarantine — refuse
  import, mark ERROR, leave local untouched (protects an older machine against a newer one).
- **Crash recovery** (`sync_recovery.recover`): heal a clone left mid-rebase (abort) or with
  unpushed commits; fall back to `settings.sync.last_good_commit` if corrupt.
- **Never force-push**; `pull --rebase --autostash`; on `RebaseConflict` abort + mark conflict + skip push.

## Session merge: why records, not bytes (fixed 2026-07-16)

`merge_session_pair` used to call the **longer byte string** the superset. That silently
deadlocked every session exported before the `cwd` placeholder shipped (93bb023) — 94 of 107 files
in the repo at the time.

Rewriting `cwd` does **not** preserve byte length: `__CC_PROJECT_ROOT__` is 19 chars against e.g.
37 for `/home/alexander/WORK/ufo-widgets-gtk4`, so the placeholder form of a session is ~19 bytes
shorter per record (measured: 21150 bytes over ~1113 records in one file). The append-only
byte-length invariant holds *within* one encoding; the comparison spanned two. Both directions
then picked the legacy form:

- **import**: `h_local` (placeholder) != `h_repo` (real path) → repo is longer → repo wins → local
  is rewritten with byte-identical content, so the disk never changes and the mismatch survives;
- **export**: same pair → repo is still longer → `hash(merged) == h_repo` → no write, so the
  normalized form could never be published.

The base never advanced (`_advance_base` only records `hl == hr`), so those projects reported
`BEHIND` forever and every sync rewrote ~93 files to no effect — and, before the snapshot rework,
copied 366MB per run to protect writes that changed nothing.

**Fix**: compare record counts (`count(b"\n")`), which are encoding-independent, and on a tie
prefer the copy holding the placeholder — the canonical form. That makes a legacy pair converge on
its own: import stops rewriting (records are equal, local's normalized form wins), export
republishes the normalized bytes, and the base advances. No migration script was needed; the first
sync on the fixed code healed 94 files → 1 and turned 12 permanent `BEHIND` into `SYNCED`.

The tie-break is what stops a ping-pong: a machine whose local copy carries *another* machine's
path can't normalize it (the rewrite is anchored to the local project path), so without preferring
the placeholder it would keep republishing the legacy form and undo the healing. With it, that
machine adopts the repo's normalized copy and materializes `cwd` to its own path — which also
repairs `/resume` there.

Remaining: a session whose `cwd` names a path no machine currently owns (e.g. a project folder
renamed since) stays in real-path form until the machine holding that path syncs. Harmless — it
simply hashes equal on both sides and never churns.

## Module breakdown

**New files**
- `src/utils/git_auth.py` — auth extracted from `GitService` (`normalize_remote_url`,
  `is_auth_error`, `get_stored_credentials`, `store_credentials`, `build_auth_env` →
  GIT_ASKPASS env). Reused by both `GitService` and `SyncRepo`.
- `src/utils/claude_paths.py` — centralize `~/.claude` layout (all from `Path.home()`):
  `project_dir/project_memory_dir/project_sessions/plans_dir/settings_json/claude_json`.
- `src/utils/project_identity.py` — id algorithm above.
- `src/models/sync.py` — `SyncState` enum, `ProjectSyncStatus`, `SyncResult`, `FileEntry`, `SCHEMA_VERSION=1`.
- `src/services/sync_repo.py` — thin git-CLI wrapper for the single clone: `clone`,
  `pull_rebase`, `push`, `commit_all`, `head_hash`, `is_mid_rebase`/`abort_rebase`,
  `hard_reset_to`. Raises `AuthenticationRequired` (reused) + new `RebaseConflict`.
- `src/services/sync_state_store.py` — the per-machine hash base manifest (load/save like `ProjectRegistry`).
- `src/services/sync_engine.py` — **pure** export/import/snapshot/validate/slice (no git,
  no network → unit-testable): `hash_file`, `export_project`, `import_project`,
  `extract_claude_json_slice`, `apply_claude_json_slice`.
- `src/services/sync_recovery.py` — `recover(repo, settings)`.
- `src/services/sync_service.py` — **singleton orchestrator** mirroring
  `ProjectStatusService` (thread-off-main, JSON status cache `sync_status_cache.json`,
  `GLib.idle_add`): `is_configured`, `get_cached_status`, `sync(credentials, progress) -> SyncResult`.
  Owns the `SyncLock`, schema guard, ordering, base + `last_good_commit` updates.
- `src/services/sync_lock.py` — `SyncLock` mirroring `ProjectLock` (hashed file in
  `/tmp/code-companion-locks/`), guards the shared clone across the multi-process app.

**Existing files touched**
- `src/services/git_service.py` — replace private auth helpers with delegations to
  `git_auth.py` (behaviour-preserving).
- `src/services/settings_service.py` — add to `DEFAULT_SETTINGS`:
  `"sync": {"enabled": False, "repo_url": "https://github.com/typedev/code-companion-sync",
  "last_good_commit": "", "mode": "selected", "claude_json_fields":
  ["allowedTools","mcpServers","enabledMcpjsonServers"]}`.
- `src/project_manager.py` — Sync button in header next to `refresh_button` (~L127);
  `_on_sync_clicked` worker cloned from `_on_refresh_clicked` (spinner, `AuthenticationRequired`
  → `show_github_credentials_dialog` → retry with creds); `row.sync_badges` slot in
  `_create_project_row` (~L293) + `_render_sync_badges`; sync CSS in `_BADGE_CSS`; cached-status
  paint on load; first-run "Configure sync…" dialog to confirm/save `sync.repo_url` + enable.

## End-to-end Sync sequence (`SyncService.sync`, under SyncLock)

```
acquire SyncLock (else return PAUSED)
0. repo = SyncRepo(config_dir/sync, settings.sync.repo_url); clone if missing; recover()
1. repo.pull_rebase(creds)
2. schema guard on manifest.json (too new → ERROR all, abort)
3. IMPORT inbound per syncable project (snapshot→temp→validate→atomic; record conflicts)
4. EXPORT outbound per project (dirty-only vs base; first-contact adoption rule)
5. changed = repo.commit_all("sync <host> <iso>")
6. if changed: pull_rebase (RebaseConflict → abort + mark CONFLICT + skip push) ; else push
7. update manifest if we bump; 
8. state_store.set_base(id, current hashes); settings.sync.last_good_commit = head; write status cache
release SyncLock   # AuthenticationRequired bubbles to _on_sync_clicked → creds dialog → retry
```
Import-before-export so export sees merged state and can't regress it. Step 8's base update
("what both sides now agree on") is the linchpin.

## Badge states (reuse `_make_badge` + new `cc-badge-*` CSS)

`NOT_CONFIGURED` (non-git / disabled) · `SYNCED` ✓ · `AHEAD` ↑ (pushed) · `BEHIND` ↓ (imported)
· `CONFLICT` (red, tooltip lists files + snapshot path) · `PAUSED` (lock held) · `ERROR` (schema/auth/git)
· `SYNCING` (spinner).

## Checkpoints (each independently testable)

- **CP0 — Scaffolding, no behavior change**: extract `git_auth.py` (delegations kept),
  add `claude_paths.py`, `sync.*` defaults, empty `models/sync.py`. Regression: app + git unchanged.
- **CP1 — Identity + state store (pure/unit)**: `project_identity.py`, `sync_state_store.py`.
- **CP2 — Sync engine (pure, no network)**: `sync_engine.py` export/import/snapshot/validate/slice.
  Table-driven 3-way tests (dirty-only export, additive import, conflict, JSONL partial line).
- **CP3 — SyncRepo + lock + recovery** against a local bare repo.
- **CP4 — SyncService orchestration (headless)**: steps 1–8, schema guard, status cache;
  two-clone harness (below), no GTK.
- **CP5 — UI integration = MVP done**: Sync button, worker + auth retry, `_render_sync_badges`,
  cached paint, first-run config dialog. Manual + two-HOME run.
- **CP6 (post-MVP) — Backup mode**: `sync.mode="backup"` exports all registered projects +
  `projects.json` registry into `global/registry.json`, no retention, still additive; import
  offers to register unknown ids. This is the "clean OS reinstall" restore path.

## Verification (no second physical machine needed)

Everything derives from `Path.home()`, so simulate machines via `HOME` override:
1. Bare remote: `git init --bare /tmp/sync-remote.git`.
2. Machine A (`HOME=/tmp/mA`): seed `~/.claude/projects/<enc>/…`, a project repo with origin
   (or root commit), register, `sync()` → assert repo populated + base updated.
3. Machine B (`HOME=/tmp/mB`): clone the *project* (same id), register, `sync()` → assert
   local materialized via id→path remap, additively.
4. Different-project concurrency (A edits X, B edits Y) → assert no clobber, both converge.
5. Same-file conflict → second syncer gets CONFLICT, no overwrite, snapshot retained.
6. First-contact adoption (B has divergent pre-existing local) → both kept, no clobber.
7. Crash recovery (kill between commit and push) → next `sync()` recovers.
8. Schema guard (bump remote schemaVersion) → import refused, local untouched.

Package 2–8 as pytest cases sharing one bare remote (HOME swap + fresh singletons) — CI-able.

## Open items / user prep
- Ensure git push/pull auth to `code-companion-sync` works on both machines (HTTPS token or SSH).
- Non-git project folders are not syncable in MVP (by design).
- Cross-machine file *deletion* propagation is out of scope for MVP.

---

## Implementation status (updated as of this session)

- [x] **CP0 — Scaffolding**: `git_auth.py` (auth extracted from GitService), `claude_paths.py`, `models/sync.py`, `sync.*` settings. Behaviour-preserving; smoke-verified.
- [x] **CP1 — Identity + state store**: `project_identity.py`, `sync_state_store.py`. 13 tests.
- [x] **CP2 — Sync engine**: `sync_engine.py` (3-way merge, dirty-only export, additive import, conflict, sessions union, JSONL sanitize, `.claude.json` slice). 33 tests.
- [x] **CP3 — SyncRepo + lock + recovery**: `sync_repo.py`, `sync_lock.py`, `sync_recovery.py`. 10 tests against a local bare repo.
- [x] **CP4 — SyncService orchestration**: `sync_service.py` (steps 0–8, schema guard, status cache, global plans/settings). 4 two-machine HOME-override tests: roundtrip, different-projects-no-clobber, non-destructive same-file conflict, schema guard.
- [x] **CP5 — Project Manager UI**: Sync button, `_on_sync_clicked` worker + auth-retry dialog, `_render_sync_badges`, cached-status paint, first-run "Configure sync…" dialog. Import-verified; mirrors the proven `_on_refresh_clicked` pattern.
- [x] **CP6 — Backup mode**: `sync.mode="backup"` writes `global/registry.json` (additive union by id, with cloneable remote URL); `list_restorable()` surfaces backed-up projects absent locally; `restore_project()` clones + registers them (then a follow-up Sync materializes their data). UI: sync-options menu (Configure / Backup mode toggle / Restore from backup…) with a folder-picker restore dialog. Header buttons now use the Material **git** and **claude** icons (were near-identical stock symbolic icons). 2 backend tests.

Total: **62 tests passing**, ruff clean. pytest added to dev deps; `[tool.pytest.ini_options]` added to pyproject.
