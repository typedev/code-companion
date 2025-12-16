"""Code view widget with syntax highlighting using GtkSourceView."""

import gi

gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, Pango

from ..services import SettingsService

# File extension to language ID mapping
EXTENSION_LANGUAGES = {
    ".py": "python3",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "sh",
    ".bash": "sh",
    ".zsh": "sh",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".xml": "xml",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".lua": "lua",
    ".vim": "vim",
    ".dockerfile": "dockerfile",
    ".makefile": "makefile",
}


def get_language_for_file(file_path: str) -> str | None:
    """Get GtkSourceView language ID for a file path."""
    file_path_lower = file_path.lower()

    # Check exact filename matches first
    if file_path_lower.endswith("dockerfile"):
        return "dockerfile"
    if file_path_lower.endswith("makefile"):
        return "makefile"

    # Check extension
    for ext, lang in EXTENSION_LANGUAGES.items():
        if file_path_lower.endswith(ext):
            return lang

    return None


class CodeView(Gtk.Frame):
    """A widget for displaying code with syntax highlighting."""

    # Height constants
    LINE_HEIGHT = 18  # Approximate line height in pixels
    MIN_LINES = 3
    MAX_LINES = 20

    def __init__(
        self,
        code: str,
        language: str | None = None,
        file_path: str | None = None,
        show_line_numbers: bool = True,
        max_lines: int | None = None,
    ):
        super().__init__()

        self.code = code
        self.language = language
        self.file_path = file_path
        self.show_line_numbers = show_line_numbers
        self.max_lines = max_lines or self.MAX_LINES

        self._build_ui()

    def _build_ui(self):
        """Build the code view UI."""
        # Create source buffer and view
        self.buffer = GtkSource.Buffer()
        self.source_view = GtkSource.View(buffer=self.buffer)

        # Configure source view
        self.source_view.set_editable(False)
        self.source_view.set_cursor_visible(False)
        self.source_view.set_show_line_numbers(self.show_line_numbers)
        self.source_view.set_monospace(True)
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        # Set smaller font size and add top padding inside code block
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"textview { font-size: 11px; padding-top: 8px; padding-bottom: 4px; }")
        self.source_view.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Set up language highlighting
        lang_id = self.language
        if not lang_id and self.file_path:
            lang_id = get_language_for_file(self.file_path)

        if lang_id:
            lang_manager = GtkSource.LanguageManager.get_default()
            language = lang_manager.get_language(lang_id)
            if language:
                self.buffer.set_language(language)

        # Set up style scheme from settings
        settings = SettingsService.get_instance()
        scheme_id = settings.get("appearance.syntax_scheme", "Adwaita-dark")
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = style_manager.get_scheme(scheme_id)
        if not scheme:
            scheme = style_manager.get_scheme("Adwaita-dark")
        if not scheme:
            scheme = style_manager.get_scheme("classic")
        if scheme:
            self.buffer.set_style_scheme(scheme)

        # Set the code
        self.buffer.set_text(self.code)

        # Calculate height based on line count
        line_count = self.code.count('\n') + 1
        display_lines = max(self.MIN_LINES, min(line_count, self.max_lines))
        calculated_height = display_lines * self.LINE_HEIGHT + 16  # +16 for padding

        # Wrap in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Set height: if content fits, use natural height; otherwise limit
        if line_count <= self.max_lines:
            scrolled.set_min_content_height(calculated_height)
            scrolled.set_propagate_natural_height(True)
        else:
            scrolled.set_min_content_height(self.max_lines * self.LINE_HEIGHT + 16)
            scrolled.set_max_content_height(self.max_lines * self.LINE_HEIGHT + 16)

        scrolled.set_child(self.source_view)
        self.set_child(scrolled)

    def set_code(self, code: str):
        """Update the displayed code."""
        self.code = code
        self.buffer.set_text(code)


