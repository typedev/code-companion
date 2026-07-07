# Native GUI test harness — proof-of-concept spike

Validated proof (2026-07-07, Fedora 44 / GNOME 50 Wayland) that a native
GTK4/libadwaita app can be **launched, driven semantically, and screenshotted
headlessly** — the basis for the "Native GUI test harness" feature in
[`docs/plan-mcp-integration.md`](../../docs/plan-mcp-integration.md) (Part B).

These are throwaway spike scripts kept for reference so the working recipe isn't
lost. They are **not** wired into the app; the real implementation lives in the
plan (Part B, decisions D2/D3).

## Why this approach

Wayland isolates clients — no app may screenshot or inject input into another's
window, and GNOME 50 locks its host-session screenshot APIs down hard. The only
robust, distro-portable path is to run the app-under-test **inside a headless
wlroots compositor we own** (`cage`), where those restrictions don't apply to us.

## What each file proves

| File | Proves |
|------|--------|
| `demo_app.py` | Target-under-test: a small GTK4/libadwaita window (label, entry, button) |
| `run_spike.sh` + `inside_cage.sh` | **Channel 1 (vision)**: launch in headless cage → `grim` screenshot |
| `run_spike2.sh` + `inside_cage2.sh` + `atspi_driver.py` | **Channel 2 (semantic)**: read the AT-SPI tree, invoke the button via `do_action` and fill the entry via `set_text_contents` (no synthetic clicks), then re-screenshot to confirm the UI changed |
| `sizetest.sh` | **Output sizing (D3)**: `wlr-randr --output HEADLESS-1 --custom-mode <WxH>` resizes the headless output at runtime |

## Run

```bash
# Requires: cage grim wlr-randr (+ at-spi2-core, already present with GTK).
# See INSTALL.md "Optional: native GUI test harness".
./run_spike2.sh        # writes shot_before.png / shot_after.png next to the scripts
```

## The hard-won gotchas (encoded in these scripts)

1. `dbus-run-session` → private session bus, isolates from host GNOME.
2. `cage` with `WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1
   WLR_HEADLESS_OUTPUTS=1` and a unique `WAYLAND_DISPLAY` (avoid clashing with the
   host's `wayland-0`).
3. Launch `at-spi-bus-launcher --launch-immediately --a11y=1` **manually** — D-Bus
   autostart of `org.a11y.Bus` fails under the private bus (service uses
   `SystemdService=`).
4. Launch `at-spi2-registryd` **bare** — its .service uses `--use-gnome-session`,
   which hangs headless; without it `Atspi.get_desktop(0)` returns -1 children.
5. Do **not** set `GTK_A11Y=atspi` — GTK 4.22 rejects the explicit value; atspi is
   the default once the a11y bus is up.
6. Identify the app by CONTENTS (it registers as the process name, e.g. `python3`);
   role is `button` not `push button`; use the GI idiom
   `Atspi.Action.do_action(node, i)`, not pyatspi `node.queryAction()`.
7. cage kiosk-fullscreens its client → window size = output size; size it with
   `wlr-randr --custom-mode`.
8. `ydotool` (uinput) is the coordinate fallback for canvas/custom-drawn widgets and
   GTK4's missing Table/TableCell/Image AT-SPI interfaces (not exercised here).
