"""Query Editor widget with Markdown highlighting and spell checking."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, GObject

# libspelling (Spelling-1 typelib) is optional: on systems where it isn't
# installed the editor still works, just without spell checking.
try:
    gi.require_version("Spelling", "1")
    from gi.repository import Spelling

    HAS_SPELLING = True
except (ImportError, ValueError):
    Spelling = None
    HAS_SPELLING = False

from ..services import SettingsService
from .snippets_bar import SnippetsBar


# Preferred canonical locale per base language for the simplified selector.
_PREFERRED_LOCALES = {
    "en": "en_US", "ru": "ru_RU", "de": "de_DE", "fr": "fr_FR",
    "es": "es_ES", "it": "it_IT", "pt": "pt_BR", "uk": "uk_UA",
    "pl": "pl_PL", "nl": "nl_NL", "cs": "cs_CZ", "sv": "sv_SE",
}


def _simple_language_name(full_name: str) -> str:
    """'English (United States)' -> 'English'."""
    return full_name.split(" (")[0].strip()


def list_simple_languages() -> list[tuple[str, str]]:
    """Return [(canonical_code, simple_name)] with one entry per base language.

    The raw spell-check list contains dozens of regional variants (e.g. 24
    English locales); this collapses them to one entry per language.

    Returns an empty list when libspelling is unavailable.
    """
    if not HAS_SPELLING:
        return []

    provider = Spelling.Provider.get_default()
    by_base: dict[str, list] = {}
    for lang in provider.list_languages():
        base = lang.get_code().split("_")[0]
        by_base.setdefault(base, []).append(lang)

    result: list[tuple[str, str]] = []
    for base, langs in by_base.items():
        preferred = _PREFERRED_LOCALES.get(base)
        chosen = next((lang for lang in langs if lang.get_code() == preferred), langs[0])
        result.append((chosen.get_code(), _simple_language_name(chosen.get_name())))

    result.sort(key=lambda item: item[1])
    return result


def language_name_for_code(code: str) -> str | None:
    """Simple language name ('English') for a spell-check code.

    Returns None for 'auto' or an unknown code.
    """
    if not code or code == "auto":
        return None
    base = code.split("_")[0]
    for c, name in list_simple_languages():
        if c.split("_")[0] == base:
            return name
    return None


class QueryEditor(Gtk.Box):
    """Collapsible multi-line editor for composing queries with spell checking."""

    __gsignals__ = {
        "send-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Ask Claude to formulate + create a GitHub issue from the editor text
        # (or, if empty, from the ongoing discussion). Carries the editor text.
        "make-issue-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # A header snippet was clicked while the editor is collapsed — the text
        # goes to the Claude terminal instead of the (hidden) buffer.
        "snippet-to-terminal": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
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

        # Snippet buttons on the left (always reachable — the header stays
        # visible while the revealer hides the editor itself).
        self.snippets_bar = SnippetsBar()
        self.snippets_bar.connect("snippet-clicked", self._on_snippet_clicked)
        header.append(self.snippets_bar)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        # Toggle button with arrow, next to the language selector
        self.toggle_btn = Gtk.Button()
        self.toggle_btn.add_css_class("flat")
        self._update_toggle_button()
        self.toggle_btn.connect("clicked", self._on_toggle_clicked)
        header.append(self.toggle_btn)

        # Language selector button
        self._build_language_selector(header)

        self.append(header)

    def _build_language_selector(self, header: Gtk.Box):
        """Build a simplified language selector (one entry per language)."""
        menu = Gtk.StringList()
        menu.append("Auto")
        self._language_codes = ["auto"]

        for code, name in list_simple_languages():
            menu.append(name)
            self._language_codes.append(code)

        # Dropdown
        self.lang_dropdown = Gtk.DropDown(model=menu)
        self.lang_dropdown.set_tooltip_text("Language (spell check + Claude replies)")
        self.lang_dropdown.add_css_class("flat")

        # Load saved language, matching by base code (e.g. saved 'en_GB' -> 'English').
        saved_lang = self.settings.get("editor.spellcheck_language", "auto")
        saved_base = "auto" if saved_lang == "auto" else saved_lang.split("_")[0]
        selected_idx = 0
        for idx, code in enumerate(self._language_codes):
            code_base = "auto" if code == "auto" else code.split("_")[0]
            if code_base == saved_base:
                selected_idx = idx
                break
        self.lang_dropdown.set_selected(selected_idx)

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

        # Make Issue button — let Claude formulate + create a GitHub issue
        make_issue_btn = Gtk.Button(label="Make Issue")
        make_issue_btn.add_css_class("flat")
        make_issue_btn.set_tooltip_text(
            "Ask Claude to format this text as a GitHub issue and create it"
        )
        make_issue_btn.connect("clicked", self._on_make_issue_clicked)
        button_bar.append(make_issue_btn)

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
        """Set up spell checking with libspelling (no-op if unavailable)."""
        self.checker = None
        self.spell_adapter = None
        if not HAS_SPELLING:
            return

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
        if not self.spell_adapter:
            return

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

    def _on_snippet_clicked(self, snippets_bar, text: str):
        """Contextual snippet insert: expanded -> at cursor, collapsed -> terminal."""
        if self._expanded:
            self.buffer.insert_at_cursor(text)
            self.source_view.grab_focus()
        else:
            self.emit("snippet-to-terminal", text)

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

    def _on_make_issue_clicked(self, button):
        """Handle Make Issue button click - format editor text as a GitHub issue."""
        text = self.get_text()
        if text.strip():
            self.emit("make-issue-requested", text)

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
