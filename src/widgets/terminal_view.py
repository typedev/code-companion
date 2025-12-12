"""Embedded VTE terminal widget."""

import os
import subprocess

import gi

gi.require_version("Vte", "3.91")

from gi.repository import Vte, Gtk, GLib, Gdk, Gio, Pango, GObject


# Dracula palette - matches ptyxis dracula theme
DRACULA_PALETTE = {
    "foreground": "#f8f8f2",
    "background": "#282a36",
    "colors": [
        "#21222c",  # black
        "#ff5555",  # red
        "#50fa7b",  # green
        "#f1fa8c",  # yellow
        "#bd93f9",  # blue
        "#ff79c6",  # magenta
        "#8be9fd",  # cyan
        "#f8f8f2",  # white
        "#6272a4",  # bright black
        "#ff6e6e",  # bright red
        "#69ff94",  # bright green
        "#ffffa5",  # bright yellow
        "#d6acff",  # bright blue
        "#ff92df",  # bright magenta
        "#a4ffff",  # bright cyan
        "#ffffff",  # bright white
    ]
}


class TerminalView(Gtk.Box):
    """A widget containing an embedded VTE terminal."""

    __gsignals__ = {
        "child-exited": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, working_directory: str | None = None, run_command: str | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.terminal = None
        self.current_directory = working_directory
        self._initial_command = run_command
        self._respawn_on_exit = True

        self._build_ui()
        self._apply_terminal_settings()
        self._spawn_shell(working_directory)

        # Run initial command after shell starts
        if run_command:
            GLib.timeout_add(500, lambda: self.run_command(run_command) or False)

    def _build_ui(self):
        """Build the terminal UI."""
        # Create terminal
        self.terminal = Vte.Terminal()
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)

        # Configure terminal
        self.terminal.set_scroll_on_output(True)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_scrollback_lines(10000)

        # Set font
        self.terminal.set_font_scale(1.0)

        # Connect signals
        self.terminal.connect("child-exited", self._on_child_exited)

        # Add key event controller for copy/paste shortcuts
        # Use CAPTURE phase to intercept before terminal processes keys
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.terminal.add_controller(key_controller)

        # Wrap in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self.terminal)
        scrolled.set_vexpand(True)

        self.append(scrolled)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press events for copy/paste."""
        ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK
        shift_pressed = state & Gdk.ModifierType.SHIFT_MASK

        if ctrl_pressed:
            # Get key name for layout-independent matching
            key_name = Gdk.keyval_name(keyval)

            # Ctrl+C - copy if there's selection, otherwise let terminal handle it
            # Support both Latin 'c' and Cyrillic 'с' (Cyrillic_es)
            if key_name in ("c", "Cyrillic_es") and not shift_pressed:
                if self.terminal.get_has_selection():
                    self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                    return True  # Stop propagation
                # No selection - let Ctrl+C go through as SIGINT
                return False

            # Ctrl+V - paste
            # Support both Latin 'v' and Cyrillic 'м' (Cyrillic_em)
            if key_name in ("v", "Cyrillic_em") and not shift_pressed:
                self._paste_from_clipboard()
                return True  # Stop propagation

            # Ctrl+Shift+C - always copy (standard terminal behavior)
            if key_name in ("C", "Cyrillic_ES") and shift_pressed:
                self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True

            # Ctrl+Shift+V - always paste (standard terminal behavior)
            if key_name in ("V", "Cyrillic_EM") and shift_pressed:
                self._paste_from_clipboard()
                return True

        return False  # Let other keys pass through

    def _paste_from_clipboard(self):
        """Paste text from clipboard directly to terminal."""
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.read_text_async(None, self._on_clipboard_text_received)

    def _on_clipboard_text_received(self, clipboard, result):
        """Handle clipboard text received."""
        try:
            text = clipboard.read_text_finish(result)
            if text:
                # Feed text directly to terminal as input
                self.terminal.feed_child(text.encode("utf-8"))
        except Exception as e:
            print(f"Clipboard paste error: {e}")

    def _spawn_shell(self, working_directory: str | None = None):
        """Spawn a shell in the terminal."""
        # Get user's default shell
        shell = os.environ.get("SHELL", "/bin/bash")

        # Use provided directory or home
        cwd = working_directory or os.path.expanduser("~")
        self.current_directory = cwd

        # Spawn the shell
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            cwd,
            [shell],
            None,  # Environment (inherit)
            GLib.SpawnFlags.DEFAULT,
            None,  # Child setup callback
            None,  # Child setup data
            -1,    # Timeout (-1 = default)
            None,  # Cancellable
            self._on_spawn_complete,  # Callback
        )

    def _on_spawn_complete(self, terminal, pid, error):
        """Called when shell spawn completes."""
        if error:
            print(f"Terminal spawn error: {error}")
        else:
            self.child_pid = pid

    def _on_child_exited(self, terminal, status):
        """Handle shell exit."""
        # Emit signal for parent to handle
        self.emit("child-exited", status)

        # Respawn shell if configured to do so
        if self._respawn_on_exit:
            GLib.timeout_add(100, self._respawn_shell)

    def _respawn_shell(self):
        """Respawn shell after exit."""
        self._spawn_shell(self.current_directory)
        return False  # Don't repeat

    def change_directory(self, path: str):
        """Change the terminal's working directory."""
        if not os.path.isdir(path):
            return

        self.current_directory = path

        # Send cd command to the terminal
        cd_command = f"cd {GLib.shell_quote(path)}\n"
        self.terminal.feed_child(cd_command.encode())

    def run_command(self, command: str):
        """Run a command in the terminal."""
        self.terminal.feed_child(f"{command}\n".encode())

    def clear(self):
        """Clear the terminal."""
        self.terminal.feed_child(b"clear\n")

    def _apply_terminal_settings(self):
        """Apply terminal font and colors from system settings."""
        self._apply_font()
        self._apply_colors()

    def _apply_font(self):
        """Apply system monospace font and line height to terminal."""
        try:
            settings = Gio.Settings.new("org.gnome.desktop.interface")
            font_name = settings.get_string("monospace-font-name")
            if font_name:
                font_desc = Pango.FontDescription.from_string(font_name)
                self.terminal.set_font(font_desc)

            # Apply cell height scale (line height) from ptyxis if available
            try:
                # Read from dconf directly since ptyxis uses relocatable schema
                import subprocess
                result = subprocess.run(
                    ["dconf", "read", "/org/gnome/Ptyxis/Profiles/eba807f5e94ca4ff724f46e16915178b/cell-height-scale"],
                    capture_output=True, text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    cell_height = float(result.stdout.strip())
                    self.terminal.set_cell_height_scale(cell_height)
            except Exception:
                # Default to 1.3 if can't read from ptyxis
                self.terminal.set_cell_height_scale(1.3)

        except Exception:
            pass  # Use default font if settings unavailable

    def _apply_colors(self):
        """Apply Dracula color palette to terminal."""
        # Foreground
        fg = Gdk.RGBA()
        fg.parse(DRACULA_PALETTE["foreground"])

        # Background
        bg = Gdk.RGBA()
        bg.parse(DRACULA_PALETTE["background"])

        # 16-color palette
        palette = []
        for color_str in DRACULA_PALETTE["colors"]:
            rgba = Gdk.RGBA()
            rgba.parse(color_str)
            palette.append(rgba)

        self.terminal.set_colors(fg, bg, palette)

    def open_system_terminal(self):
        """Open system terminal in current directory."""
        cwd = self.current_directory or os.path.expanduser("~")

        # Try xdg-terminal-exec first (modern freedesktop standard)
        # Then fall back to common terminal emulators
        terminals = [
            ["xdg-terminal-exec"],  # Will open in cwd if we set it
            ["ptyxis", f"--working-directory={cwd}"],
            ["gnome-terminal", f"--working-directory={cwd}"],
            ["kgx", f"--working-directory={cwd}"],
            ["konsole", f"--workdir={cwd}"],
            ["xfce4-terminal", f"--working-directory={cwd}"],
            ["tilix", f"--working-directory={cwd}"],
            ["alacritty", f"--working-directory={cwd}"],
            ["kitty", f"--directory={cwd}"],
        ]

        for cmd in terminals:
            try:
                subprocess.Popen(cmd, cwd=cwd, start_new_session=True)
                return True
            except FileNotFoundError:
                continue

        return False
