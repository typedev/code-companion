#!/usr/bin/env bash
export GDK_BACKEND=wayland
echo "@@@ outputs before:"
wlr-randr 2>&1 | sed 's/^/@@@   /'
# Grab the headless output name (first line, first token).
OUT=$(wlr-randr 2>/dev/null | awk 'NR==1{print $1}')
echo "@@@ output name = $OUT"
echo "@@@ applying custom-mode 1000x640..."
wlr-randr --output "$OUT" --custom-mode 1000x640 2>&1 | sed 's/^/@@@   /'
sleep 0.5
echo "@@@ outputs after:"
wlr-randr 2>&1 | grep -iE "current|^[A-Za-z]" | sed 's/^/@@@   /'
python3 "$(dirname "$0")/demo_app.py" & sleep 2.0
grim "$(dirname "$0")/shot_sized.png"
echo "@@@ grim rc=$?"
