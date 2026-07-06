# Stability & Growth Roadmap (v0.8.x → v1.0)

**Status**: Phases 1 (data safety) & 2 (async layer, +3.4 git env) code-complete & tested; Phase 7 (MCP) next
**Based on**: 4-track reliability audit + worktree architecture research (2026-07-06)
**Code references**: valid as of commit `ef69c77` — line numbers may drift, symbol names are stable.

> **Near-term scope (settled 2026-07-06):** the active track is **Phase 1 → Phase 2 → Phase 7 (MCP)**, in that order (MCP strictly after 1 and 2). **Deferred beyond this horizon:** Phase 6 (worktrees) and Phase 4.4 (merge/conflict-resolution view) — conflicts are resolved via the agent in the terminal, so an in-app merge UI isn't built now. Consequently the merge-specific parts of Phase 3 (3.1 merge-parent guards, 3.6 conflicts UI, 3.9 stash-switch) and MCP 7.6 (worktree tools) are deferred too. **Add-on to Phase 2:** pull in 3.4 (`build_git_env` with `LC_ALL=C` + `GIT_TERMINAL_PROMPT=0`). **MCP v1 scope:** read tools (7.3) + mutating tools (7.4). See the revised version mapping at the bottom.

## How to use this document (for the implementing agent)

