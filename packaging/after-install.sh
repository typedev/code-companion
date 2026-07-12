#!/bin/sh
# Post-install scriptlet (runs on both .rpm and .deb installs).
# Refresh the desktop and icon caches so the app shows up in the menu with its icon.
# Best-effort: never fail the install if a tool is missing (mirrors install.sh).
set -e

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

exit 0
