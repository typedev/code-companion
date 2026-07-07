#!/usr/bin/env bash
# Runs as the single client of the headless cage compositor.
# cage exports WAYLAND_DISPLAY for us, so both the GTK app and grim below
# connect to the SAME compositor. We launch the app, give it time to render,
# capture with grim, then exit (which tears the compositor down).
set -u

HERE="$(dirname "$(readlink -f "$0")")"
OUT="${1:-$HERE/shot.png}"

# Force the app onto the Wayland backend of our cage instance.
export GDK_BACKEND=wayland
# Turn on the AT-SPI accessibility bridge so the tree is inspectable later.
export GTK_A11Y=atspi

python3 "$HERE/demo_app.py" &
APP_PID=$!

# Give GTK time to map + render the first frame.
sleep 2.5

grim "$OUT"
GRIM_RC=$?
echo "grim exit=$GRIM_RC -> $OUT"

kill "$APP_PID" 2>/dev/null
exit $GRIM_RC
