# Brief: small-screen UI fixes (Claude pane + session history)

## Why (context)

On a 14" laptop the workspace wastes vertical space: the bottom **Claude pane** has a
fixed 220px minimum and can't be hidden, and the **"Review session changes"** section in
the session view has no scroll and eats >half the screen, making the message history
below it unusable. Three focused fixes, all mirroring mechanisms that already exist in the
codebase. Chat/UX in Russian, code + comments in English (project rule 1).

**Delegated from the main project (worktree delegation dogfood).** Follow the two
human-gates in your worktree system prompt: confirm intake with the human before starting,
and confirm with the human before `report_worktree_complete`. Ask the parent via
`reply_message` if anything here is ambiguous — don't re-ask the human to re-explain.

Shared layout background (read first): `_build_content()` in `src/project_window.py`
(~lines 980-1024) builds a vertical `Gtk.Paned` `self.content_vpaned` (created ~1007):
- start child = `self.tab_view` (`Adw.TabView`, the top "tabs area");
- end child = the Claude pane from `_build_claude_pane()` (~1026-1031).
- Paned flags (~1008-1011): `shrink_start(True)/resize_start(True)`,
  `shrink_end(False)/resize_end(False)` → tabs area shrinks & absorbs resize; Claude pane
  keeps its min height and never shrinks.
The existing **collapse-tabs-area** mechanism (the pattern to mirror) is at ~1380-1439:
`_on_workspace_toggle`, `_sync_workspace_toggle`, `collapse_workspace`/`expand_workspace`
(they toggle `self.tab_view.set_visible(False/True)`), `toggle_workspace`,
`_on_selected_page_changed` (auto-expands on tab open), `_on_workspace_split_changed`.
State: `self._workspace_collapsed` / `self._workspace_split_position` (init ~52-53);
settings keys `window.workspace_collapsed` / `window.workspace_split_position`.

---

## Task 1 — Auto-expand the Claude pane when the last file-tab is closed

**Goal:** when the user closes the last tab in the top tabs area, the tabs area should
give its space to the Claude pane automatically (so the pane isn't stuck sharing height
with an empty tabs area).

**Where:**
- Tab-close signals wired in `_build_content` (~1000-1003): `"close-page"` →
  `_on_tab_close_requested` (~2143-2200, fires *before* removal — `get_n_pages()` still
  counts the closing page here, so don't detect "last" here); `"page-detached"` →
  `_on_page_detached` (~2125-2130, fires *after* removal — here `tab_view.get_n_pages()==0`
  reliably means the last tab is gone). **Hook Task 1 in `_on_page_detached`.**
- Reuse the existing `collapse_workspace()` (~1398-1408): it already hides `tab_view` and
  lets the Claude pane fill the height — exactly the desired end state. There is already a
  precedent for auto-expanding on tab open (`_on_selected_page_changed` ~1427-1430), so the
  inverse (auto-collapse-tabs-area / expand-Claude on last-tab-close) is symmetric.

**Acceptance:** open a few tabs, close them one by one; when the last one closes, the tabs
area collapses and the Claude pane takes the full height. Opening a new tab (existing
`_on_selected_page_changed`) restores the split. No regression to manual collapse toggle.

---

## Task 2 — Scroll for the "Review session changes" section

**Goal:** the session "changes" section must not grow unbounded; cap its height and make it
scroll, so the message history below stays usable on a 14" screen.

**Where:** `src/widgets/session_view.py`, class `SessionView` (`Gtk.Box`):
- `_build_ui` (~70-89): `SessionView` appends two direct children — `self.changes_container`
  (plain `Gtk.Box(VERTICAL)`, ~74-76, **NOT** in a `ScrolledWindow`, no height constraint)
  and `self._scrolled` (the message list scroller, `vexpand=True`, ~79-89).
- `_render_changes` (~137-190) fills `changes_container`: a "Changes this session" header +
  the **"Review session changes"** button (~161), a "Files touched (N)" `Gtk.Expander`
  (~167-177, **unbounded** file labels), a "Commits (N)" `Gtk.Expander` (~179-187, expanded
  by default, unbounded). These expanders are what grow tall.

**Approach (pick the cleanest):** wrap the changes section (or the expanders' content) in a
`Gtk.ScrolledWindow` with `hscrollbar-policy=NEVER`, `vscrollbar-policy=AUTOMATIC`,
`propagate-natural-height=True` and a `max-content-height` (e.g. ~200-240px) so it scrolls
past that. Keep the message-list scroller below fully functional. Follow the existing
ScrolledWindow setup at `session_view.py` ~79-81 for policy conventions.

**Acceptance:** with a session that touched many files / many commits, the changes section
never exceeds ~a third of the view; it scrolls internally; the message history below is
scrollable and visible. Small sessions still look fine (no empty scroll gap — natural
height).

---

## Task 3 — Fully hide the Claude pane (toggle, like collapse-tabs-area)

**Goal:** a toggle that hides the Claude pane completely (not just shrinks it), reclaiming
its 220px minimum on small screens. Mechanism analogous to collapse-tabs-area.

**Where:**
- `_build_claude_pane` (~1026-1031): `self.claude_container = Gtk.Box(VERTICAL)` with
  `set_size_request(-1, 220)` (~1029) — the fixed minimum. Paned end-child flags
  (`shrink_end(False)`, `resize_end(False)`, ~1009/1011) keep it from shrinking.
- There is currently **no** hide/collapse toggle for the Claude pane (only the tabs-area
  one). Activity bar / header built in `_build_activity_bar` (~311-354), packed at ~250.

**Approach:** mirror the workspace-collapse block (~1380-1439):
- add state `self._claude_collapsed` (init near ~52) + setting `window.claude_collapsed`;
- `collapse_claude_pane()` / `expand_claude_pane()` toggling
  `self.claude_container.set_visible(False/True)` (same pattern `collapse_workspace` uses on
  `tab_view`); when hiding, lift the min height (`set_size_request(-1, -1)` or 0) and restore
  it on show;
- a toggle button — either in the header activity bar (~311-354) or as a paned/tab-bar
  action widget analogous to `workspace_toggle_btn` (~990-995,
  `set_end_action_widget`) — with a clear icon + tooltip;
- persist/restore via `window.claude_collapsed` following the read-in-`_build_content` /
  write-in-toggle pattern (~1018/1021 read, ~1408/1418 write).

**Interaction with Task 1:** decide the sane combined behaviour — e.g. hiding the Claude
pane and collapsing the tabs area shouldn't leave an empty window; when the pane is fully
hidden, the tabs area should take all height. Keep the two toggles independent but coherent.

**Acceptance:** a toggle fully hides/reveals the Claude pane; when hidden it occupies zero
height (no 220px band); state persists across restart; works together with the tabs-area
collapse without an empty/blank layout.

---

## Delivery checklist (per the worktree protocol)
1. Implement all three; keep diffs focused and mirror existing patterns/naming.
2. Run the app and confirm each fix on a small window height; run `/code-review`.
3. Commit on this branch (`feature/ui-small-screen-fixes`).
4. **Ask the human to confirm**, then call `report_worktree_complete` with a summary,
   the review findings, and the test/verify status. Do not merge from the worktree.
