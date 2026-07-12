#!/usr/bin/env bash
# Clean-install test for the built packages, in throwaway containers.
# Verifies (per distro): dependency resolution, files landed, --help runs, the full
# GObject + vendored import graph loads, and clean removal.
#
# Usage:  packaging/test-install.sh            # test both
#         packaging/test-install.sh fedora     # or a single distro
#         packaging/test-install.sh ubuntu
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$REPO/dist"
SMOKE="$REPO/packaging/import_smoke.py"

test_fedora() {
    local rpm base
    rpm="$(ls "$DIST"/code-companion-*.rpm 2>/dev/null | head -1)"
    [ -n "$rpm" ] || { echo "No .rpm in dist/ — run build.sh first" >&2; return 1; }
    base="$(basename "$rpm")"
    echo "############ FEDORA (fedora:latest) — $base"
    podman run --rm --security-opt label=disable \
        -v "$DIST":/dist:ro \
        -v "$SMOKE":/import_smoke.py:ro \
        fedora:latest bash -euxc "
            dnf install -y /dist/$base
            test -x /usr/bin/code-companion
            test -f /usr/lib/code-companion/main.py
            test -d /usr/lib/code-companion/src
            test -d /usr/lib/code-companion/vendor
            test -f /usr/share/applications/dev.typedev.CodeCompanion.desktop
            test -f /usr/share/icons/hicolor/scalable/apps/dev.typedev.CodeCompanion.svg
            code-companion --help
            PYTHONPATH=/usr/lib/code-companion:/usr/lib/code-companion/vendor python3 /import_smoke.py
            dnf remove -y code-companion
            echo FEDORA-OK
        "
}

test_ubuntu() {
    local deb base
    deb="$(ls "$DIST"/code-companion_*.deb 2>/dev/null | head -1)"
    [ -n "$deb" ] || { echo "No .deb in dist/ — run build.sh first" >&2; return 1; }
    base="$(basename "$deb")"
    echo "############ UBUNTU (ubuntu:24.04) — $base"
    podman run --rm --security-opt label=disable \
        -v "$DIST":/dist:ro \
        -v "$SMOKE":/import_smoke.py:ro \
        ubuntu:24.04 bash -euxc "
            export DEBIAN_FRONTEND=noninteractive
            apt-get update
            apt-get install -y /dist/$base
            test -x /usr/bin/code-companion
            test -d /usr/lib/code-companion/vendor
            test -f /usr/share/applications/dev.typedev.CodeCompanion.desktop
            code-companion --help
            PYTHONPATH=/usr/lib/code-companion:/usr/lib/code-companion/vendor python3 /import_smoke.py
            apt-get remove -y code-companion
            echo UBUNTU-OK
        "
}

case "${1:-both}" in
    fedora) test_fedora ;;
    ubuntu) test_ubuntu ;;
    both)   test_fedora; test_ubuntu ;;
    *) echo "Usage: $0 [fedora|ubuntu|both]" >&2; exit 1 ;;
esac
