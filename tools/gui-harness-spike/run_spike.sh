#!/usr/bin/env bash
# Spike: prove "launch a GTK4 app in a headless compositor we own -> screenshot it".
# No touching the host GNOME session, so no GNOME 50 screenshot lockdown applies.
set -u

HERE="$(dirname "$(readlink -f "$0")")"
OUT="$HERE/shot.png"
rm -f "$OUT"

chmod +x "$HERE/inside_cage.sh"

# Headless wlroots backend with one virtual output; no real input devices needed.
export WLR_BACKENDS=headless
export WLR_LIBINPUT_NO_DEVICES=1
# A fixed virtual output size so the screenshot is deterministic.
export WLR_HEADLESS_OUTPUTS=1

echo "Launching cage (headless) -> demo_app -> grim ..."
# cage runs our wrapper as its only client and exits when the wrapper exits.
cage -- "$HERE/inside_cage.sh" "$OUT"
CAGE_RC=$?

echo "cage exit=$CAGE_RC"
if [ -f "$OUT" ]; then
    echo "OK: screenshot at $OUT"
    file "$OUT"
else
    echo "FAIL: no screenshot produced"
fi
