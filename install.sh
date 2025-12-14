#!/bin/bash
# Claude Companion installation script
# Run: ./install.sh [install|update|uninstall]

set -e

APP_NAME="claude-companion"
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

install() {
    info "Installing Claude Companion..."

    # Clean up old installation (before rename to dev.typedev.ClaudeCompanion)
    if [ -f "$DESKTOP_DIR/$APP_NAME.desktop" ]; then
        info "Removing old .desktop file..."
        rm "$DESKTOP_DIR/$APP_NAME.desktop"
    fi
    if [ -f "$ICON_DIR/$APP_NAME.svg" ]; then
        info "Removing old icon..."
        rm "$ICON_DIR/$APP_NAME.svg"
    fi

    # Check dependencies
    if ! command -v uv &> /dev/null; then
        error "uv is not installed. Install it first: curl -LsSf https://astral.sh/uv/install.sh | sh"
    fi

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
    cp "$APP_DIR/src/resources/icons/claude.svg" "$ICON_DIR/dev.typedev.ClaudeCompanion.svg"

    # Install .desktop file (name must match app ID)
    info "Installing .desktop file..."
    mkdir -p "$DESKTOP_DIR"
    cp "$APP_DIR/data/dev.typedev.ClaudeCompanion.desktop" "$DESKTOP_DIR/"

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
    info "Updating Claude Companion..."

    # Clean up old installation (before rename to dev.typedev.ClaudeCompanion)
    [ -f "$DESKTOP_DIR/$APP_NAME.desktop" ] && rm "$DESKTOP_DIR/$APP_NAME.desktop"
    [ -f "$ICON_DIR/$APP_NAME.svg" ] && rm "$ICON_DIR/$APP_NAME.svg"

    cd "$APP_DIR"

    # Pull latest changes
    if [ -d .git ]; then
        info "Pulling latest changes..."
        git pull
    else
        warn "Not a git repository, skipping git pull"
    fi

    # Update dependencies
    info "Updating Python dependencies..."
    uv sync

    # Update icon (in case it changed)
    mkdir -p "$ICON_DIR"
    cp "$APP_DIR/src/resources/icons/claude.svg" "$ICON_DIR/dev.typedev.ClaudeCompanion.svg"

    # Update .desktop file
    mkdir -p "$DESKTOP_DIR"
    cp "$APP_DIR/data/dev.typedev.ClaudeCompanion.desktop" "$DESKTOP_DIR/"

    info "Update complete!"
}

uninstall() {
    info "Uninstalling Claude Companion..."

    # Remove symlink
    if [ -L "$BIN_DIR/$APP_NAME" ]; then
        info "Removing symlink..."
        sudo rm "$BIN_DIR/$APP_NAME"
    fi

    # Remove icon
    if [ -f "$ICON_DIR/dev.typedev.ClaudeCompanion.svg" ]; then
        info "Removing icon..."
        rm "$ICON_DIR/dev.typedev.ClaudeCompanion.svg"
    fi
    # Also remove old icon name if exists
    [ -f "$ICON_DIR/$APP_NAME.svg" ] && rm "$ICON_DIR/$APP_NAME.svg"

    # Remove .desktop file
    if [ -f "$DESKTOP_DIR/dev.typedev.ClaudeCompanion.desktop" ]; then
        info "Removing .desktop file..."
        rm "$DESKTOP_DIR/dev.typedev.ClaudeCompanion.desktop"
    fi
    # Also remove old .desktop name if exists
    [ -f "$DESKTOP_DIR/$APP_NAME.desktop" ] && rm "$DESKTOP_DIR/$APP_NAME.desktop"

    # Update desktop database
    if command -v update-desktop-database &> /dev/null; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi

    # Remove settings (optional)
    if [ -d "$HOME/.config/claude-companion" ]; then
        read -p "Remove settings (~/.config/claude-companion)? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$HOME/.config/claude-companion"
            info "Settings removed"
        fi
    fi

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
        echo "  install    Install Claude Companion (default)"
        echo "  update     Update to latest version"
        echo "  uninstall  Remove from system"
        exit 1
        ;;
esac
