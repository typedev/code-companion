"""Embedded VTE terminal widget."""

import os
import subprocess

import gi

gi.require_version("Vte", "3.91")

from gi.repository import Vte, Gtk, GLib, Gdk, Gio, Pango, GObject

from ..services import ToastService, SettingsService


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

        # Horizontal container for left padding + terminal
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.set_vexpand(True)

        # Left padding with terminal background color
        self._left_padding = Gtk.Box()
        self._left_padding.set_size_request(24, -1)  # 24px width
        hbox.append(self._left_padding)

        # Wrap terminal in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self.terminal)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        hbox.append(scrolled)

        self.append(hbox)

        # Search bar (hidden by default)
        self._build_search_bar()

    def _build_search_bar(self):
        """Build the search bar for terminal."""
        self.search_bar = Gtk.Revealer()
        self.search_bar.set_reveal_child(False)

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        search_box.add_css_class("search-bar")
        search_box.set_margin_start(8)
        search_box.set_margin_end(8)
        search_box.set_margin_top(4)
        search_box.set_margin_bottom(4)

        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.set_placeholder_text("Find in terminal...")
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_next)
        self.search_entry.connect("next-match", self._on_search_next)
        self.search_entry.connect("previous-match", self._on_search_prev)
        search_box.append(self.search_entry)

        # Navigation buttons
        prev_btn = Gtk.Button()
        prev_btn.set_icon_name("go-up-symbolic")
        prev_btn.set_tooltip_text("Previous match (Shift+Enter)")
        prev_btn.add_css_class("flat")
        prev_btn.connect("clicked", lambda b: self._on_search_prev())
        search_box.append(prev_btn)

        next_btn = Gtk.Button()
        next_btn.set_icon_name("go-down-symbolic")
        next_btn.set_tooltip_text("Next match (Enter)")
        next_btn.add_css_class("flat")
        next_btn.connect("clicked", lambda b: self._on_search_next())
        search_box.append(next_btn)

        # Case sensitive toggle
        self.case_btn = Gtk.ToggleButton()
        self.case_btn.set_icon_name("font-x-generic-symbolic")
        self.case_btn.set_tooltip_text("Match case")
        self.case_btn.add_css_class("flat")
        self.case_btn.connect("toggled", self._on_search_changed)
        search_box.append(self.case_btn)

        # Close button
        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.set_tooltip_text("Close (Escape)")
        close_btn.add_css_class("flat")
        close_btn.connect("clicked", lambda b: self.hide_search())
        search_box.append(close_btn)

        self.search_bar.set_child(search_box)
        self.append(self.search_bar)

    def show_search(self):
        """Show the search bar."""
        self.search_bar.set_reveal_child(True)
        self.search_entry.grab_focus()

    def hide_search(self):
        """Hide the search bar."""
        self.search_bar.set_reveal_child(False)
        self.terminal.search_set_regex(None, 0)
        self.terminal.grab_focus()

    def _on_search_changed(self, *args):
        """Handle search text or options change."""
        text = self.search_entry.get_text()
        if not text:
            self.terminal.search_set_regex(None, 0)
            return

        # Build regex flags
        import re
        flags = 0
        if not self.case_btn.get_active():
            flags |= GLib.RegexCompileFlags.CASELESS

        try:
            # Escape special regex characters for literal search
            escaped = GLib.Regex.escape_string(text, len(text))
            regex = GLib.Regex.new(escaped, flags, 0)
            self.terminal.search_set_regex(regex, 0)
            self.search_entry.remove_css_class("error")
        except GLib.Error:
            self.search_entry.add_css_class("error")

    def _on_search_next(self, *args):
        """Find next match."""
        self.terminal.search_find_next()

    def _on_search_prev(self, *args):
        """Find previous match."""
        self.terminal.search_find_previous()

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press events for copy/paste and search."""
        ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK
        shift_pressed = state & Gdk.ModifierType.SHIFT_MASK

        # Ctrl+Shift+F - show search
        if ctrl_pressed and shift_pressed:
            key_name = Gdk.keyval_name(keyval)
            if key_name in ("F", "f", "Cyrillic_A", "Cyrillic_a"):
                self.show_search()
                return True

        # Escape - hide search
        if keyval == Gdk.KEY_Escape:
            if self.search_bar.get_reveal_child():
                self.hide_search()
                return True

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
            ToastService.show_error(f"Clipboard paste error: {e}")

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
            ToastService.show_error(f"Terminal spawn error: {error}")
        else:
            self.child_pid = pid
            # Auto-activate .venv if exists
            self._activate_venv_if_exists()

    def _activate_venv_if_exists(self):
        """Activate .venv if it exists in the working directory."""
        if not self.current_directory:
            return

        venv_activate = os.path.join(self.current_directory, ".venv", "bin", "activate")
        if os.path.isfile(venv_activate):
            # Small delay to let shell initialize
            GLib.timeout_add(100, lambda: self._source_venv(venv_activate) or False)

    def _source_venv(self, activate_path: str):
        """Source the venv activate script."""
        # Use source command (works in bash, zsh, etc.)
        self.terminal.feed_child(f"source {GLib.shell_quote(activate_path)}\n".encode())

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
        """Apply terminal font and colors from app settings."""
        self.settings = SettingsService.get_instance()
        self._apply_font()
        self._apply_colors()

        # Listen for settings changes
        self.settings.connect("changed", self._on_setting_changed)

    def _on_setting_changed(self, settings, key, value):
        """Handle settings changes."""
        if key.startswith("editor."):
            self._apply_font()

    def _apply_font(self):
        """Apply font settings from app settings (same as editor)."""
        font_family = self.settings.get("editor.font_family", "Monospace")
        font_size = self.settings.get("editor.font_size", 12)
        line_height = self.settings.get("editor.line_height", 1.4)

        font_desc = Pango.FontDescription.from_string(f"{font_family} {font_size}")
        self.terminal.set_font(font_desc)
        self.terminal.set_cell_height_scale(line_height)

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

        # Apply background color to left padding
        css = f"""
        .terminal-padding {{
            background-color: {DRACULA_PALETTE["background"]};
        }}
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._left_padding.add_css_class("terminal-padding")

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
