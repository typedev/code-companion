# Touchpad scroll in the terminal: taming VTE's unit-blind scrolling

**Status: shipped (Phase 1, Claude pane).** Validated live — touchpad scrolling is tame, the
mouse wheel is unchanged. Default `terminal.touchpad_pixels_per_click = 25` confirmed by use
(cell height here is 18 px, so a step ≈ 1.4 lines of finger travel). Phase 2 (shell tabs)
remains open; see the end of this doc.

## Context

Touchpad scrolling over any VTE terminal in the app is wildly too fast — a millimetre of
finger travel flies off screen. The mouse wheel is fine. A system-level GNOME extension slows
the app's *GUI* scrolling (file tree, editor) but leaves the terminal untouched.

**Root cause (measured, then confirmed in VTE's source).** GDK4 tags every scroll event with a
unit: `WHEEL` (delta = wheel notches, `dy == 1.0` per notch) or `SURFACE` (delta = *pixels*).
VTE never reads it. `src/vte.cc:10298` (master, identical in 0.84):

```cpp
m_mouse_smooth_scroll_y_delta += event.dy();
if (m_mouse_tracking_mode != MouseTrackingMode::eNONE) {
        cnt_y = m_mouse_smooth_scroll_y_delta;   // double -> gint
        ...
        for (i = 0; i < cnt_y; i++)              // one wheel click per unit of dy
                feed_mouse_event(...);
```

`grep -c get_unit src/widget.cc src/vte.cc` → **0, 0** on both 0.84.0 and master. The unit is
discarded at the boundary: `vte::platform::ScrollEvent` (`src/widget.hh:222`) carries only
`{modifiers, dx, dy}`. So the touchpad gets **one wheel click per pixel of finger travel**.

Measured on this machine (probe: GDK-side controller + a PTY child parsing the SGR sequences
VTE actually delivers):

| Gesture | Device | GDK events | finger travel | clicks delivered to the app |
|---|---|---|---|---|
| mouse | `WHEEL` | 5 | 5 notches | ~5 |
| touchpad | `SURFACE` | 354 | 1212 px | **1519** |
| touchpad | `SURFACE` | 177 | 789 px | **934** |

~250x. Worse, VTE converts the *total path*, not net displacement: gesture #1 moved 1212 px of
path but only −151 px net, so a finger wiggle emits hundreds of clicks in **both** directions
(1585 up / 1440 down over one session). That is the "flies off to nowhere" feel — the app is fed
a sawtooth.

**Upstream is a dead end.** [vte#2720](https://gitlab.gnome.org/GNOME/vte/-/issues/2720)
"GTK4 - Excessive scroll speed with application scroll events" — open since 2024-01-06, still
open (last activity 2026-06-25). Duplicates: vte#2744, console#342 (both closed into it);
vte#2857 open. A patch exists in the thread; maintainer `chpe` is blocked on choosing the
divisor — "I don't like arbitrary values … libshumate divides by 50, papers by 20, and gtk
itself multiplies by 2.5". **There is no version to upgrade to and no property to set.**
`scroll-unit-is-pixels` is a trap: it only scales the `GtkAdjustment` (all four call sites in
`widget.cc` touch the adjustment only) and has zero effect on events sent to the child — Ptyxis
set it in two `.ui` files and it did not help them.

**Why a slider, not a constant.** Upstream is stuck precisely because no single divisor is right
for every touchpad and every user. We do not have to pick one for the world — only expose the
knob. This sidesteps what has blocked the fix for two and a half years.

## All three VTE branches are unit-blind

Not just the Claude pane. `widget_mouse_scroll` picks one of three paths from internal state:

| # | Condition | What VTE does | Effect with pixel deltas |
|---|---|---|---|
| 1 | `m_mouse_tracking_mode != eNONE` | one button-4/5 escape per unit of `dy` | 1 click / px |
| 2 | alt-screen + `XTERM_ALTBUF_SCROLL` | fakes `v * dy` cursor Up/Down keys | ~3 arrows / px |
| 3 | else, `m_fallback_scrolling` | `dcnt = scroll_delta + v * dy`, moves the adjustment | ~3 lines / px |

where `v = MAX(1., ceil(m_row_count / 10.))` ≈ 3. So plain shell tabs (`less`, `vim`, bare bash
history scroll) are hit too — arguably harder, because of the extra `v` multiplier.

**The crux:** `m_mouse_tracking_mode`, the current screen and `XTERM_ALTBUF_SCROLL` are private
VTE state. If we claim an event we must decide *for ourselves* which branch VTE would have taken,
and reproduce it. We cannot ask.

## What is already proven (don't re-litigate)

Measured with the probe, not assumed:

1. **A CAPTURE-phase `GtkEventControllerScroll` returning `True` does preempt VTE.** 1654 px of
   claimed touchpad movement delivered **0** clicks to the app. This is the whole foundation.
2. **Claiming is selective.** With touchpad claiming on, the mouse wheel still worked normally
   (we only ever claim `unit == SURFACE`). Mouse must never enter this code path.
3. **Synthetic events are impossible.** `Gdk.ScrollEvent` has no public constructor in GTK4
   (`Gdk.Display.put_event` exists but there is nothing to hand it). We cannot "rescale and
   re-emit" — we must produce the effect ourselves.
4. **Dropping events cannot work.** Any event we let through hands VTE its *full* `dy`, and
   per-event `dy` ranges 0.121 … 22.961. Passing 1-in-N yields jumps of 1 to 23 lines at random —
   chunky and unpredictable, exactly what we're trying to fix.
5. `Vte.Terminal.get_vadjustment()` exists → branch 3 we can drive exactly.

## Design

Claim every `SURFACE` event, accumulate pixels ourselves, and emit **one scroll step per
`terminal.touchpad_pixels_per_click` pixels**, carrying the fractional remainder across events
(same trick VTE uses, just with the right unit). `WHEEL` events return `False` untouched — the
mouse keeps its current behaviour exactly.

### Scope: Claude pane first

Branch 1 is the only one we can identify with certainty, and it happens to be where the pain is:

- **Claude pane / worktree / remote session** (`TerminalView(argv=tmux_argv)`): the child is
  always `tmux` loaded with our own `src/resources/tmux/tmux-managed.conf`, which sets
  `set -g mouse on`. tmux therefore keeps mouse reporting enabled toward VTE unconditionally →
  `m_mouse_tracking_mode != eNONE` is **guaranteed**, not guessed. Emit mouse clicks via
  `feed_child()`.
- **Shell tabs** (`TerminalView()` with no `argv` — Terminal button, task/script runners,
  `window.py:108`): mode is whatever the running program decides and changes at runtime
  (`htop` turns tracking on mid-session). Deferred to Phase 2.

This keeps Phase 1 free of heuristics. `TerminalView` already distinguishes the two modes:
`self._argv is not None` (used for `_respawn_on_exit`).

### Emitting a click in branch 1

`feed_child()` writes to the PTY as if the terminal had sent it — the same seam already used at
`terminal_view.py:294/363/388/392`. The sequence must match the encoding the child negotiated.
The probe confirmed VTE emits **SGR** (`\e[<64;COL;ROWM` / `65` for down) for a child that
enabled 1006, and tmux does enable 1006. Open risk: this is negotiated state we are again
assuming. **Checkpoint 2 must verify it empirically before we build on it.**

Coordinates: SGR carries cell col/row. VTE reports the pointer's cell. We must compute it from
the pointer position and `get_char_width()`/`get_char_height()`. tmux with a single full-pane
window is unlikely to care about wheel coordinates, but the panes-aware path does — verify.

### Setting

| Key | Default | Range | Meaning |
|---|---|---|---|
| `terminal.touchpad_pixels_per_click` | `25` | 1–100 | pixels of finger travel per one scroll step; `1` reproduces today's behaviour |

25 ≈ one line height, and lands the measured 1212 px gesture at ~48 clicks instead of 1519.
`Adw.SpinRow` or `Adw.ActionRow` + `Gtk.Scale` on the Preferences → Appearance/Editor page,
live-applied via the existing `SettingsService` `changed` signal (no restart), per
`CLAUDE.md`'s settings pattern.

Scale reference from the measured gesture (1212 px path):

| px/click | clicks | feel |
|---|---|---|
| 1 (today) | ~1212 | flies away |
| 10 | ~121 | still fast |
| **25** | ~48 | ≈ line height, proposed default |
| 60 | ~20 | slow, precise |

## Checkpoints

- [x] **1. Seam.** `TerminalView._build_ui`: CAPTURE `Gtk.EventControllerScroll` on
      `self.terminal`, mirroring the `key_controller` pattern above it. `WHEEL` → `return False`
      untouched; `SURFACE` → claimed. Gated on `self._argv is not None` so shell tabs are
      unaffected. A `Gtk.EventControllerMotion` tracks the pointer for addressing the reports.
- [x] **2. Encoding verified** *(was blocking)*. `strings $(which tmux)` contains `[?1006h`, so
      tmux enables SGR; `vte.cc feed_mouse_event` prefers SGR when the child enabled 1006.
      Confirmed end-to-end: `_feed_wheel(3)` / `_feed_wheel(-2)` on a real `TerminalView`
      produced `\e[<65;11;1M`×3 and `\e[<64;11;1M`×2, and a child parsing SGR counted all five
      as wheel clicks.
- [x] **3. Accumulator.** `src/utils/scroll_accumulator.py` — signed banking, remainder carried,
      truncation toward zero (as VTE's `double -> gint`). Signed accumulation is what makes a
      wiggling finger cancel instead of sawtooth. Buttons: 65 = down, 64 = up.
- [x] **4. Setting + row.** `terminal.touchpad_pixels_per_click` (default 25, range 1–100) in
      `DEFAULT_SETTINGS`; new Preferences → **Terminal** page with an `Adw.SpinRow` (the dialog's
      established numeric idiom — no `Gtk.Scale` exists in it). Live-applied via the `terminal.`
      branch of `TerminalView._on_setting_changed`, cached (a gesture is ~350 events — never read
      settings per event). The page also gives `terminal.auto_activate_env` its first UI home;
      that key was documented and read but never declared in the defaults.
- [x] **5. Tests.** `tests/test_scroll_accumulator.py`, 9 tests, no display needed.
      373 passing overall (was 364).
- [x] **6. Live check.** Confirmed by Alexander: touchpad tame, mouse unchanged, slider applies
      without restart. Default 25 kept.
- [ ] **7. Report upstream — deferred until a second device is measured.** Every number here comes
      from **one Apple touchpad**. The pixel-delta scale is set by the device and its driver, so
      the 1-click-per-pixel ratio and the useful divisor may well differ elsewhere — and a
      single-device measurement is exactly the kind of evidence that has kept vte#2720 stuck on
      "which constant?" for two and a half years. Re-measure on the HP laptop's touchpad
      (`scroll_probe.py` + `child_counter.py`, kept in this session's scratchpad; rebuild from
      this doc if gone), then decide whether 25 is still a sane default and post the comparison.
      Two devices disagreeing would itself be the strongest argument for the knob. Posting goes
      out under Alexander's account — do **not** post without his approval.

## What shipped

| File | Change |
|---|---|
| `src/utils/scroll_accumulator.py` | new; pure, `gi`-free |
| `tests/test_scroll_accumulator.py` | new; 9 tests |
| `src/widgets/terminal_view.py` | scroll/motion controllers, `_on_scroll`, `_feed_wheel`, `_pointer_cell`, live apply |
| `src/services/settings_service.py` | `terminal` section |
| `src/widgets/preferences_dialog.py` | `_build_terminal_page()` + two handlers |
| `CLAUDE.md` | settings-table row |

## Phase 2 (separate, only if Phase 1 lands well)

Shell tabs. Options, none obviously right yet:

- **Adjustment probe.** Let one event through, watch whether `get_vadjustment().get_value()`
  moved → branch 3 vs branch 1/2. Fragile: at a scroll limit nothing moves either.
- **Assume branch 3, accept breaking `htop`.** Simple, and wrong exactly when someone runs a
  mouse-aware TUI in a shell tab.
- **Do nothing.** Shell tabs are scrolled with the wheel far more often; the pain is in the
  Claude pane.

Decide with measurements, not taste.

## Found en route — not fixed, worth their own tasks

- **Settings-signal leak.** `TerminalView._apply_terminal_settings` connects to the
  `SettingsService` singleton's `changed` and never disconnects; `cleanup()` only kills the PTY.
  Same in `project_window.py`, `query_editor.py`, `file_editor.py`, `svg_editor.py` — none
  disconnect, and `dispatch_panel.py` stores the handler id but never uses it. The singleton
  holds a strong ref, so every `TerminalView` ever created stays alive and keeps reacting to
  settings after its VTE is gone. Pre-existing; this change adds one more branch to that same
  handler but does not make it worse. Fix pattern to copy: `git_changes_panel.py` stores the id
  and disconnects.
- **`SettingsService.reset(None)` is a shallow copy** of `DEFAULT_SETTINGS`, so a reset-all
  aliases the nested dicts and a later `set()` mutates the defaults in place. Pre-existing; the
  new nested `terminal` section is one more victim.

## Out of scope

- `vte#2870` (byobu/kinetic inconsistency) — a different bug in the fallback branch.
- Horizontal scroll (`dx`). VTE zeroes `m_mouse_smooth_scroll_x_delta` outside tracking mode
  anyway; no reported pain.
- Kinetic/momentum scrolling.
