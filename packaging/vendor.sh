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

# Drop the packages that carry ABI-locked compiled extensions (pydantic_core, rpds-py,
# cffi) plus the pure-Python parents tightly version-coupled to them (pydantic,
# jsonschema/referencing, cryptography). Vendoring these would freeze the package to a
# single Python minor version (a .cpython-312 .so is invisible to 3.13, etc.). Instead
# the .rpm/.deb depend on the distro builds (python3-pydantic / python3-jsonschema /
# python3-cryptography), which always match the running interpreter. See _build-in-container.sh.
echo ">> Pruning distro-provided (ABI-locked) packages from the vendor tree"
rm -rf "$DEST"/pydantic "$DEST"/pydantic_core \
       "$DEST"/jsonschema "$DEST"/jsonschema_specifications "$DEST"/referencing "$DEST"/rpds \
       "$DEST"/cryptography "$DEST"/cffi "$DEST"/pycparser \
       "$DEST"/_cffi_backend*.so
rm -rf "$DEST"/pydantic-*.dist-info "$DEST"/pydantic_core-*.dist-info \
       "$DEST"/jsonschema-*.dist-info "$DEST"/jsonschema_specifications-*.dist-info \
       "$DEST"/referencing-*.dist-info "$DEST"/rpds_py-*.dist-info \
       "$DEST"/cryptography-*.dist-info "$DEST"/cffi-*.dist-info "$DEST"/pycparser-*.dist-info

# Trim what the app never needs at runtime.
find "$DEST" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$DEST/bin"

# Guard: after pruning, nothing ABI-locked must remain. Any leftover .so would re-lock
# the package to this container's Python minor version and break on other distros.
LEFTOVER_SO="$(find "$DEST" -name '*.so' -not -name '*.abi3.so' -printf '%f\n')"
if [ -n "$LEFTOVER_SO" ]; then
    echo "ERROR: ABI-locked .so left in vendor tree (add its package to the prune list):" >&2
    echo "$LEFTOVER_SO" >&2
    exit 1
fi

echo ">> Vendored top-level packages:"
ls "$DEST" | grep -viE 'dist-info|__pycache__' | sort | tr '\n' ' '; echo
