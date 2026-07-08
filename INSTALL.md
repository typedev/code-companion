# Installation Guide

## Requirements

- **OS**: Linux (tested on Fedora 43, Ubuntu 26.04)
- **Python**: 3.12+
- **uv**: Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))

### System Dependencies (Fedora)

```bash
sudo dnf install gtk4-devel libadwaita-devel gtksourceview5-devel \
    vte291-gtk4-devel python3-gobject pygobject3-devel \
    libgit2-devel webkitgtk6.0-devel libspelling-devel \
    ripgrep fd-find
```

### System Dependencies (Ubuntu/Debian)

```bash
sudo apt install libgtk-4-dev libadwaita-1-dev libgtksourceview-5-dev \
    libvte-2.91-gtk4-dev libwebkitgtk-6.0-dev libgit2-dev \
    libspelling-1-dev gir1.2-spelling-1 \
    libcairo2-dev libgirepository-2.0-dev pkg-config \
    python3-dev python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
    ripgrep fd-find
```

> **Ubuntu note:** install `cage`/`grim`/`ydotool` from the distro repos, not as
> Snaps — Snap confinement can block access to `/dev/uinput` and Wayland sockets
> the harness needs.

### Optional: persistent Claude session

`tmux` (≥ 3.2) keeps the embedded Claude session alive across window restarts — the
session runs inside a tmux session, so closing/reopening the project window re-attaches
instead of killing `claude`. Without it the app still works; the session simply ends when
the window closes.

```bash
# Package name is identical across distros
sudo dnf install tmux      # Fedora
sudo apt install tmux      # Ubuntu/Debian
sudo pacman -S tmux        # Arch
```

`install.sh` installs it automatically (non-fatally). No user `~/.tmux.conf` is needed —
the app ships its own managed config and runs tmux invisibly.

### Optional: native GUI test harness

These let the assistant launch, drive and screenshot **another project's** GTK/Qt
GUI in an isolated headless Wayland compositor — so it can visually inspect and
test native UIs the way Playwright handles the web. Not required to run Code
Companion itself; the app works fine without them.

```bash
# Fedora
sudo dnf install cage grim wlr-randr ydotool

# Ubuntu/Debian (from repos, not Snap — see note above)
sudo apt install cage grim wlr-randr ydotool

# Arch
sudo pacman -S cage grim wlr-randr ydotool
```

- `cage` — headless [wlroots](https://gitlab.freedesktop.org/wlroots/wlroots) compositor that hosts the app under test
- `grim` — captures the compositor's output as PNG for visual inspection
- `wlr-randr` — sizes the headless output to the desired canvas (`--custom-mode`)
- `ydotool` — coordinate-level input injection fallback (needs `/dev/uinput`
  access: add your user to the `input` group + a udev rule, and run `ydotoold`)

The semantic layer (read the widget tree, click/type by role+name) also needs the
**AT-SPI** stack — `at-spi2-core` (`at-spi-bus-launcher` + `at-spi2-registryd`) and the
`Atspi` GObject-introspection binding. These ship with the GTK4 stack, so they are
already pulled in by the app's base dependencies; no separate install is required.

The `install.sh` script installs these automatically (non-fatally) when available.

---

## Quick Install

```bash
# Clone the repository
git clone https://github.com/typedev/code-companion.git
cd code-companion

# Run installation script
chmod +x install.sh
./install.sh
```

This will:
- Install Python dependencies via uv
- Create `/usr/local/bin/code-companion` symlink
- Install app icon to `~/.local/share/icons/`
- Install .desktop file to `~/.local/share/applications/`

---

## Usage

### From Terminal

```bash
# Open Project Manager (select/add projects)
code-companion

# Open specific project directly
code-companion --project /path/to/your/project
```

### From App Menu

Search for "Code Companion" in your desktop environment's application menu.

---

## Update

```bash
cd /path/to/code-companion
./install.sh update
```

Or manually:

```bash
cd /path/to/code-companion
git pull
uv sync
```

Since the app is installed via symlink, changes take effect immediately.

---

## Uninstall

```bash
cd /path/to/code-companion
./install.sh uninstall
```

This will:
- Remove `/usr/local/bin/code-companion` symlink
- Remove app icon
- Remove .desktop file
- Optionally remove settings (`~/.config/code-companion/`)

The source code directory is **not** deleted.

---

## Manual Installation

If you prefer not to use the install script:

### 1. Install Dependencies

```bash
cd code-companion
uv sync
```

### 2. Create Launcher Script

```bash
sudo ln -sf $(pwd)/bin/code-companion /usr/local/bin/code-companion
chmod +x bin/code-companion
```

### 3. Install Icon

```bash
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
cp src/resources/icons/app.svg ~/.local/share/icons/hicolor/scalable/apps/dev.typedev.CodeCompanion.svg
```

### 4. Install .desktop File

```bash
cp data/dev.typedev.CodeCompanion.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

---

## Configuration

Settings are stored in `~/.config/code-companion/`:

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
cd code-companion
uv run python -m src.main
```

Or with a specific project:

```bash
uv run python -m src.main --project /path/to/project
```
