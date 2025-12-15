"""Preferences dialog for application settings."""

import gi

gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, Gtk, GtkSource

from ..services import SettingsService


class PreferencesDialog(Adw.PreferencesDialog):
    """Application preferences dialog."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.settings = SettingsService.get_instance()

        self.set_title("Preferences")

        # Build pages
        self._build_appearance_page()
        self._build_editor_page()
        self._build_linters_page()

    def _build_appearance_page(self):
        """Build the Appearance preferences page."""
        page = Adw.PreferencesPage()
        page.set_title("Appearance")
        page.set_icon_name("applications-graphics-symbolic")

        # Theme group
        theme_group = Adw.PreferencesGroup()
        theme_group.set_title("Theme")

        # Theme selector
        theme_row = Adw.ComboRow()
        theme_row.set_title("Color Scheme")
        theme_row.set_subtitle("Choose the application color scheme")

        theme_model = Gtk.StringList.new(["System", "Light", "Dark"])
        theme_row.set_model(theme_model)

        # Set current value
        current_theme = self.settings.get("appearance.theme", "system")
        theme_map = {"system": 0, "light": 1, "dark": 2}
        theme_row.set_selected(theme_map.get(current_theme, 0))

        theme_row.connect("notify::selected", self._on_theme_changed)
        theme_group.add(theme_row)

        # Syntax scheme selector
        scheme_row = Adw.ComboRow()
        scheme_row.set_title("Syntax Highlighting")
        scheme_row.set_subtitle("Color scheme for code editor")

        # Get available schemes
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        scheme_ids = scheme_manager.get_scheme_ids()

        # Build display names
        scheme_names = []
        self._scheme_ids = []
        for scheme_id in sorted(scheme_ids):
            scheme = scheme_manager.get_scheme(scheme_id)
            if scheme:
                name = scheme.get_name() or scheme_id
                scheme_names.append(name)
                self._scheme_ids.append(scheme_id)

        scheme_model = Gtk.StringList.new(scheme_names)
        scheme_row.set_model(scheme_model)

        # Set current value
        current_scheme = self.settings.get("appearance.syntax_scheme", "Adwaita-dark")
        try:
            scheme_index = self._scheme_ids.index(current_scheme)
            scheme_row.set_selected(scheme_index)
        except ValueError:
            pass  # Use default

        scheme_row.connect("notify::selected", self._on_scheme_changed)
        theme_group.add(scheme_row)

        page.add(theme_group)
        self.add(page)

    def _build_editor_page(self):
        """Build the Editor preferences page."""
        page = Adw.PreferencesPage()
        page.set_title("Editor")
        page.set_icon_name("accessories-text-editor-symbolic")

        # Font group
        font_group = Adw.PreferencesGroup()
        font_group.set_title("Font")

        # Font family
        font_row = Adw.EntryRow()
        font_row.set_title("Font Family")
        current_font = self.settings.get("editor.font_family", "Monospace")
        font_row.set_text(current_font)
        font_row.connect("changed", self._on_font_family_changed)
        font_group.add(font_row)

        # Font size
        size_row = Adw.SpinRow.new_with_range(8, 32, 1)
        size_row.set_title("Font Size")
        size_row.set_value(self.settings.get("editor.font_size", 12))
        size_row.connect("notify::value", self._on_font_size_changed)
        font_group.add(size_row)

        # Line height
        height_row = Adw.SpinRow.new_with_range(1.0, 2.0, 0.1)
        height_row.set_title("Line Height")
        height_row.set_value(self.settings.get("editor.line_height", 1.4))
        height_row.connect("notify::value", self._on_line_height_changed)
        font_group.add(height_row)

        page.add(font_group)

        # Tabs group
        tabs_group = Adw.PreferencesGroup()
        tabs_group.set_title("Indentation")

        # Tab size
        tab_row = Adw.SpinRow.new_with_range(2, 8, 1)
        tab_row.set_title("Tab Size")
        tab_row.set_value(self.settings.get("editor.tab_size", 4))
        tab_row.connect("notify::value", self._on_tab_size_changed)
        tabs_group.add(tab_row)

        # Insert spaces
        spaces_row = Adw.SwitchRow()
        spaces_row.set_title("Insert Spaces")
        spaces_row.set_subtitle("Use spaces instead of tabs")
        spaces_row.set_active(self.settings.get("editor.insert_spaces", True))
        spaces_row.connect("notify::active", self._on_insert_spaces_changed)
        tabs_group.add(spaces_row)

        page.add(tabs_group)

        # Display group
        display_group = Adw.PreferencesGroup()
        display_group.set_title("Display")

        # Word wrap
        wrap_row = Adw.SwitchRow()
        wrap_row.set_title("Word Wrap")
        wrap_row.set_subtitle("Wrap long lines at word boundaries")
        wrap_row.set_active(self.settings.get("editor.word_wrap", True))
        wrap_row.connect("notify::active", self._on_word_wrap_changed)
        display_group.add(wrap_row)

        page.add(display_group)
        self.add(page)

    def _build_linters_page(self):
        """Build the Linters preferences page."""
        page = Adw.PreferencesPage()
        page.set_title("Linters")
        page.set_icon_name("dialog-warning-symbolic")

        # Linters group
        linters_group = Adw.PreferencesGroup()
        linters_group.set_title("Enabled Linters")
        linters_group.set_description("Choose which linters to run in the Problems panel")

        # Ruff toggle
        ruff_row = Adw.SwitchRow()
        ruff_row.set_title("Ruff")
        ruff_row.set_subtitle("Fast Python linter (style, imports, etc.)")
        ruff_row.set_active(self.settings.get("linters.ruff_enabled", True))
        ruff_row.connect("notify::active", self._on_ruff_enabled_changed)
        linters_group.add(ruff_row)

        # Mypy toggle
        mypy_row = Adw.SwitchRow()
        mypy_row.set_title("Mypy")
        mypy_row.set_subtitle("Static type checker for Python")
        mypy_row.set_active(self.settings.get("linters.mypy_enabled", True))
        mypy_row.connect("notify::active", self._on_mypy_enabled_changed)
        linters_group.add(mypy_row)

        page.add(linters_group)

        # Filters group
        filters_group = Adw.PreferencesGroup()
        filters_group.set_title("Filters")
        filters_group.set_description("Ignore specific error codes (comma-separated)")

        # Ignored codes
        ignored_row = Adw.EntryRow()
        ignored_row.set_title("Ignored Codes")
        current_ignored = self.settings.get("linters.ignored_codes", "")
        ignored_row.set_text(current_ignored)
        ignored_row.connect("changed", self._on_ignored_codes_changed)
        filters_group.add(ignored_row)

        # Help text
        help_label = Gtk.Label()
        help_label.set_markup(
            '<span size="small" alpha="60%">'
            'Examples: import-untyped, E402, F401\n'
            'Use ruff codes (E*, F*, W*) and mypy codes (import-untyped, arg-type, etc.)'
            '</span>'
        )
        help_label.set_xalign(0)
        help_label.set_margin_start(12)
        help_label.set_margin_top(4)
        help_label.set_wrap(True)
        filters_group.add(help_label)

        page.add(filters_group)
        self.add(page)

    # Signal handlers

    def _on_theme_changed(self, row, pspec):
        """Handle theme selection change."""
        selected = row.get_selected()
        theme_map = {0: "system", 1: "light", 2: "dark"}
        self.settings.set("appearance.theme", theme_map.get(selected, "system"))

    def _on_scheme_changed(self, row, pspec):
        """Handle syntax scheme selection change."""
        selected = row.get_selected()
        if 0 <= selected < len(self._scheme_ids):
            self.settings.set("appearance.syntax_scheme", self._scheme_ids[selected])

    def _on_font_family_changed(self, row):
        """Handle font family change."""
        self.settings.set("editor.font_family", row.get_text())

    def _on_font_size_changed(self, row, pspec):
        """Handle font size change."""
        self.settings.set("editor.font_size", int(row.get_value()))

    def _on_line_height_changed(self, row, pspec):
        """Handle line height change."""
        self.settings.set("editor.line_height", round(row.get_value(), 1))

    def _on_tab_size_changed(self, row, pspec):
        """Handle tab size change."""
        self.settings.set("editor.tab_size", int(row.get_value()))

    def _on_insert_spaces_changed(self, row, pspec):
        """Handle insert spaces toggle."""
        self.settings.set("editor.insert_spaces", row.get_active())

    def _on_word_wrap_changed(self, row, pspec):
        """Handle word wrap toggle."""
        self.settings.set("editor.word_wrap", row.get_active())

    def _on_ruff_enabled_changed(self, row, pspec):
        """Handle ruff enabled toggle."""
        self.settings.set("linters.ruff_enabled", row.get_active())

    def _on_mypy_enabled_changed(self, row, pspec):
        """Handle mypy enabled toggle."""
        self.settings.set("linters.mypy_enabled", row.get_active())

    def _on_ignored_codes_changed(self, row):
        """Handle ignored codes change."""
        self.settings.set("linters.ignored_codes", row.get_text())
