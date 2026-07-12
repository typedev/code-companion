#!/usr/bin/env bash
# Vendor the PyPI-only deps for THIS container's Python ABI.
# Runs inside a *target* distro image (fedora:latest for the .rpm, ubuntu:24.04 for the .deb)
# so the compiled wheels (e.g. pydantic_core) match the interpreter that will run the app.
#
# Mount /vendor (rw) as the output dir. Keep the dep set in sync with pyproject.toml.
set -euo pipefail

DEST=/vendor
mkdir -p "$DEST"
rm -rf "${DEST:?}"/* 2>/dev/null || true

# Bootstrap python3 + pip (bare distro images don't ship them).
if command -v dnf >/dev/null 2>&1; then
    dnf install -y --setopt=install_weak_deps=False python3 python3-pip >/dev/null
elif command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null
    apt-get install -y python3 python3-pip >/dev/null
fi

PIP_FLAGS="--quiet --no-compile --target $DEST"
# Debian/Ubuntu mark the interpreter externally-managed (PEP 668); installing into our
# own target dir is safe, so opt out of the guard.
[ -f /etc/debian_version ] && PIP_FLAGS="$PIP_FLAGS --break-system-packages"

echo ">> Vendoring (python $(python3 -V 2>&1)) into $DEST"
python3 -m pip install $PIP_FLAGS \
    "mcp>=1.28,<2" "mistune>=3.1.4" "pathspec>=0.12.0"

# Trim what the app never needs at runtime.
find "$DEST" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$DEST/bin"

echo ">> Vendored top-level packages:"
ls "$DEST" | grep -viE 'dist-info|__pycache__' | sort | tr '\n' ' '; echo
