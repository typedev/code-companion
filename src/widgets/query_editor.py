"""Query Editor widget with Markdown highlighting and spell checking."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")
gi.require_version("Spelling", "1")

from gi.repository import Gtk, GtkSource, GObject, Pango, Spelling

from ..services import SettingsService


class QueryEditor(Gtk.Box):
    """Collapsible multi-line editor for composing queries with spell checking."""

    __gsignals__ = {
        "send-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._expanded = False
        self.settings = SettingsService.get_instance()

        self._build_ui()
        self._setup_spellcheck()
        self._apply_settings()

        # Listen for settings changes
        self.settings.connect("changed", self._on_setting_changed)

    def _build_ui(self):
        """Build the editor UI."""
        # Add top separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.append(separator)

        # Header bar with toggle button
        self._build_header()

        # Revealer for collapsible content
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_reveal_child(False)

        # Content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.add_css_class("query-editor-content")
        content_box.set_margin_start(8)
        content_box.set_margin_end(8)

        # Editor area
        self._build_editor(content_box)

        # Button bar
        self._build_button_bar(content_box)

        self.revealer.set_child(content_box)
        self.append(self.revealer)

    def _build_header(self):
        """Build the header bar with toggle and language selector."""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("query-editor-header")
        header.set_margin_start(8)
        header.set_margin_end(8)
        header.set_margin_top(4)
        header.set_margin_bottom(4)

        # Toggle button with arrow
        self.toggle_btn = Gtk.Button()
        self.toggle_btn.add_css_class("flat")
        self._update_toggle_button()
        self.toggle_btn.connect("clicked", self._on_toggle_clicked)
        header.append(self.toggle_btn)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        # Language selector button
        self._build_language_selector(header)

        self.append(header)

    def _build_language_selector(self, header: Gtk.Box):
        """Build language selector dropdown."""
        # Get available languages
        provider = Spelling.Provider.get_default()
        languages = provider.list_languages()

        # Create menu
        menu = Gtk.StringList()
        menu.append("Auto")
        self._language_codes = ["auto"]

        for lang in languages:
            menu.append(lang.get_name())
            self._language_codes.append(lang.get_code())

        # Dropdown
        self.lang_dropdown = Gtk.DropDown(model=menu)
        self.lang_dropdown.set_tooltip_text("Spell check language")
        self.lang_dropdown.add_css_class("flat")

        # Load saved language
        saved_lang = self.settings.get("editor.spellcheck_language", "auto")
        if saved_lang in self._language_codes:
            idx = self._language_codes.index(saved_lang)
            self.lang_dropdown.set_selected(idx)

        self.lang_dropdown.connect("notify::selected", self._on_language_changed)
        header.append(self.lang_dropdown)

    def _build_editor(self, parent: Gtk.Box):
        """Build the GtkSourceView editor."""
        # Create source buffer with Markdown language
        self.buffer = GtkSource.Buffer()

        lang_manager = GtkSource.LanguageManager.get_default()
        md_language = lang_manager.get_language("markdown")
        if md_language:
            self.buffer.set_language(md_language)

        # Create source view
        self.source_view = GtkSource.View(buffer=self.buffer)
        self.source_view.set_editable(True)
        self.source_view.set_cursor_visible(True)
        self.source_view.set_show_line_numbers(False)
        self.source_view.set_monospace(True)
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.source_view.set_left_margin(8)
        self.source_view.set_right_margin(8)
        self.source_view.set_top_margin(8)
        self.source_view.set_bottom_margin(8)

        # Wrap in scrolled window with frame
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_min_content_height(200)  # ~10 lines default
        self.scrolled.set_child(self.source_view)

        # Add frame around editor (no rounded corners)
        frame = Gtk.Frame()
        frame.set_child(self.scrolled)

        # Remove rounded corners via CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string("frame { border-radius: 0; }")
        frame.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        parent.append(frame)

    def _build_button_bar(self, parent: Gtk.Box):
        """Build the button bar with Clear and Send buttons."""
        button_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_bar.set_margin_start(8)
        button_bar.set_margin_end(8)
        button_bar.set_margin_top(4)
        button_bar.set_margin_bottom(8)
        button_bar.set_halign(Gtk.Align.END)

        # Clear button
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.add_css_class("flat")
        clear_btn.connect("clicked", self._on_clear_clicked)
        button_bar.append(clear_btn)

        # Send button
        send_btn = Gtk.Button(label="Send")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", self._on_send_clicked)
        button_bar.append(send_btn)

        parent.append(button_bar)

    def _setup_spellcheck(self):
        """Set up spell checking with libspelling."""
        self.checker = Spelling.Checker.get_default()
        self.spell_adapter = Spelling.TextBufferAdapter.new(self.buffer, self.checker)
        self.spell_adapter.set_enabled(True)

        # Add spell check menu to right-click
        menu_model = self.spell_adapter.get_menu_model()
        self.source_view.set_extra_menu(menu_model)

        # Apply saved language
        self._apply_spellcheck_language()

    def _apply_spellcheck_language(self):
        """Apply spell check language from settings."""
        lang_code = self.settings.get("editor.spellcheck_language", "auto")

        if lang_code == "auto":
            # Keep default system language (don't change)
            return

        # Set language code directly on adapter
        self.spell_adapter.set_language(lang_code)

    def _apply_settings(self):
        """Apply editor settings (font, colors, etc.)."""
        # Syntax scheme
        scheme_id = self.settings.get("appearance.syntax_scheme", "Adwaita-dark")
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = style_manager.get_scheme(scheme_id)
        if not scheme:
            scheme = style_manager.get_scheme("Adwaita-dark")
        if not scheme:
            scheme = style_manager.get_scheme("classic")
        if scheme:
            self.buffer.set_style_scheme(scheme)

        # Font
        font_family = self.settings.get("editor.font_family", "Monospace")
        font_size = self.settings.get("editor.font_size", 12)
        line_height = self.settings.get("editor.line_height", 1.4)

        css = f"""
            textview {{
                font-family: "{font_family}";
                font-size: {font_size}pt;
                line-height: {line_height};
            }}
        """
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(css)
        self.source_view.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._css_provider = css_provider

    def _on_setting_changed(self, settings, key, value):
        """Handle settings changes."""
        if key.startswith("appearance.") or key.startswith("editor."):
            self._apply_settings()
        if key == "editor.spellcheck_language":
            self._apply_spellcheck_language()

    def _update_toggle_button(self):
        """Update toggle button icon and label."""
        icon = "pan-down-symbolic" if self._expanded else "pan-end-symbolic"
        self.toggle_btn.set_icon_name(icon)
        self.toggle_btn.set_label("Query Editor")

    def _on_toggle_clicked(self, button):
        """Handle toggle button click."""
        self._expanded = not self._expanded
        self.revealer.set_reveal_child(self._expanded)
        self._update_toggle_button()

        if self._expanded:
            self.source_view.grab_focus()

    def _on_language_changed(self, dropdown, pspec):
        """Handle language selection change."""
        idx = dropdown.get_selected()
        if idx < len(self._language_codes):
            lang_code = self._language_codes[idx]
            self.settings.set("editor.spellcheck_language", lang_code)

    def _on_clear_clicked(self, button):
        """Handle Clear button click."""
        self.buffer.set_text("")
        self.source_view.grab_focus()

    def _on_send_clicked(self, button):
        """Handle Send button click."""
        text = self.get_text()
        if text.strip():
            self.emit("send-requested", text)

    # Public API

    def get_text(self) -> str:
        """Get the editor text."""
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        return self.buffer.get_text(start, end, True)

    def set_text(self, text: str):
        """Set the editor text."""
        self.buffer.set_text(text)

    def clear(self):
        """Clear the editor."""
        self.buffer.set_text("")

    def set_expanded(self, expanded: bool):
        """Set expanded state."""
        if self._expanded != expanded:
            self._expanded = expanded
            self.revealer.set_reveal_child(expanded)
            self._update_toggle_button()

    def is_expanded(self) -> bool:
        """Check if editor is expanded."""
        return self._expanded

    def grab_focus(self):
        """Focus the editor."""
        self.source_view.grab_focus()
