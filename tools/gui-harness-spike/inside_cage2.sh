#!/usr/bin/env bash
# Runs as cage's single client, INSIDE a dbus session (see run_spike2.sh).
# Sequence: launch app -> screenshot BEFORE -> drive via AT-SPI -> screenshot
# AFTER. The two screenshots + the driver's tree dump prove both channels.
set -u

HERE="$(dirname "$(readlink -f "$0")")"
BEFORE="$HERE/shot_before.png"
AFTER="$HERE/shot_after.png"

export GDK_BACKEND=wayland
# NOTE: do NOT force GTK_A11Y=atspi — GTK 4.22 rejects the explicit value; the
# atspi backend is the default and auto-selects once org.a11y.Bus is reachable.
unset GTK_A11Y

# The private dbus-run-session bus can't systemd-activate org.a11y.Bus, so we
# launch the AT-SPI accessibility bus ourselves. --launch-immediately registers
# org.a11y.Bus on our session bus directly, no D-Bus activation needed.
/usr/libexec/at-spi-bus-launcher --launch-immediately --a11y=1 &
A11Y_PID=$!
sleep 1.0
# The registry daemon maintains the desktop/app tree. Its D-Bus service file
# uses --use-gnome-session, which hangs without gnome-session; launch it bare.
/usr/libexec/at-spi2-registryd &
REG_PID=$!
sleep 1.0

python3 "$HERE/demo_app.py" &
APP_PID=$!

sleep 2.5
grim "$BEFORE"; echo "grim before -> $BEFORE (rc=$?)"

echo "=== running AT-SPI driver ==="
python3 "$HERE/atspi_driver.py"
DRIVER_RC=$?
echo "=== AT-SPI driver rc=$DRIVER_RC ==="

sleep 1.0
grim "$AFTER"; echo "grim after -> $AFTER (rc=$?)"

kill "$APP_PID" 2>/dev/null
kill "$REG_PID" 2>/dev/null
kill "$A11Y_PID" 2>/dev/null
exit "$DRIVER_RC"
