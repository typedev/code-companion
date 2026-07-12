#!/usr/bin/env bash
# Runs INSIDE the fpm builder container (see Containerfile.builder).
# Wraps the app + a per-format, ABI-matched vendor tree into .rpm and .deb.
#
# Mounts expected:
#   /src         -> repo checkout (read-only)
#   /vendor-rpm  -> vendored PyPI deps built with Fedora's python (read-only)
#   /vendor-deb  -> vendored PyPI deps built with Ubuntu's python (read-only)
#   /out         -> host dist/ dir (read-write)
# Env:
#   VERSION      -> package version (from pyproject.toml)
set -euo pipefail

VERSION="${VERSION:?VERSION not set}"

# Lay out /usr tree for one format into $1, using vendor tree $2.
stage() {
    local root="$1" vendor="$2"
    local app="$root/usr/lib/code-companion"
    rm -rf "$root"
    mkdir -p "$app" \
             "$root/usr/bin" \
             "$root/usr/share/applications" \
             "$root/usr/share/icons/hicolor/scalable/apps"

    cp /src/main.py "$app/"
    cp -r /src/src "$app/"
    find "$app/src" -type d -name __pycache__ -prune -exec rm -rf {} +
    find "$app/src" -type f -name '*.pyc' -delete

    cp -r "$vendor" "$app/vendor"

    install -m 0755 /src/packaging/launcher.sh "$root/usr/bin/code-companion"
    install -m 0644 /src/data/dev.typedev.CodeCompanion.desktop \
        "$root/usr/share/applications/dev.typedev.CodeCompanion.desktop"
    install -m 0644 /src/src/resources/icons/app.svg \
        "$root/usr/share/icons/hicolor/scalable/apps/dev.typedev.CodeCompanion.svg"
}

COMMON=(
    -s dir
    -n code-companion
    -v "$VERSION"
    -a native
    --license "Apache-2.0"
    --maintainer "Alexander Lubovenko <lubovenko@gmail.com>"
    --url "https://github.com/typedev/code-companion"
    --description "GTK4/libadwaita desktop companion for AI coding assistants"
    --after-install /src/packaging/after-install.sh
    --after-remove /src/packaging/after-install.sh
    --force
)

mkdir -p /out

# --- RPM (Fedora/RHEL names) ---
echo ">> Building .rpm"
stage /tmp/stage-rpm /vendor-rpm
fpm "${COMMON[@]}" -t rpm -p /out -C /tmp/stage-rpm \
    -d python3 \
    -d python3-gobject \
    -d python3-cairo \
    -d python3-pygit2 \
    -d gtk4 \
    -d libadwaita \
    -d gtksourceview5 \
    -d vte291-gtk4 \
    -d webkitgtk6.0 \
    -d libspelling \
    -d libsecret \
    -d git \
    -d ripgrep \
    --rpm-tag "Recommends: tmux" \
    --rpm-tag "Recommends: ShellCheck" \
    --rpm-tag "Recommends: yamllint" \
    usr

# --- DEB (Debian/Ubuntu names) ---
# Depend on the gir1.2-* typelib packages: each pulls its runtime .so library, so we
# avoid guessing soname-versioned lib package names (e.g. libspelling-1-1 vs -2).
echo ">> Building .deb"
stage /tmp/stage-deb /vendor-deb
fpm "${COMMON[@]}" -t deb -p /out -C /tmp/stage-deb \
    -d python3 \
    -d python3-gi \
    -d python3-gi-cairo \
    -d python3-pygit2 \
    -d gir1.2-gtk-4.0 \
    -d gir1.2-adw-1 \
    -d gir1.2-gtksource-5 \
    -d gir1.2-vte-3.91 \
    -d gir1.2-webkit-6.0 \
    -d gir1.2-spelling-1 \
    -d gir1.2-secret-1 \
    -d git \
    -d ripgrep \
    --deb-recommends tmux \
    --deb-recommends shellcheck \
    --deb-recommends yamllint \
    usr

echo ">> Done. Artifacts in /out:"
ls -la /out/*.rpm /out/*.deb
