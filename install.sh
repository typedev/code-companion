#!/bin/bash
# Code Companion installation script
# Run: ./install.sh [install|update|uninstall]

set -e

APP_NAME="code-companion"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="/usr/local/bin"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$HOME/.local/share/applications"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

install_system_deps() {
    info "Checking system dependencies..."

    # Detect package manager and install dependencies
    if command -v dnf &> /dev/null; then
        # Fedora/RHEL
        PACKAGES="cairo-devel cairo-gobject-devel gobject-introspection-devel gtk4-devel libadwaita-devel gtksourceview5-devel vte291-gtk4-devel webkitgtk6.0-devel libgit2-devel libspelling-devel python3-devel meson ninja-build ripgrep fd-find"

        # Check if packages are installed
        MISSING=""
        for pkg in $PACKAGES; do
            if ! rpm -q "$pkg" &> /dev/null; then
                MISSING="$MISSING $pkg"
            fi
        done

        if [ -n "$MISSING" ]; then
            info "Installing missing system packages:$MISSING"
            sudo dnf install -y $MISSING || error "Failed to install system dependencies"
        else
            info "All system dependencies are already installed"
        fi

    elif command -v apt-get &> /dev/null; then
        # Debian/Ubuntu
        PACKAGES="libcairo2-dev libgirepository-2.0-dev libgtk-4-dev libadwaita-1-dev libgtksourceview-5-dev libvte-2.91-gtk4-dev libwebkitgtk-6.0-dev libgit2-dev libspelling-1-dev gir1.2-spelling-1 python3-dev pkg-config meson ninja-build ripgrep fd-find"

        # Check if packages are installed
        MISSING=""
        for pkg in $PACKAGES; do
            if ! dpkg -s "$pkg" &> /dev/null 2>&1; then
                MISSING="$MISSING $pkg"
            fi
        done

        if [ -n "$MISSING" ]; then
            info "Installing missing system packages:$MISSING"
            sudo apt-get update
            sudo apt-get install -y $MISSING || error "Failed to install system dependencies"
        else
            info "All system dependencies are already installed"
        fi

    elif command -v pacman &> /dev/null; then
        # Arch Linux
        PACKAGES="cairo gobject-introspection gtk4 libadwaita gtksourceview5 vte4 webkitgtk-6.0 libgit2 libspelling python meson ninja ripgrep fd"

        # Check if packages are installed
        MISSING=""
        for pkg in $PACKAGES; do
            if ! pacman -Q "$pkg" &> /dev/null 2>&1; then
                MISSING="$MISSING $pkg"
            fi
        done

        if [ -n "$MISSING" ]; then
            info "Installing missing system packages:$MISSING"
            sudo pacman -S --noconfirm $MISSING || error "Failed to install system dependencies"
        else
            info "All system dependencies are already installed"
        fi

    else
        warn "Unknown package manager. Please install these dependencies manually:"
        warn "  - cairo development files"
        warn "  - gobject-introspection development files"
        warn "  - gtk4 development files"
        warn "  - libadwaita development files"
        warn "  - gtksourceview5 development files"
        warn "  - vte (gtk4 variant) development files"
        warn "  - webkitgtk 6.0 development files"
        warn "  - libgit2 development files"
        warn "  - libspelling development files (+ GObject introspection typelib)"
        warn "  - python3 development files"
        warn "  - meson and ninja build tools"
        warn "  - ripgrep and fd (recommended, for fast file/content search)"
        warn "  - cage, grim, wlr-randr, ydotool (optional, for the native GUI test harness)"
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# Optional runtime dependencies that enhance the app but are not required to run
# it — failure here is non-fatal and never aborts install:
#   - tmux: keeps the Claude session alive across window restarts (session
#     supervisor); without it the session simply ends when the window closes.
#   - native GUI test harness: a headless Wayland compositor (cage) to run a
#     project's GUI in isolation, a screenshot tool (grim) for visual inspection,
#     output sizing (wlr-randr), and input injection (ydotool) as a fallback.
install_gui_test_deps() {
    info "Checking optional runtime dependencies (tmux, cage, grim, wlr-randr, ydotool)..."

    # Package names are identical across Fedora, Debian/Ubuntu and Arch.
    local PACKAGES="tmux cage grim wlr-randr ydotool"
    local MISSING="" installer=""

    if command -v dnf &> /dev/null; then
        installer="sudo dnf install -y"
        for pkg in $PACKAGES; do rpm -q "$pkg" &> /dev/null || MISSING="$MISSING $pkg"; done
    elif command -v apt-get &> /dev/null; then
        installer="sudo apt-get install -y"
        for pkg in $PACKAGES; do dpkg -s "$pkg" &> /dev/null 2>&1 || MISSING="$MISSING $pkg"; done
    elif command -v pacman &> /dev/null; then
        installer="sudo pacman -S --noconfirm"
        for pkg in $PACKAGES; do pacman -Q "$pkg" &> /dev/null 2>&1 || MISSING="$MISSING $pkg"; done
    else
        warn "Unknown package manager; skipping optional GUI test deps ($PACKAGES)."
        return 0
    fi

    if [ -z "$MISSING" ]; then
        info "GUI test harness dependencies are already installed"
        return 0
    fi

    info "Installing optional GUI test packages:$MISSING"
    if ! $installer $MISSING; then
        warn "Could not install optional GUI test deps ($MISSING). The app works"
        warn "without them; the native GUI test harness will be unavailable until"
        warn "they are installed. Continuing."
    fi
}

install() {
    info "Installing Code Companion..."

    # Clean up old installation (before rename to dev.typedev.CodeCompanion)
    for old_name in "claude-companion" "$APP_NAME"; do
        [ -f "$DESKTOP_DIR/$old_name.desktop" ] && rm "$DESKTOP_DIR/$old_name.desktop"
        [ -f "$ICON_DIR/$old_name.svg" ] && rm "$ICON_DIR/$old_name.svg"
    done
    # Clean up old ClaudeCompanion naming
    [ -f "$DESKTOP_DIR/dev.typedev.ClaudeCompanion.desktop" ] && rm "$DESKTOP_DIR/dev.typedev.ClaudeCompanion.desktop"
    [ -f "$ICON_DIR/dev.typedev.ClaudeCompanion.svg" ] && rm "$ICON_DIR/dev.typedev.ClaudeCompanion.svg"

    # Check uv
    if ! command -v uv &> /dev/null; then
        error "uv is not installed. Install it first: curl -LsSf https://astral.sh/uv/install.sh | sh"
    fi

    # Install system dependencies (cairo, gtk4, etc.)
    install_system_deps

    # Install optional GUI test harness dependencies (cage, grim, ydotool)
    install_gui_test_deps

    # Install Python dependencies
    info "Installing Python dependencies..."
    cd "$APP_DIR"
    uv sync

    # Make launcher executable
    chmod +x "$APP_DIR/bin/$APP_NAME"

    # Create symlink to /usr/local/bin
    info "Creating symlink in $BIN_DIR..."
    sudo ln -sf "$APP_DIR/bin/$APP_NAME" "$BIN_DIR/$APP_NAME"

    # Install icon (name must match app ID for GNOME Shell)
    info "Installing icon..."
    mkdir -p "$ICON_DIR"
    cp "$APP_DIR/src/resources/icons/app.svg" "$ICON_DIR/dev.typedev.CodeCompanion.svg"

    # Install .desktop file (name must match app ID)
    info "Installing .desktop file..."
    mkdir -p "$DESKTOP_DIR"
    cp "$APP_DIR/data/dev.typedev.CodeCompanion.desktop" "$DESKTOP_DIR/"

    # Update desktop database
    if command -v update-desktop-database &> /dev/null; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi

    # Update icon cache
    if command -v gtk-update-icon-cache &> /dev/null; then
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    fi

    info "Installation complete!"
    info "You can now run '$APP_NAME' from terminal or find it in your app menu."
}

update() {
    info "Updating Code Companion..."

    # Clean up old installation
    for old_name in "claude-companion" "$APP_NAME"; do
        [ -f "$DESKTOP_DIR/$old_name.desktop" ] && rm "$DESKTOP_DIR/$old_name.desktop"
        [ -f "$ICON_DIR/$old_name.svg" ] && rm "$ICON_DIR/$old_name.svg"
        # Clean up old symlinks
        [ -L "$BIN_DIR/$old_name" ] && sudo rm "$BIN_DIR/$old_name"
    done
    [ -f "$DESKTOP_DIR/dev.typedev.ClaudeCompanion.desktop" ] && rm "$DESKTOP_DIR/dev.typedev.ClaudeCompanion.desktop"
    [ -f "$ICON_DIR/dev.typedev.ClaudeCompanion.svg" ] && rm "$ICON_DIR/dev.typedev.ClaudeCompanion.svg"

    cd "$APP_DIR"

    # Pull latest changes
    if [ -d .git ]; then
        info "Pulling latest changes..."
        git pull
    else
        warn "Not a git repository, skipping git pull"
    fi

    # Check/install system dependencies
    install_system_deps

    # Check/install optional GUI test harness dependencies (cage, grim, ydotool)
    install_gui_test_deps

    # Update dependencies
    info "Updating Python dependencies..."
    uv sync

    # Update symlink (handles rename from claude-companion to code-companion)
    chmod +x "$APP_DIR/bin/$APP_NAME"
    info "Updating symlink in $BIN_DIR..."
    sudo ln -sf "$APP_DIR/bin/$APP_NAME" "$BIN_DIR/$APP_NAME"

    # Update icon (in case it changed)
    mkdir -p "$ICON_DIR"
    cp "$APP_DIR/src/resources/icons/app.svg" "$ICON_DIR/dev.typedev.CodeCompanion.svg"

    # Update .desktop file
    mkdir -p "$DESKTOP_DIR"
    cp "$APP_DIR/data/dev.typedev.CodeCompanion.desktop" "$DESKTOP_DIR/"

    # Update desktop database
    if command -v update-desktop-database &> /dev/null; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi

    # Update icon cache
    if command -v gtk-update-icon-cache &> /dev/null; then
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    fi

    info "Update complete!"
}

uninstall() {
    info "Uninstalling Code Companion..."

    # Remove symlink
    if [ -L "$BIN_DIR/$APP_NAME" ]; then
        info "Removing symlink..."
        sudo rm "$BIN_DIR/$APP_NAME"
    fi

    # Remove icon (all naming variants)
    info "Removing icon..."
    for icon_name in "dev.typedev.CodeCompanion" "dev.typedev.ClaudeCompanion" "claude-companion" "code-companion"; do
        [ -f "$ICON_DIR/$icon_name.svg" ] && rm "$ICON_DIR/$icon_name.svg"
    done

    # Remove .desktop file (all naming variants)
    info "Removing .desktop file..."
    for desktop_name in "dev.typedev.CodeCompanion" "dev.typedev.ClaudeCompanion" "claude-companion" "code-companion"; do
        [ -f "$DESKTOP_DIR/$desktop_name.desktop" ] && rm "$DESKTOP_DIR/$desktop_name.desktop"
    done

    # Update desktop database
    if command -v update-desktop-database &> /dev/null; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi

    # Remove settings (optional) - check both old and new config dirs
    for config_dir in "$HOME/.config/code-companion" "$HOME/.config/claude-companion"; do
        if [ -d "$config_dir" ]; then
            read -p "Remove settings ($config_dir)? [y/N] " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                rm -rf "$config_dir"
                info "Settings removed from $config_dir"
            fi
        fi
    done

    info "Uninstall complete!"
    info "Source code remains in: $APP_DIR"
}

# Main
case "${1:-install}" in
    install)
        install
        ;;
    update)
        update
        ;;
    uninstall|remove)
        uninstall
        ;;
    *)
        echo "Usage: $0 [install|update|uninstall]"
        echo ""
        echo "Commands:"
        echo "  install    Install Code Companion (default)"
        echo "  update     Update to latest version"
        echo "  uninstall  Remove from system"
        exit 1
        ;;
esac
