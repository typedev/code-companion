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
