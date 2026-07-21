# Plan: Codex history cross-machine sync

Status: **DONE** (2026-07-20).

## Goal

Bring Codex CLI session history into the existing cross-machine sync, at parity
with Claude history — closing the "Codex parity #1" gap (`~/.codex/sessions` was
never backed up and absent from the pre-reinstall checklist).

## Design

**Per-project, not a global dump.** Codex rollouts live flat by date
(`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`), but each file is one session with
one `cwd` (in its first-line `session_meta`), so the project association is
unambiguous. Syncing per-project means a machine restores only the history of the
projects it actually has, and the `cwd` is remapped so history groups correctly on
each machine.

**Key reuse:** the engine's cwd rewrite (`_cwd_rewrite`, `sync_engine.py`) is
anchored on the `"cwd"` JSON key, and Codex serialises `session_meta.payload.cwd`
as `"cwd":"/path"` — so `_cwd_to_placeholder`/`_placeholder_to_cwd`,
`sanitize_jsonl`, `validate_file` and `merge_session_pair` all apply to Codex
rollouts unchanged. Codex rollouts are append-only jsonl, exactly like Claude
sessions.

## Checkpoints

- [x] `codex_history.py`: `rollout_paths_for_cwd(project)` — index-only lookup of a
  project's rollout file paths (no display-metadata scan); `[]` when Codex unused.
- [x] `sync_engine.py`: `CODEX_PREFIX = "codex/"` + `_is_session_like(rel)` helper;
  the 4 merge/cwd branches (read-rewrite, materialized_bytes, export-merge,
  import-merge) switched from `SESSIONS_PREFIX` to `_is_session_like`.
- [x] `LocalProjectView`: `codex_sessions_root` + `codex_rollout_paths` fields;
  `_iter_local_rels`/`_local_path` map `codex/<date>/rollout.jsonl` ↔
  `~/.codex/sessions/<date>/rollout.jsonl`. `_repo_hashes` scans `codex/` too.
- [x] `sync_service.py`: `_view` injects `CodexHistoryService().sessions_root` +
  `rollout_paths_for_cwd` (used by both `_run` and `_advance_base` → hashes match).
- [x] Tests (`tests/test_sync_service.py`): `seed_codex_rollout` helper;
  roundtrip with cwd-remap between two machines; no-`~/.codex` no-op.
- [x] Docs: CLAUDE.md sync description + settings row; pre-reinstall checklist note.

## No provider gate

No new setting. Absent `~/.codex` or a project with no rollouts → no `codex/` rels,
identical behaviour to before. Rides on top of the pull-on-start / manual-push
trigger model (see `docs/plan-migration-hardening.md` and the sync memory).

## Verification

- `uv run pytest tests/` green (432, incl. 2 new Codex tests).
- Two machines: A → Sync (push) writes `projects/<id>/codex/<date>/rollout-*.jsonl`
  with `__CC_PROJECT_ROOT__` in `cwd`; B opens PM (startup pull) → rollout lands in
  B's `~/.codex/sessions/<date>/` with B's own path, visible in the project's
  Codex history.
