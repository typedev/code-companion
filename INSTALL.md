# Installation Guide

## Requirements

- **OS**: Linux (tested on Fedora 43)
- **Python**: 3.12+
- **uv**: Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))

### System Dependencies (Fedora)

```bash
sudo dnf install gtk4-devel libadwaita-devel gtksourceview5-devel \
    vte291-gtk4-devel python3-gobject pygobject3-devel \
    libgit2-devel webkit2gtk5.0-devel
```

### System Dependencies (Ubuntu/Debian)

```bash
sudo apt install libgtk-4-dev libadwaita-1-dev libgtksourceview-5-dev \
    libvte-2.91-gtk4-dev python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
    libgit2-dev libwebkitgtk-6.0-dev
```

---

## Quick Install

```bash
# Clone the repository
git clone https://github.com/typedev/claude-companion.git
cd claude-companion

# Run installation script
chmod +x install.sh
./install.sh
```

This will:
- Install Python dependencies via uv
- Create `/usr/local/bin/claude-companion` symlink
- Install app icon to `~/.local/share/icons/`
- Install .desktop file to `~/.local/share/applications/`

---

## Usage

### From Terminal

```bash
# Open Project Manager (select/add projects)
claude-companion

# Open specific project directly
claude-companion --project /path/to/your/project
```

### From App Menu

Search for "Claude Companion" in your desktop environment's application menu.

---

## Update

```bash
cd /path/to/claude-companion
./install.sh update
```

Or manually:

```bash
cd /path/to/claude-companion
git pull
uv sync
```

Since the app is installed via symlink, changes take effect immediately.

---

## Uninstall

```bash
cd /path/to/claude-companion
./install.sh uninstall
```

This will:
- Remove `/usr/local/bin/claude-companion` symlink
- Remove app icon
- Remove .desktop file
- Optionally remove settings (`~/.config/claude-companion/`)

The source code directory is **not** deleted.

---

## Manual Installation

If you prefer not to use the install script:

### 1. Install Dependencies

```bash
cd claude-companion
uv sync
```

### 2. Create Launcher Script

```bash
sudo ln -sf $(pwd)/bin/claude-companion /usr/local/bin/claude-companion
chmod +x bin/claude-companion
```

### 3. Install Icon

```bash
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
cp src/resources/icons/claude.svg ~/.local/share/icons/hicolor/scalable/apps/claude-companion.svg
```

### 4. Install .desktop File

```bash
cp data/claude-companion.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

---

## Configuration

Settings are stored in `~/.config/claude-companion/`:

| File | Description |
|------|-------------|
| `settings.json` | App preferences (theme, font, etc.) |
| `projects.json` | Registered projects list |

---

## Troubleshooting

### App doesn't start

Check if all dependencies are installed:

```bash
python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')"
```

### Icon not showing

Update icon cache:

```bash
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

### Command not found

Check if `/usr/local/bin` is in your PATH:

```bash
echo $PATH | grep -q /usr/local/bin && echo "OK" || echo "Add /usr/local/bin to PATH"
```

---

## Development

Run directly without installation:

```bash
cd claude-companion
uv run python -m src.main
```

Or with a specific project:

```bash
uv run python -m src.main --project /path/to/project
```
