#!/usr/bin/env bash
# Publish a Code Companion GitHub release: build the packages, tag, and upload.
#
# Version is read from pyproject.toml. Requires: podman (build) + gh (authenticated).
#
# Usage:
#   packaging/release.sh            # build, tag v<version>, create the GitHub release
#   packaging/release.sh --dry-run  # build + show what would happen, no tag/push/release
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
[ -n "$VERSION" ] || { echo "ERROR: no version in pyproject.toml" >&2; exit 1; }
TAG="v$VERSION"
# Package files use '~' instead of '-' (see build.sh); glob on that form.
PKG_VERSION="$(printf '%s' "$VERSION" | tr '-' '~')"
# Anything with a '-' suffix or a letter (beta/rc/alpha) is a GitHub pre-release.
PRERELEASE=""
case "$VERSION" in *-*|*[a-zA-Z]*) PRERELEASE="--prerelease" ;; esac
echo "==> Releasing code-companion $TAG ${PRERELEASE:+(pre-release)}"

# --- Preflight ---
command -v gh >/dev/null || { echo "ERROR: gh (GitHub CLI) not found" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh not authenticated (run: gh auth login)" >&2; exit 1; }

if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: working tree is dirty — commit or stash before releasing." >&2
    git status --short >&2
    exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "ERROR: tag $TAG already exists. Bump the version in pyproject.toml first." >&2
    exit 1
fi
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "ERROR: GitHub release $TAG already exists." >&2
    exit 1
fi

# --- Build the artifacts ---
echo "==> Building packages"
packaging/build.sh
RPM="$(ls dist/code-companion-"$PKG_VERSION"*.rpm 2>/dev/null | head -1)"
DEB="$(ls dist/code-companion_"$PKG_VERSION"*.deb 2>/dev/null | head -1)"
[ -n "$RPM" ] && [ -n "$DEB" ] || { echo "ERROR: expected .rpm and .deb for $PKG_VERSION in dist/" >&2; exit 1; }
echo "    $RPM"
echo "    $DEB"

# --- Release notes ---
NOTES="$(cat <<EOF
## Code Companion $TAG

Native packages for a one-command install (dependencies resolved from official repos):

\`\`\`bash
sudo dnf install ./$(basename "$RPM")      # Fedora
sudo apt  install ./$(basename "$DEB")      # Ubuntu/Debian
\`\`\`

> Also required (not in distro repos): the \`claude\` CLI (npm) and \`uv\`.
> See \`packaging/README.md\` for build/test details and supported distro versions.
EOF
)"

if [ "$DRY_RUN" = "1" ]; then
    echo "==> [dry-run] would: git tag $TAG && git push origin $TAG"
    echo "==> [dry-run] would: gh release create $TAG <assets> with notes:"
    echo "-----"; echo "$NOTES"; echo "-----"
    exit 0
fi

# --- Tag, push, publish ---
echo "==> Tagging $TAG"
git tag -a "$TAG" -m "Code Companion $TAG"
git push origin "$TAG"

echo "==> Creating GitHub release"
gh release create "$TAG" "$RPM" "$DEB" $PRERELEASE \
    --title "Code Companion $TAG" \
    --notes "$NOTES"

echo "==> Done: $(gh release view "$TAG" --json url -q .url)"
