#!/usr/bin/env bash
# Build Code Companion .rpm and .deb packages using throwaway podman containers.
# The host stays clean: fpm/ruby/rpmbuild live only inside the builder image.
#
# Binary wheels (pydantic_core) are Python-ABI-specific, so the PyPI-only deps are
# vendored SEPARATELY against each target distro's own python:
#   .rpm  <- vendored in fedora:latest   .deb  <- vendored in ubuntu:24.04
# Both trees are then wrapped by fpm in a single Fedora-based builder image.
#
# Usage:  packaging/build.sh
# Output: dist/code-companion-<v>.x86_64.rpm  and  dist/code-companion_<v>_amd64.deb
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="code-companion-builder"
DIST="$REPO/dist"
VENDOR_RPM="$DIST/.vendor-rpm"
VENDOR_DEB="$DIST/.vendor-deb"

# label=disable: don't SELinux-relabel the host repo for these throwaway build containers.
RUN=(podman run --rm --security-opt label=disable)

RAW_VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)"
[ -n "$RAW_VERSION" ] || { echo "ERROR: could not read version from pyproject.toml" >&2; exit 1; }
# rpm/deb forbid '-' in the version field. Map it to '~' so a pre-release
# (e.g. 0.9.0-beta -> 0.9.0~beta) sorts *before* the final 0.9.0 release.
VERSION="$(printf '%s' "$RAW_VERSION" | tr '-' '~')"
echo "==> Building code-companion $RAW_VERSION (package version: $VERSION)"

echo "==> Building builder image ($IMAGE)"
podman build -t "$IMAGE" -f "$REPO/packaging/Containerfile.builder" "$REPO/packaging"

mkdir -p "$DIST" "$VENDOR_RPM" "$VENDOR_DEB"

echo "==> Vendoring PyPI deps for RPM (fedora:latest / cpython)"
"${RUN[@]}" -v "$REPO":/src:ro -v "$VENDOR_RPM":/vendor fedora:latest \
    bash /src/packaging/vendor.sh

echo "==> Vendoring PyPI deps for DEB (ubuntu:24.04 / cpython)"
"${RUN[@]}" -v "$REPO":/src:ro -v "$VENDOR_DEB":/vendor ubuntu:24.04 \
    bash /src/packaging/vendor.sh

echo "==> Packaging (fpm: rpm + deb)"
"${RUN[@]}" \
    -v "$REPO":/src:ro \
    -v "$VENDOR_RPM":/vendor-rpm:ro \
    -v "$VENDOR_DEB":/vendor-deb:ro \
    -v "$DIST":/out \
    -e VERSION="$VERSION" \
    "$IMAGE" bash /src/packaging/_build-in-container.sh

echo "==> Cleaning intermediate vendor trees"
rm -rf "$VENDOR_RPM" "$VENDOR_DEB"

echo "==> Artifacts:"
ls -la "$DIST"/*.rpm "$DIST"/*.deb 2>/dev/null || true