- Read `CLAUDE.md` first — especially the **GTK4/libadwaita Gotchas** section (dialog text input, ShortcutController scope, icons) and the development rules (use `uv`, English code/comments, **no commits without the user's approval**).
- Work phase by phase, checkpoint by checkpoint. Update the `[ ]` boxes here as you go.
- Each checkpoint lists: the defect (with file references), the required fix, and acceptance criteria.
- Run the app with `uv run python -m src.main --project <path>` to verify changes live.
- Phases are ordered by dependency: **do not** start Phase 4+ features before Phase 2 (async layer), or they will inherit the same races.

## Guiding principles

1. **No data loss, ever.** Every write is atomic; every save checks the disk state first; every close checks the save result.
2. **One async pattern.** All background work goes through a single helper (generation token + widget-liveness guard + marshalled callback). No ad-hoc `threading.Thread` + `GLib.idle_add` pairs.
3. **States are visible.** Repo states (merge, conflicts, detached HEAD), operation states (pushing…), and file states (changed on disk) are shown in the UI, never swallowed.
4. **Reuse existing UI idioms.** Toasts for passive info; `Adw.AlertDialog` (+`set_extra_child`) for decisions; popovers for compact actions (like `branch_popover`); reusable main-area tabs for detail views (like `commit_detail_view`). Two new idioms are introduced: `Adw.Banner` for persistent in-view state, and in-button spinners for in-flight operations. **No new sidebar toolbar letters, no new sidebar panels.**
5. **Errors surface with the failure, not a raw dump.** Every error path either toasts (transient), banners (persistent state), or dialogs (decision needed) — never `print()` or silent `pass`.

---

## Phase 1 — Data Safety (editor & files)

Goal: eliminate every identified data-loss path. Small in code volume, highest value. No new UI surfaces except one banner and two dialogs.

### 1.1 External-change detection in `FileEditor`
- [x] Defect: `FileEditor` (`src/widgets/file_editor.py`, `__init__` ~line 25) never monitors its own file and is not subscribed to `FileMonitorService`; the service only watches tree-expanded directories. An open file rewritten externally (by Claude Code, `git checkout`, etc.) is silently stale.
- Fix: on open, record `os.stat` mtime+size and attach a `Gio.FileMonitor` for the file itself (or subscribe to `FileMonitorService` with a per-file filter). On external change:
  - buffer **unmodified** → auto-reload silently + toast "File reloaded from disk".
  - buffer **modified** → show `Adw.Banner` at the top of the editor: "File changed on disk" with buttons **Reload** and **Diff** (Diff opens buffer-vs-disk comparison in the existing reusable diff tab).
- The banner is state, not an event: it stays until resolved, and must not steal focus.
- Dispose the monitor when the tab closes.
- Acceptance: open file → edit it externally → banner appears (dirty buffer) or content auto-reloads (clean buffer). Closing the tab detaches the monitor (no leak, verify with repeated open/close).

### 1.2 Pre-save conflict guard (mtime check)
- [x] Defect: `save()` (`file_editor.py` ~539, `svg_editor.py` ~736) writes blindly. Combined with 1.1's blindness this silently clobbers external edits. Implicit save paths make it worse: tab close (`project_window.py` ~1619), window-close Save All (~1673), rename (~1253), delete (~1339), run-script autosave (`file_editor.py` ~630).
- Fix: before writing, re-stat the file. If mtime/size differ from last known → `Adw.AlertDialog`: **Overwrite / Reload / Show Diff** (Cancel semantics on Reload/Diff: the save is aborted). Update stored mtime after every successful save/reload.
- Acceptance: edit in editor + edit externally → Ctrl+S shows the conflict dialog; Overwrite writes; Reload discards buffer with confirmation if dirty.

### 1.3 Atomic writes
- [x] Defect: `open(path, "w")` truncates before writing (`file_editor.py` ~546, `svg_editor.py` ~743, `unified_search.py` `_on_replace_all` ~457). Crash/ENOSPC mid-write destroys the file.
- Fix: shared helper (e.g. `src/utils/atomic_write.py`): write to a temp file in the same directory → `flush` + `os.fsync` → `os.replace(tmp, target)`. Preserve original file mode (`os.stat().st_mode`). Use it for editor save, svg save, and search replace.
- Acceptance: unit-testable helper; simulated failure (e.g. exception between write and replace) leaves the original file intact.

### 1.4 Respect `save()` failures in all close flows
- [x] Defect: callers discard the boolean: `_on_unsaved_close_response` (`project_window.py` ~1617) closes the tab even if save failed; same for Save All on window close (~1667-1681), rename (~1253), delete (~1339).
- Fix: if `save()` returns False → abort the close/rename/delete, keep the tab open, show a persistent error `Adw.Banner` in the editor with the OS error text (a toast would vanish and leave a mystery open tab).
- Acceptance: make a file read-only on disk, edit, close tab → "Save" fails → tab stays open with an error banner.

### 1.5 Undo stack integrity
- [x] Defect: `_load_file` (`file_editor.py` ~511) and `reload()` (~329) call `buffer.set_text()` without `begin_not_undoable_action()`. Ctrl+Z right after opening wipes the buffer to empty; undo after reload interleaves corrupt states.
- Fix: wrap both `set_text` calls in `begin_not_undoable_action()` / `end_not_undoable_action()`.
- Acceptance: open file → Ctrl+Z → nothing happens. Reload → Ctrl+Z undoes only user edits made after reload.

### 1.6 Encoding & line endings
- [x] Defect: text-mode IO with default newline translation silently converts CRLF/CR/mixed files to LF on save (`file_editor.py` ~514/546) → spurious full-file git diffs. Non-UTF8 files put "Error loading file:" into the buffer and never restore `editable` on a later successful reload (~523-525). Binary files with unknown extensions route into the editor (`project_window.py` ~1471).
- Fix:
  - Read with `newline=""`; detect dominant line ending; store it on the editor; join with it on save. (Optional later: show ending in a status area.)
  - On successful load always `set_editable(True)`; track a `_load_failed` flag that blocks `save()`.
  - Binary sniff (null byte in first 8 KB) before opening in `FileEditor` → show a placeholder view instead.
- Acceptance: CRLF file survives open/edit/save with CRLF intact (`git diff` shows only the edited line). Binary file opens as placeholder, not garbage.

### 1.7 Safe global Replace All (`UnifiedSearch`)
- [x] Defect (`src/widgets/unified_search.py` `_on_replace_all` ~442-464): hardcoded `re.IGNORECASE` substring replace over **whole files** (not just shown matches); locale-default encoding via `read_text()`/`write_text()`; newline translation; non-atomic; synchronous on the UI thread; errors go to `print()`; open editors not notified (a later editor save clobbers the replacement).
- Fix:
  - Replace only within the match ranges actually found by the search (same case sensitivity the search used).
  - Confirmation `Adw.AlertDialog` before applying: "N replacements in M files" with the file list in `set_extra_child`.
  - UTF-8 + `newline=""`, atomic write via the 1.3 helper, run in a worker thread (see Phase 2 helper), per-file errors collected and shown in a summary dialog.
  - After writing, emit through `FileMonitorService` so open editors pick it up via 1.1 (clean buffers auto-reload; dirty buffers banner).
- Acceptance: search "foo", file containing "FOObar" is NOT touched unless the search itself matched it; open+clean editor of a replaced file shows the new content; simulated write error appears in the summary.

### 1.8 Delete/rename of open files
- [x] Defect: deleting a file with unsaved edits pops "Unsaved Changes / Save?" — and Save resurrects the just-deleted file (`project_window.py` `_close_tabs_for_path` ~1227 → `_on_tab_close_requested` ~1545). Rename force-saves to the OLD path before renaming (~1253) without asking. Externally renamed files leave the editor writing to a ghost path (`file_editor.py` stores `file_path` at construction, never updated).
- Fix: delete-driven closes force-close (clear modified flag or dedicated force path). Rename updates `editor.file_path` in place instead of save+close. External delete/rename detected via the 1.1 monitor → banner "File was deleted/moved on disk" with Save-As / Close actions.
- Acceptance: delete an open modified file → tab closes, no dialog, no resurrected file. Rename an open file in-app → same tab, new path, edits intact.

### 1.9 App-quit guard
- [x] Defect: window `close-request` guards unsaved changes, but app-level quit paths (SIGTERM, future Ctrl+Q) bypass it (`src/main.py` has no `do_shutdown` handling). The `FileEditor` docstring claims "autosave" that does not exist (`file_editor.py:18`).
- Fix: route quit through the same unsaved-changes check; correct the docstring. (Full crash-recovery drafts are out of scope for this phase.)
- Acceptance: quitting with dirty buffers prompts exactly like window close.

---

## Phase 2 — Unified Async Layer

Goal: one correct concurrency pattern everywhere; no UI-thread blocking; no stale-result races; no callbacks into dead widgets.

Reference implementations already in the codebase (copy these, don't invent):
- correct debounce: `FileMonitorService._schedule_signal` (`src/services/file_monitor_service.py` ~316-357) — cancels the previous `timeout_add` id;
- correct generation token: `issue_detail_view.py` `_comments_token` (~246-265, 327-343);
- correct liveness guard: `git_changes_panel._apply_refresh` (~324) `hasattr` check.

### 2.1 `run_async` helper
- [x] Create `src/utils/async_runner.py` (or `services/`): `run_async(widget, worker, on_done, on_error=None)`:
  - runs `worker` in a daemon thread, wraps the body in try/except;
  - marshals the result via `GLib.idle_add`;
  - **generation token**: each call site owns a counter; results from superseded calls are dropped;
  - **liveness guard**: callback first checks `widget.get_root() is not None` (or an explicit cancelled flag) and bails;
  - `on_error` default: toast via `ToastService` + reset any "loading" UI state.
- Acceptance: a demo race (two calls, first resolves last) applies only the newest result; destroying the widget mid-flight produces no GTK-CRITICAL.

### 2.2 Off-thread push/pull (CRITICAL freeze fix)
- [x] Defect: `_do_pull`/`_do_push` (`src/widgets/git_changes_panel.py` ~682-708) call `git_service.pull/push` (subprocess with 60 s timeout) synchronously on the UI thread → whole-window freeze on slow network. `has_uncommitted_changes()` (full `repo.status()`) also runs on the UI thread before pull (~655).
- Fix: run via 2.1; while in flight: spinner inside the Push/Pull button + `set_sensitive(False)` on mutating panel actions; completion → toast (success) or dialog (failure, see 3.5). The `AuthenticationRequired` retry loop must also stay off-thread and gain a retry cap (2-3 attempts, then give up with the dialog).
- Acceptance: pull against an unreachable remote — UI stays responsive, button spins, error dialog appears after timeout.

### 2.3 Off-thread stage/unstage/commit/status
- [x] Defect: pygit2 index ops and `status()` run in click handlers (`git_changes_panel.py` ~569-650); `index.add_all()`/`write_tree()` walk the whole worktree.
- Fix: route through 2.1 with the same busy-button pattern. Serialize mutating git ops per panel (a simple in-flight flag: queue or ignore clicks while one runs).
- Acceptance: staging in a large repo does not stall the window; double-clicking Commit cannot produce two commits.

### 2.4 Coalesced, generation-guarded changes refresh
- [x] Defect: `git_changes_panel.refresh()` (~237-276) spawns an unbounded thread per trigger (2 s poll + monitor signals + every action); stale results overwrite fresh state.
- Fix: single debounced trigger (150-300 ms) merging poll + monitor + post-action refreshes; generation token so only the newest `_fetch` result renders. Consider dropping the 2 s poll entirely once `FileMonitorService` signals are trusted (keep a slow 30 s fallback poll).
- Acceptance: `git checkout` of a big branch produces one visible refresh, not a flicker storm; staged/unstaged sections never show pre-action state after an action.

### 2.5 `UnifiedSearch` correctness
- [x] Defect (~183-229): debounce gates scheduling instead of resetting the timer; Enter bypasses debounce; no generation token → old query's results overwrite the new query's; concurrent `rg`/`grep`/`find` subprocesses unbounded; no widget-liveness guard.
- Fix: canonical debounce (cancel prior timeout id); generation token checked in `_display_results`; terminate superseded subprocesses (`Popen.kill()`); route through 2.1.
- Acceptance: type fast then pause — results always match the entry text; no flicker between queries.

### 2.6 Liveness guards on all remaining idle_add landing points
- [x] Audit list: `claude_history_panel.py` `_on_sessions_loaded` (~160), `problems_panel.py` (~197, ~333), `file_tree.py` `_apply_git_status` (~503), `project_manager.py` callbacks (~439, 475, 564, 770), `notes_panel.py` `_display_todos` (~330).
- Fix: migrate to 2.1 (preferred) or add the guard inline.
- Acceptance: closing tabs / switching projects during background loads produces zero GTK-CRITICAL warnings in the console.

### 2.7 `ProjectStatusService` cache lock
- [x] Defect: `_cache` dict + JSON file mutated from worker threads while the main thread reads (`src/services/project_status_service.py` ~149-195 vs `project_manager.py` ~443-484).
- Fix: `threading.Lock` around cache read/write and file write (or mutate only via idle_add on the main thread).

### 2.8 Badge generation tokens
- [x] Defect: header badge fetchers (`project_window.py` ~416-460, ~688-708) have liveness guards but no ordering — stale counts can land last.
- Fix: latest-wins token per badge (comes free with 2.1).

### 2.9 File-tree refresh coalescing
- [x] Defect: `_on_working_tree_changed` (`src/widgets/file_tree.py` ~811) triggers a **full tree rebuild + `git status` subprocess per changed path** (service debounces per-path, so N files → N rebuilds).
- Fix: single debounce key for tree refresh; one in-flight git status at a time (generation-guarded). Incremental row updates are a stretch goal, not required now.
- Acceptance: branch switch touching 50 files → one or two rebuilds, not dozens.

---

## Phase 3 — Git Robustness

Goal: existing git operations become trustworthy; repo states become visible; errors become actionable.

### 3.1 Commit guards
- [ ] Defects (`src/services/git_service.py` `commit()` ~213-244): ignores `repo.index.conflicts` and repo state (`MERGE_HEAD`) → mid-merge commit silently drops the second parent or commits conflict markers; empty commits allowed (no tree-vs-HEAD check); DOM-based commit-button gating in the panel races the async refresh (`git_changes_panel.py` ~622-636); stale in-memory pygit2 index after CLI pull (no `index.read()`).
- Fix: in `commit()` — `repo.index.read()` first; refuse if `index.conflicts`; if `repo.state()` is merge → create the commit with both parents (`HEAD` + `MERGE_HEAD`) and clear state; reject empty tree unless amend; gate the button on service state, not on scanning widget children.
- Acceptance: mid-merge commit produces a correct 2-parent merge commit; commit with nothing staged is refused with a toast; conflicted index blocks commit with a pointing message.

### 3.2 Error surfacing policy
- [ ] Defects: `unstage`/`unstage_all` swallow `pygit2.GitError` (`git_service.py` ~165-183) — buttons appear dead; status errors render as an empty change list = false "No changes" (`git_service.py` ~100, `git_changes_panel.py` ~270); auth helpers `except Exception: pass` (`git_auth.py` ~125, 198); `refresh_remote_status` swallows fetch failures so "behind" is silently stale (`project_status_service.py` ~135).
- Fix: propagate errors to the panel; a failed status shows an in-panel error state ("Couldn't read repository status: …" + Retry), never an empty list; failed fetch marks the badge/timestamp as stale.
- Acceptance: chmod the repo `.git` unreadable → panel shows the error state, not "No changes".

### 3.3 `restore_file` via real checkout
- [ ] Defect (`git_service.py` ~185-211): hand-writes `blob.data` to the worktree — bypasses `.gitattributes` filters (CRLF/smudge), writes symlink targets as file contents, never clears an exec bit, ignores the index (staged deletion not undone).
- Fix: use `repo.checkout(paths=[...], strategy=FORCE)` or `git checkout -- <path>`.
- Acceptance: discard on a symlink restores the symlink; discard on a CRLF-attributed file preserves CRLF.

### 3.4 Deterministic git CLI environment
- [x] Defect: locale-dependent stderr string matching — "has no upstream branch" retry (`git_service.py` ~407-422), auth-error indicators (`git_auth.py` ~18-26), "Already up to date" (~369) — breaks on non-English systems. `GIT_TERMINAL_PROMPT=0` missing from panel status subprocesses and `get_ahead_behind` → possible hangs on prompt.
- Fix: one `build_git_env()` used by every subprocess call: `LC_ALL=C`, `GIT_TERMINAL_PROMPT=0` (+ auth vars when needed). Prefer exit codes / `--porcelain` output over message matching where possible.

### 3.5 Actionable push/pull failure dialogs
- [ ] Defect: rejected push / diverged / pull conflicts → raw stderr dump (`git_changes_panel.py` ~691, ~708), repo left conflicted with no UI.
- Fix: classify outcomes: non-fast-forward push → `Adw.AlertDialog` with **Pull & retry** / **Force push** (destructive appearance, extra confirm) / **Cancel**; pull with conflicts → dialog explaining state + the 3.6 conflicts UI takes over. Keep raw output available behind an expander for debugging.

### 3.6 Repo state visibility in the Changes panel
- [ ] Merge/revert/cherry-pick in progress → `Adw.Banner` at the top of the panel ("Merge in progress — resolve conflicts"); conflicted files listed in a third **Conflicts** section (alongside Staged/Unstaged) with distinct icon; Commit disabled with explanation while conflicts exist. Detached HEAD → warning-styled branch button + "detached" label; branch popover gains "Return to branch…". Unborn repo (no commits) → explicit hint instead of empty history.
- Depends on: 3.1.

### 3.7 Secure credentials
- [ ] Defects: PATs stored plaintext via `credential-store` → `~/.git-credentials` (`git_auth.py` ~173-199), stored unconditionally on success without consent (`git_service.py` ~419, 429); askpass temp scripts never unlinked (`git_service.py` ~886-892).
- Fix: store via Secret Service (libsecret — GNOME keyring is a safe assumption for this app's audience): use a `git credential-libsecret` helper if available, else the `Secret` GI API; add a "Remember credentials" checkbox to the auth dialog (default off); unlink askpass scripts in a `finally`.
- Acceptance: after an authenticated push with "remember" unchecked, `~/.git-credentials` does not exist/grow; askpass scripts don't accumulate in `$TMPDIR`.

### 3.8 Diff/status parsing fixes
- [ ] Binary files: `get_diff` decodes blobs unconditionally (`git_service.py` ~246-292) → garbage; use `delta.is_binary`/null-byte check → "Binary file" placeholder in `DiffView`.
- [ ] Renames: porcelain parser drops `old_path` and desyncs on copies (`git_changes_panel.py` ~278-320); parse `-z` rename/copy pairs, populate `GitFileStatus.old_path`, render "old → new".
- [ ] Deduplicate status logic: the panel's porcelain parser vs `GitService.get_status()` (pygit2) disagree; pick ONE source of truth (recommended: keep the CLI porcelain path since it's already threaded, move it into `GitService`, delete the pygit2 twin).

### 3.9 Branch switch safety
- [ ] Defect (`git_service.py` `switch_branch` ~686-713): double `status()` call, weak guard (untracked-only trees pass then SAFE checkout throws cryptic pygit2 errors surfaced raw in `branch_popover.py` ~216).
- Fix: single status check; on dirty tree → dialog **Stash & switch** (once 4.2 lands; before that: "Commit or discard first" message); catch checkout conflicts and translate to a readable message.

### 3.10 Monitor gaps
- [ ] `.git/packed-refs` not watched; repos initialized after window open never get git monitors; dangling monitors for externally-deleted expanded dirs are never pruned (`src/services/file_monitor_service.py` ~77-124, ~220-228; `file_tree.py` ~786-805).
- Fix: watch `packed-refs`; re-evaluate `_is_git_repo` on demand; prune `_expanded_paths` entries whose paths vanished.

---

## Phase 4 — Git Features

Goal: close the everyday-workflow gaps. Every feature reuses Phase 2 async + Phase 3 error surfacing. UI homes are fixed here so the implementing agent doesn't invent new surfaces.

### 4.1 Clone from URL (Project Manager)
- [ ] "Clone" button next to "New Project". Dialog (`Adw.AlertDialog` + `set_extra_child` per CLAUDE.md gotchas): URL entry + destination folder picker + optional name. On confirm: the project card appears immediately in the list with a "Cloning…" spinner state; `git clone --progress` runs in a worker parsing progress; success → registered + openable; failure → card shows error state with Retry/Remove. Reuse the 3.7 auth flow for private remotes.
- Acceptance: clone a private HTTPS repo end-to-end without the UI freezing; a bad URL shows the error on the card.

### 4.2 Stash
- [ ] Stash icon-button in the Changes panel header with a popover (pattern: `branch_popover.py`): list stashes (message + relative time), actions Stash (with optional message, include-untracked toggle), Pop, Drop (confirm). Wire into 3.9's dirty-switch dialog ("Stash & switch").
- Backend: pygit2 `repo.stash*` or CLI — pick one, follow 3.8's single-source decision.

### 4.3 Commit UX: amend + multiline message
- [ ] Replace the message `Gtk.Entry` (`git_changes_panel.py` ~190) with a 2-3-line auto-growing `Gtk.TextView` (Ctrl+Enter = commit). Replace the Commit button with `Adw.SplitButton` (pattern already used in `script_toolbar.py`): primary = Commit, menu = "Amend last commit" (pre-fills last message, confirm dialog if HEAD is pushed — check ahead count).

### 4.4 Merge + conflict resolution view
- [ ] Branch popover: per-branch secondary action "Merge into current". Conflicts → the state banner from 3.6 plus a reusable main-area **Conflicts** tab (pattern: `commit_detail_view.py` — list left, detail right): conflicted file list; per-file view with conflict hunks and **Ours / Theirs / Open in editor** actions per hunk; "Mark resolved" stages the file; when the Conflicts section is empty, the banner offers "Complete merge" (→ 3.1's merge-parent commit).
- This is the largest UI item of the phase; build it after 4.2/4.3.

### 4.5 Force push + upstream management
- [ ] Force push exists only via 3.5's rejected-push dialog (always `--force-with-lease`, destructive-styled, extra confirmation). "Set upstream" surfaced when a branch has none (badge area shows "not published" instead of nothing — fixes the silent `(0,0)` ahead/behind).

### 4.6 New Project polish
- [ ] `git init` gains: warning when the target folder is non-empty; `--initial-branch` from a setting (default `main`); optional initial commit (empty or with generated `.gitignore`/`README`) so the repo isn't left unborn.

### 4.7 SSH awareness
- [ ] Detect SSH remotes before push/pull: if the agent has no identities (`ssh-add -l` non-zero), show a clear dialog ("SSH key not available in agent…") instead of the useless username/password dialog. Full passphrase askpass is out of scope; the goal is honest messaging.

### 4.8 Remote branch checkout
- [ ] Branch popover: remote branches get "Checkout as local tracking branch" (backend `create_branch(from_ref=...)` already exists at `git_service.py` ~649 — UI just never uses it).

Deferred beyond v0.9 (record only): hunk-level staging, tags UI, remotes management, blame, cherry-pick UI, compare-arbitrary-commits.

---

## Phase 5 — Reviewer-Oriented Editor

Goal: the editor serves a human *reviewing* AI-written code: navigation and comprehension over typing features.

### 5.1 Session viewer scalability
- [ ] Defect: `load_session_content` (`src/services/history.py` ~138) materializes the whole JSONL; `session_view.py` (~56-79) builds one widget per message in a `Gtk.Box`, synchronously on the UI thread → multi-MB agent sessions freeze the app.
- Fix: parse off-thread (Phase 2 helper); render via `Gtk.ListView` + `Gio.ListStore` (virtualized) or paginate (load last N, "Load earlier" button). Cap giant tool-result payloads with an expander.
- [ ] JSONL robustness: catch `UnicodeDecodeError` (currently uncaught — it is not an `OSError`; `history.py` ~135/172), open with `errors="replace"`; tolerate a partial trailing line and show a "session in progress" indicator instead of silently dropping it.
- Acceptance: a 50 MB session opens without freezing; a truncated last line doesn't crash or vanish silently.

### 5.2 Tab path normalization
- [ ] Defect: tab dedup compares raw strings (`project_window.py` ~1467, ~734) while paths arrive from tree/search/notes/git in different forms → duplicate tabs, divergent buffers, last-save-wins.
- Fix: normalize every path to `Path(...).resolve()` at a single chokepoint (`_open_file`) before comparison/storage.

### 5.3 "Changed since opened" diff
- [ ] Toolbar button on the editor: diff current buffer vs the content at open/last-save (kept snapshot), shown in the reusable diff tab. Pairs naturally with 1.1's Diff action (same code path).

### 5.4 Find/replace completion (per `docs/TODO-editor-search.md`)
- [ ] Ctrl+H replace mode, whole-word toggle (`SearchSettings.set_at_word_boundaries`), "N of M" positional counter, invalid-regex indication (currently silent, `file_editor.py` ~376-421), replace-all wrapped in one `begin_user_action` (atomic undo).
- [ ] Fix regex-engine mismatch: validation uses Python `re` but execution uses GtkSource/PCRE (`file_editor.py` ~436-449) — validate with the engine that executes (try the GtkSource search, catch its error) or drop pre-validation.

### 5.5 Navigation
- [ ] Ctrl+G go-to-line dialog (backend `go_to_line` exists); project-wide go-to-symbol as a stretch goal (reuse `python_outline.py` over all files, cached).

---

## Phase 6 — Worktrees & Parallel Agents (v1.0)

Goal: worktree = a task. Create with a branch + provisioned environment; see every agent's activity from the main window; merge back and clean up. Depends on Phases 2 (async), 3 (git robustness), 4.4 (merge/conflicts UI).

**History note**: a previous attempt lives on the unmerged branch tagged `last-working-state` (commits `a827ac6`, `710d65d`). It targeted self-development (stable + one dev worktree), used **detached-HEAD** worktrees synced by cherry-pick, and did no environment provisioning — both decisions are explicitly **rejected** here (branch-based worktrees + provisioning pipeline instead). Reusable from that branch: `git show last-working-state:src/services/window_state_service.py` (per-project window-state persistence, useful independently) and the registry worktree-methods structure (`git show 710d65d:src/services/project_registry.py`) — lift the shape, replace the semantics.

### 6.1 Correctness base
- [ ] `FileMonitorService` worktree support: it currently requires `project_path/.git` to be a **directory** (`file_monitor_service.py` ~64-65), so in a worktree (where `.git` is a file) all git monitors are silently skipped. Fix: if `.git` is a file, read its `gitdir:` pointer; watch `HEAD`/`index`/`logs/HEAD` in the resolved per-worktree gitdir (`<main>/.git/worktrees/<name>/`) and shared refs (`refs/`, `packed-refs`, `logs/`) in the common gitdir (resolve via the `commondir` file or `git rev-parse --git-common-dir`).
- [ ] Worktree self-detection in `ProjectWindow`: if the open path is a linked worktree, show a header badge "worktree of <parent> · <branch>". (The pygit2 layer already works — `discover_repository` handles the `.git` file.)
- [ ] Sync-engine exclusion: `resolve_project_identity` (`src/services/project_identity.py` ~63-104) keys on origin URL/root-commit, so a worktree collides with its parent's sync slot. Exclude worktree-registered projects from sync (they're ephemeral) — detect via `.git`-is-file.
- Acceptance: open a worktree as a project → git status/history live-refresh works; badge shown; sync ignores it.

### 6.2 Lifecycle with provisioning
- [ ] Registry model: extend `ProjectRegistry` v2 with `worktrees: {parent_path: [{path, name, branch}]}` (+ migration adding the key; pattern exists in the old branch). Helpers: `get_worktrees`, `is_worktree`, `get_parent_project`.
- [ ] **New Worktree** dialog (from the Project Manager card menu AND from the project window's worktree popover, 6.3): single input = task name → derived branch `feature/<slug>` (editable) and path `<parent>--<slug>` sibling directory (editable). On confirm, a background pipeline with per-step progress reporting:
  1. `git worktree add <path> -b <branch>` (never detached);
  2. environment provisioning: if `pyproject.toml`/`uv.lock` exists → `uv venv && uv sync` in the worktree (uv hardlinks from its cache — seconds, near-zero disk). Configurable per project via settings key `worktree.provision_command` (default auto-detect uv; empty = skip);
  3. copy untracked files listed in `worktree.copy_untracked` (default `.env`) if present;
  4. register in the registry as a child of the parent.
- [ ] Remove Worktree: refuses (with explanation) if dirty or has unpushed/unmerged commits, unless the user confirms a destructive override; then `git worktree remove` + optional branch delete + unregister.
- Terminal note: `TerminalView` venv activation already keys off cwd (`terminal_view.py` ~304-312: sources `<cwd>/.venv/bin/activate` if present), so a provisioned worktree gets activation with **zero** terminal changes.
- Acceptance: create a worktree in a uv project → open it → terminal has the venv active, `uv run pytest` works immediately.

### 6.3 Visibility (the "loss of control" fix)
- [ ] Project Manager: worktrees render as indented child rows under the parent's card (the row pattern existed pre-`d8a953d`; re-implement against the current git-centric UI) with badges: branch, dirty ●, ahead/behind **vs the parent's main branch**, last Claude-session activity time.
- [ ] Project window: "Worktrees" `MenuButton`+popover in the header (pattern: `branch_popover.py`): rows = each worktree with status + actions **Open window** (spawns via the existing per-path process model — locks are per-path (`project_lock.py` ~119), so parent + worktrees open concurrently already), **Merge back**, **Remove**; footer = "New worktree…".
- [ ] Aggregated Claude sessions: session history is keyed by path encoding (`/`→`-`, `src/utils/paths.py` ~6), so each worktree's sessions live in a different `~/.claude/projects/` dir, invisible today. Extend `HistoryService` with `get_sessions_for_paths([...])`; the Claude panel in the parent window enumerates `git worktree list --porcelain` paths and shows their sessions in collapsible per-worktree groups (parent's own sessions first). This is how the user watches every agent from one window.
- Acceptance: run a Claude session inside a worktree → it appears in the parent window's Claude tab under that worktree's group after refresh.

### 6.4 Merge-back flow
- [ ] "Merge back" on a worktree row: dialog summarizing the branch diff (commit count + files, reuse `commit_detail_view` pieces) → merge into the parent's current branch using 4.4's machinery (conflicts → Conflicts tab in the *parent* window) → on success offer "Remove worktree and branch" cleanup. Guard: refuse if the worktree itself is dirty (offer stash via 4.2 or commit first).
- Acceptance: full cycle — create worktree, agent commits on its branch, merge back from the parent window, cleanup — without touching a terminal.

---

## Phase 7 — MCP Control Surface (v1.0, parallel to Phase 6)

Goal: make the app↔Claude link bidirectional (GitHub issue #2): the app hosts a local MCP server so Claude Code can drive the live UI. Depends on **Phase 2** (thread-marshaling discipline); the worktree tools additionally depend on Phase 6.

**Decisions (settled 2026-07-06):**
- Transport: **Streamable HTTP** on `127.0.0.1` (SSE-as-transport is deprecated in the MCP spec; stdio doesn't fit a long-lived GUI process).
- Registration: the app already launches `claude` itself (`_start_claude_session`), so it generates a temporary MCP config with this window's port + a per-session auth token and starts `claude --mcp-config <file>`. No `.mcp.json` in the repo, no persistent registration, per-window isolation for free (each NON_UNIQUE process = own server on a random port). Optional later: `claude mcp add --scope local` with a path-hash-derived deterministic port for Claude sessions started outside the app.
- Security: localhost bind + random session token required in a header; **no in-app confirmation UI for mutating tools** — Claude Code's own MCP permission system already covers that.
- Threading: the server runs in a background thread with its own asyncio loop; every tool handler marshals to the GTK main thread via `GLib.idle_add` + `concurrent.futures.Future` with a timeout (never touch GTK from the server thread; never block the main loop waiting on the server).
- **Scope principle**: only tools that act on the *running app* (UI, panels, app-owned state). No generic file-read/search/git tools — Claude already has those natively; duplicating them adds confusion and attack surface.

### 7.1 Server infrastructure
- [ ] `src/services/mcp_server.py`: Python MCP SDK (FastMCP), streamable HTTP, thread + asyncio loop, lifecycle bound to the `ProjectWindow` (started on window init if enabled, stopped on destroy). Marshaling helper `call_on_main(fn, timeout=5)` used by every tool. Token check middleware.
- Acceptance: a tool call from the embedded Claude session round-trips; closing the window frees the port; a tool that raises returns an MCP error, not a hang.

### 7.2 Registration & settings
- [ ] Generate the MCP config at Claude-terminal launch; pass `--mcp-config`. Setting `mcp.enabled` (default true) with a Preferences toggle; when off, launch `claude` bare.
- Acceptance: toggling the setting and restarting the Claude tab adds/removes the tools (`/mcp` in Claude shows the server).

### 7.3 Tools v1 — read & present (issue #2 first cut, extended)

| Tool | Effect | Why it earns its place |
|------|--------|------------------------|
| `get_workspace_state()` | Active file, cursor line, open tabs with **dirty flags** | One call answers "what is the user looking at"; dirty flags let an agent avoid editing a file with unsaved buffer edits (pairs with Phase 1.1/1.2) |
| `get_selection()` | Current editor selection (path, range, text) | Deixis: user selects code and says "объясни/поправь это" — no more pasting |
| `open_file(path, line?, end_line?)` | Open tab, scroll, highlight range | From issue; range highlight added (absorbs `goto_line`) |
| `show_diff(path)` | Open working-tree diff view | From issue |
| `show_commit(hash)` | Open commit detail tab | Completes "let me show you what I did" |
| `get_problems(path?)` | Current ruff/mypy findings | Cheap agent self-check after edits, same view as the Problems panel |
| `list_tasks()` | Names from tasks.json | Discoverability for `run_task` |
| `notify(message)` | Toast + desktop notification if unfocused | Long agent run finished → user's attention; the smallest highest-value tool |

### 7.4 Tools v1 — mutating (explicit, few)
- [ ] `create_issue(title, body)` + `refresh_issues()` (from issue #2, via `IssuesService`).
- [ ] `run_task(name)` — run a tasks.json task in the app's terminal.
- [ ] `add_note(name, content)` — write/append `notes/<name>.md` (Notes panel is app-owned state: lets the agent persist design decisions where the user keeps theirs).
- Acceptance: each mutating tool triggers the relevant panel refresh through existing signals, not bespoke plumbing.

### 7.5 Hook hybrid (automation without the model)
- [ ] Document (in README or docs/) a `PostToolUse` hook snippet that POSTs to the server's `/refresh` endpoint after `gh issue create` / `git commit`, so panels auto-refresh even when the model doesn't call a tool. The server exposes that one plain-HTTP endpoint alongside MCP.

### 7.6 Worktree orchestration tools (after Phase 6)
- [ ] `list_worktrees()` — worktrees with branch/dirty/ahead-behind/last-session-activity (same data as 6.3 badges).
- [ ] `create_worktree(task_name)` — runs the full 6.2 pipeline (branch + provisioning); returns the path so the agent can hand it to a sub-agent or tell the user to open it.
- [ ] Agent-completion flow: an agent in a worktree calls `notify` + `show_diff`-equivalent against the **parent** window (route via the parent's server; the worktree's own config includes the parent's endpoint).
- Acceptance: from the parent window's Claude session: create a worktree, verify it in the popover, get notified when the sub-agent's branch has commits.

---

## Phase 8 — Agent Observability (v1.0 track)

Goal: make the agent's work transparent — what a session cost, what it changed, where work stopped. This is the app's unique territory: editors are plentiful; a tool that makes AI work reviewable and searchable is not. All UI lands on existing surfaces (Claude panel rows, Project Manager cards, Notes panel) per principle 4.

**Dependencies**: 8.1 builds directly on Phase 5.1's history-parsing rework (do them together — one parsing pass, not two rewrites). If the v0.8 adapter refactor (`docs/plan-code-companion-refactor.md`) has landed, implement 8.1 inside the Claude adapter behind the `HistoryService` interface. 8.6 is fully independent and can be done at any time.

### 8.1 Session insight index (foundation)
- [ ] Extend the (post-5.1) JSONL parsing to extract per session: token usage per message (the `usage` fields on assistant messages: input/output/cache tokens), files touched by `tool_use` blocks (Edit/Write/NotebookEdit inputs — explicit file tools only, no Bash guessing), first/last timestamps, first user prompt, and last assistant text (trimmed).
- [ ] Cache the extracted summary per session file, keyed by mtime/size (JSON cache under `~/.config/code-companion/`), so panels never re-parse unchanged JSONL. Parsing runs off-thread via the Phase 2 helper.
- Acceptance: opening the Claude tab on a project with 100 sessions shows enriched rows without re-parsing unchanged files (verify via timing/log).

### 8.2 Session cost display
- [ ] Claude panel: per-session token totals on the row; aggregate for the project (and today) in the panel header. Show **tokens** as the primary number; show an approximate cost figure from a bundled price table (per model id found in the session), clearly marked as an estimate — prices drift, tokens don't.
- Acceptance: totals match a manual sum of the JSONL `usage` fields for a sample session.

### 8.3 "What changed this session" review
- [ ] Two correlated sources, both from 8.1: (a) the tool-touched file list; (b) commits whose author time falls inside the session's time range (`git log --since/--until` on the current branch). Session detail view gets a **Changes** section: touched files + session-range commits.
- [ ] "Review session changes" action: if the session maps to commits → open the existing diff machinery for the range `<first-commit>^..<last-commit>` (reuse `commit_detail_view` pieces); for uncommitted sessions → working-tree diff filtered to the touched files.
- The user's workflow is many small commits per session — the commit-range view is the primary presentation; the touched-file list is the fallback and the cross-check.
- Acceptance: run a session that edits 3 files across 2 commits → its Changes section lists both commits and all 3 files; the range diff opens in one click.

### 8.4 "Where we left off" in Project Manager
- [ ] Per-project card gains a one-line resume hint from the most recent session (via 8.1 cache, background-loaded like the existing status badges): relative time + trimmed last assistant text or first prompt (e.g. "2h ago — implemented Phase 1.3, tests pending"). Clicking it opens the project with that session selected in the Claude tab.
- Acceptance: the hint appears without blocking the manager's startup; projects without sessions show nothing.

### 8.5 Cross-project prompt search
- [ ] Search box (Project Manager, near the existing project search): full-text search over **user prompts** across all encoded dirs in `~/.claude/projects/`. Implementation: `rg` over JSONL constrained to `"type":"user"` lines (fallback: the 8.1 cache), results grouped project → session with prompt snippet + date; activating a result opens the project window at that session.
- Acceptance: find a phrase from a months-old prompt in another project in under a couple of seconds.

### 8.6 Plan progress from checkboxes (independent, cheap)
- [ ] Notes/Docs panel: for `docs/plan-*.md`, parse `- [ ]` / `- [x]` and render a progress indicator (`12/45`) next to the doc row; headings with checkbox children (phases) get per-section counts in the doc's outline.
- [ ] Project Manager card: optional small badge with the top plan's progress (most recently modified `plan-*.md`).
- Meta-note: this makes the implementation of this very roadmap observable inside the app.
- Acceptance: checking a box in the editor updates the count on save (Notes panel already refreshes via `FileMonitorService`).

Not planned: a pre-session "checkpoint/rollback" tool — the user's branch-per-feature + commit-per-step habit plus 8.3's session-range view covers rollback (`git revert`/`reset` of the session's commit range) without new machinery.

---

## Settings added by this roadmap

| Key | Default | Phase | Description |
|-----|---------|-------|-------------|
| `git.default_branch` | `"main"` | 4.6 | `--initial-branch` for New Project |
| `git.remember_credentials` | `false` | 3.7 | Pre-check state of the auth dialog checkbox |
| `worktree.provision_command` | `""` (auto: uv) | 6.2 | Shell command run in a fresh worktree; empty = auto-detect uv projects |
| `worktree.copy_untracked` | `".env"` | 6.2 | Comma-separated untracked files to copy into new worktrees |
| `mcp.enabled` | `true` | 7.2 | Host the local MCP server and register it with the embedded Claude session |

## Suggested version mapping

**Active track (re-prioritized 2026-07-06):**
- v0.8.1 — **Phase 1** (data safety)
- v0.8.2 — **Phase 2** (async layer) + **3.4** (deterministic git env) as an add-on
- v0.8.3 / v0.9 — **Phase 7** (MCP control surface: read tools 7.3 + mutating tools 7.4; **excluding 7.6** worktree tools)

**Deferred (record only, not scheduled):**
- Phase 3 (git robustness) — non-merge items (3.2 error surfacing, 3.3 restore_file, 3.5 push/pull dialogs, 3.7 credentials, 3.8 diff/status parsing, 3.10 monitor gaps) may be cherry-picked opportunistically; merge-specific items (3.1, 3.6, 3.9) are tied to the deferred merge UI.
- Phase 4 (git features) — including 4.4 (merge/conflict view), deferred with Phase 6.
- Phase 5 (reviewer editor); Phase 8 (agent observability); Phase 8.6 (plan progress) any time.
- Phase 6 (worktrees & parallel agents) — deferred to a later horizon; re-evaluate after the MCP track lands.

The pre-existing v0.8 refactor plan (`docs/plan-code-companion-refactor.md`, HistoryService adapters) remains orthogonal.

The pre-existing v0.8 refactor plan (`docs/plan-code-companion-refactor.md`, HistoryService adapters) is orthogonal; if done first, Phase 5.1/6.3 history changes should target the adapter interface instead of `HistoryService` directly.
