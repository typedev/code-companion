"""Code view widget with syntax highlighting using GtkSourceView."""

import gi

gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, Pango

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

        # Set up style scheme (Dracula to match terminal)
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = style_manager.get_scheme("dracula")
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
    """A widget for displaying diff between old and new text."""

    def __init__(self, old_text: str, new_text: str, file_path: str | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.old_text = old_text
        self.new_text = new_text
        self.file_path = file_path

        self._build_ui()

    def _build_ui(self):
        """Build the diff view UI."""
        # Old text (removed)
        if self.old_text:
            old_label = Gtk.Label(label="Removed:")
            old_label.set_xalign(0)
            old_label.add_css_class("dim-label")
            self.append(old_label)

            old_code = CodeView(
                self.old_text,
                file_path=self.file_path,
                show_line_numbers=False,
                max_lines=15,
            )
            self.append(old_code)

        # New text (added)
        if self.new_text:
            new_label = Gtk.Label(label="Added:")
            new_label.set_xalign(0)
            new_label.add_css_class("dim-label")
            self.append(new_label)

            new_code = CodeView(
                self.new_text,
                file_path=self.file_path,
                show_line_numbers=False,
                max_lines=15,
            )
            self.append(new_code)