class DiffView(Gtk.Box):
    """A widget for displaying unified diff with +/- and color highlighting."""

    def __init__(self, old_text: str, new_text: str, file_path: str | None = None, raw_diff: str | None = None):
        """Create a diff view.

        Args:
            old_text: Original text (for generating diff)
            new_text: New text (for generating diff)
            file_path: File path for display
            raw_diff: Pre-generated unified diff text (if provided, old_text/new_text are ignored)
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.old_text = old_text
        self.new_text = new_text
        self.file_path = file_path
        self.raw_diff = raw_diff

        self._setup_css()
        self._build_ui()

    def _setup_css(self):
        """Set up CSS for diff colors."""
        settings = SettingsService.get_instance()
        font_family = settings.get("editor.font_family", "Monospace")
        font_size = settings.get("editor.font_size", 12)
        line_height = settings.get("editor.line_height", 1.4)

        css = f"""
        .diff-view {{
            font-family: "{font_family}";
            font-size: {font_size}pt;
            line-height: {line_height};
        }}
        .diff-added {{
            background-color: rgba(46, 204, 113, 0.2);
            color: #2ecc71;
        }}
        .diff-removed {{
            background-color: rgba(231, 76, 60, 0.2);
            color: #e74c3c;
        }}
        .diff-hunk {{
            background-color: rgba(52, 152, 219, 0.15);
            color: #3498db;
        }}
        .diff-context {{
            color: @theme_fg_color;
        }}
        """
        provider = Gtk.CssProvider()
        provider.load_from_string(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        """Build the diff view UI."""
        import difflib

        # Use raw diff if provided, otherwise generate from old/new text
        if self.raw_diff:
            diff = self.raw_diff.splitlines()
        else:
            # Generate unified diff
            old_lines = self.old_text.splitlines(keepends=True) if self.old_text else []
            new_lines = self.new_text.splitlines(keepends=True) if self.new_text else []

            diff = list(difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{self.file_path}" if self.file_path else "a/old",
                tofile=f"b/{self.file_path}" if self.file_path else "b/new",
                lineterm=""
            ))

        # Scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        if not diff:
            # No changes
            label = Gtk.Label(label="No differences")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            scrolled.set_child(label)
        else:
            # Create text view for diff
            text_view = Gtk.TextView()
            text_view.set_editable(False)
            text_view.set_cursor_visible(False)
            text_view.set_monospace(True)
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_view.add_css_class("diff-view")
            text_view.set_left_margin(12)
            text_view.set_right_margin(12)
            text_view.set_top_margin(8)
            text_view.set_bottom_margin(8)

            # Apply font settings
            self._apply_font_settings(text_view)

            buffer = text_view.get_buffer()

            # Create tags for highlighting
            tag_added = buffer.create_tag("added", background="rgba(46, 204, 113, 0.2)", foreground="#2ecc71")
            tag_removed = buffer.create_tag("removed", background="rgba(231, 76, 60, 0.2)", foreground="#e74c3c")
            tag_hunk = buffer.create_tag("hunk", background="rgba(52, 152, 219, 0.15)", foreground="#3498db")

            # Insert diff lines with tags
            for line in diff:
                line_clean = line.rstrip('\n')
                iter_end = buffer.get_end_iter()

                if line.startswith('+++') or line.startswith('---'):
                    # File headers
                    buffer.insert(iter_end, line_clean + "\n")
                elif line.startswith('@@'):
                    # Hunk header
                    start_offset = buffer.get_char_count()
                    buffer.insert(iter_end, line_clean + "\n")
                    start_iter = buffer.get_iter_at_offset(start_offset)
                    end_iter = buffer.get_end_iter()
                    buffer.apply_tag(tag_hunk, start_iter, end_iter)
                elif line.startswith('+'):
                    # Added line
                    start_offset = buffer.get_char_count()
                    buffer.insert(iter_end, line_clean + "\n")
                    start_iter = buffer.get_iter_at_offset(start_offset)
                    end_iter = buffer.get_end_iter()
                    buffer.apply_tag(tag_added, start_iter, end_iter)
                elif line.startswith('-'):
                    # Removed line
                    start_offset = buffer.get_char_count()
                    buffer.insert(iter_end, line_clean + "\n")
                    start_iter = buffer.get_iter_at_offset(start_offset)
                    end_iter = buffer.get_end_iter()
                    buffer.apply_tag(tag_removed, start_iter, end_iter)
                else:
                    # Context line
                    buffer.insert(iter_end, line_clean + "\n")

            scrolled.set_child(text_view)

        self.append(scrolled)

    def _apply_font_settings(self, text_view: Gtk.TextView):
        """Apply font settings from preferences to text view."""
        settings = SettingsService.get_instance()

        font_family = settings.get("editor.font_family", "Monospace")
        font_size = settings.get("editor.font_size", 12)
        line_height = settings.get("editor.line_height", 1.4)

        # Apply font via Pango
        font_desc = Pango.FontDescription.from_string(f"{font_family} {font_size}")
        text_view.modify_font(font_desc) if hasattr(text_view, 'modify_font') else None

        # Apply via CSS for better control
        css = f"""
        .diff-view {{
            font-family: "{font_family}";
            font-size: {font_size}pt;
            line-height: {line_height};
        }}
        """
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css.encode())
        text_view.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
