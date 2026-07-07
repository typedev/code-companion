#!/usr/bin/env bash
# Spike v2: prove BOTH channels — grim screenshots (vision) AND AT-SPI actions
# (semantic control) — inside a headless compositor we own.
#
# dbus-run-session gives us a private session bus so the AT-SPI accessibility
# bus (at-spi-bus-launcher) can come up; cage hosts the app; the wrapper drives
# it. Nothing touches the host GNOME session.
set -u

HERE="$(dirname "$(readlink -f "$0")")"
rm -f "$HERE/shot_before.png" "$HERE/shot_after.png"
chmod +x "$HERE/inside_cage2.sh"

export WLR_BACKENDS=headless
export WLR_LIBINPUT_NO_DEVICES=1
export WLR_HEADLESS_OUTPUTS=1
# Avoid clashing with the host compositor's wayland-0 socket.
export WAYLAND_DISPLAY="wayland-ccspike-$$"

echo "Launching dbus-run-session -> cage (headless) -> app + AT-SPI driver ..."
dbus-run-session -- cage -- "$HERE/inside_cage2.sh"
RC=$?

echo "---- result rc=$RC ----"
for f in shot_before.png shot_after.png; do
    [ -f "$HERE/$f" ] && { echo "OK: $f"; file "$HERE/$f"; } || echo "MISSING: $f"
done
