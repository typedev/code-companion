# Plan: Cross-machine `/resume` continuity (cwd placeholder in sync) + History display fixes

Status: **core ✅ validated live** (laptop `…/code-companion` → desktop `…/claude-companion`,
canary `ЯНТАРНЫЙ-ОСЬМИНОГ-0713`, 2026-07-13). Follow-up A (History label match) ✅ done;
follow-up B (resume-from-History) deferred.

## Context

Cross-machine Sync copies Claude session JSONL transcripts byte-for-byte and remaps the
*encoded directory name* to the local machine, but it did **not** rewrite the absolute paths
baked *inside* the records. Each transcript keeps the `cwd` of the machine that created it
(a desktop session records `cwd: /home/alexander/WORK/claude-companion`).

Claude Code's native `/resume` lists only sessions whose recorded `cwd` matches the current
working directory. On a second machine where the same project lives at a different path
(`…/code-companion`), every synced session was filtered out of `/resume` — the user saw only a
freshly-created local session and got zero prior context. Reproduced end-to-end with a canary
(`ЛИЛОВЫЙ-НАРВАЛ-1307` in session `22a7fdc0`): the transcript was physically present in the local
projects dir yet invisible to `/resume`. All ~20 synced sessions carried `cwd=…/claude-companion`;
none matched `…/code-companion`.

Two related IDE-History display bugs were folded in: the session list showed timestamps in **UTC**
(a 10:33-local session read "13:33", looked like a future session), and previews showed the
`/usage-credits` login caveat instead of the first real prompt — together they made the user's own
live session unrecognizable.

## What was implemented

### Part A — `cwd` placeholder in the sync repo  ✅
`src/services/sync_engine.py`:
- New `_PROJECT_ROOT_PLACEHOLDER = "__CC_PROJECT_ROOT__"` and `_cwd_rewrite` /
  `_cwd_to_placeholder` / `_placeholder_to_cwd` (anchored byte regex on the top-level `"cwd"`
  value, lookahead `["/]` for a path boundary; never re-serializes JSON, so byte-length is
  preserved for `merge_session_pair`).
- Export seam: `LocalProjectView.read_local_bytes` — after `sanitize_jsonl`, sessions only →
  local abspath → placeholder. Sits **below** the hashing/merge layer, so repo copies are
  byte-identical across machines (no churn; unchanged files resolve to `skip`).
- Import seam: `LocalProjectView.write_local` — session branch, before the atomic write →
  placeholder → local abspath, so `/resume` lists the session for the current cwd.
- Nested absolute paths (tool inputs, `toolUseResult`, `file-history-snapshot`) are intentionally
  left origin-specific — `/resume` only keys on `cwd`. Full transcript portability is out of scope.

### Part B — History display fixes  ✅
- `src/models/session.py` `display_date`: `.astimezone()` before `strftime` (UTC → local).
- `src/services/history.py`: new `_is_command_meta`; `_parse_session_metadata` now takes the first
  **real** user prompt (skips `<local-command-caveat>` / `<command-…>` / `<local-command-stdout>`),
  reusing `_extract_user_text`.

### Tests  ✅
`tests/test_sync_engine.py`: `make_view(abs_path=…)` param + three cases — pure transform
round-trip/guards, cross-machine normalize-in-repo/materialize-per-machine, and churn-free
re-export. `36 passed`.

## Rollout / migration

The transform takes effect on the next sync round-trip; **no one-off migration** (the `22a7fdc0`
test session need not be recovered). Pre-existing synced files retain their old foreign `cwd`
locally until the **origin** machine re-exports them (its export rewrites its cwd → placeholder in
the repo; the target's next import materializes placeholder → local path). Transition stabilizes
after one push→pull round-trip per session.

## Verification (remaining: live canary)

1. Deploy on both machines.
2. Desktop: fresh session, new codeword (e.g. `ЗЕЛЁНЫЙ-КИТ-1307`), `/exit`, run Sync.
3. Repo copy: `grep '"cwd":"__CC_PROJECT_ROOT__"'` present, no desktop abspath in `cwd`.
4. Laptop: Sync → launch → `/resume` → session appears → select → ask "что делали" → recalls codeword.
5. Laptop local JSONL: `"cwd"` shows the laptop abspath.
6. History panel: current session shows local time + real preview (not `<local-command-caveat>`).

### Live validation result (2026-07-13)

Confirmed end-to-end laptop→desktop: transcript synced, `/resume` on the desktop listed and
restored the session (canary `ЯНТАРНЫЙ-ОСЬМИНОГ-0713` recalled without hints), and all 314 `cwd`
fields were materialized to the desktop path (0 leftover laptop-path, 0 `cwd`-placeholders; the 22
remaining `__CC_PROJECT_ROOT__` occurrences are prose mentions in the transcript — proof the
anchored rewrite touches only the `cwd` field, not arbitrary text).

## Follow-up A — History ↔ `/resume` label match  ✅

The live test surfaced that the native `/resume` picker labels sessions by Claude Code's
auto-generated `aiTitle` (e.g. `cross-machine-resume-cwd-sync`) while History labeled by the first
prompt — the same session looked different in the two lists, and our fix (which newly populates the
`/resume` list with synced sessions) made the mismatch acute.

- `src/models/session.py`: `Session` gains `ai_title` and `last_timestamp`.
- `src/services/history.py` `_parse_session_metadata`: parse the `ai-title` record's `aiTitle`
  (last wins) and track last activity; list methods now sort by `last_timestamp` (recency, matching
  `/resume`) instead of session start.
- `src/widgets/claude_history_panel.py`: render `aiTitle` as the row title (`.session-title`,
  accent/bold) above the preview; include it in the search filter.

## Follow-up B — resume a session directly from History  (deferred)

Add a "Resume" action in History that launches `claude --resume <session-id>`, so the native picker
is never needed. Deferred because it collides with the one-live-session-per-project tmux supervisor
(resuming another session displaces the current one) — needs a UX decision (replace / new tab /
confirm). To be planned separately.
