"""Problems detail view widget showing problems list and code preview."""

from pathlib import Path

from gi.repository import Gtk, GObject, Gdk, GLib, GtkSource

from ..services import FileProblems, Problem, SettingsService


class ProblemsDetailView(Gtk.Box):
    """Widget for viewing file problems with code preview.

    Layout:
    - Top: Problems list with copy button
    - Bottom: Code preview with highlighted problem lines
    """

    def __init__(self, file_path: str, file_problems: FileProblems, project_path: Path):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.file_path = file_path
        self.file_problems = file_problems
        self.project_path = project_path
        self._selected_problem: Problem | None = None

        self._setup_css()
        self._build_ui()
        self._load_file()

    def _setup_css(self):
        """Set up CSS for the view."""
        css = b"""
        .problems-header {
            padding: 12px;
            background: alpha(@card_bg_color, 0.5);
        }
        .problem-row {
            padding: 6px 12px;
        }
        .problem-error {
            color: @error_color;
        }
        .problem-warning {
            color: @warning_color;
        }
        .problem-info {
            color: @accent_color;
        }
        .problem-location {
            font-family: monospace;
            color: @accent_color;
        }
        .problem-code {
            font-family: monospace;
            font-weight: bold;
        }
        .problem-message {
            opacity: 0.9;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        """Build the UI."""
        # Header
        header = self._build_header()
        self.append(header)

        # Main paned: problems list (top), code (bottom)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.paned.set_vexpand(True)

        # Problems list (top)
        problems_box = self._build_problems_list()
        self.paned.set_start_child(problems_box)
        self.paned.set_resize_start_child(True)
        self.paned.set_shrink_start_child(False)

        # Code view (bottom)
        code_box = self._build_code_view()
        self.paned.set_end_child(code_box)
        self.paned.set_resize_end_child(True)
        self.paned.set_shrink_end_child(False)

        # Set initial position
        self.paned.set_position(200)

        self.append(self.paned)

    def _build_header(self) -> Gtk.Box:
        """Build header with file name and copy button."""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("problems-header")

        # File name (save as attribute for updates)
        self.file_label = Gtk.Label(label=self.file_path)
        self.file_label.set_xalign(0)
        self.file_label.set_hexpand(True)
        self.file_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        header.append(self.file_label)

        # Problem count (save as attribute for updates)
        self.count_label = Gtk.Label()
        self.count_label.add_css_class("dim-label")
        self._update_count_label()
        header.append(self.count_label)

        # Copy all button
        copy_btn = Gtk.Button()
        copy_btn.set_icon_name("edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy all problems")
        copy_btn.connect("clicked", self._on_copy_all)
        header.append(copy_btn)

        return header

    def _update_count_label(self):
        """Update the problem count label."""
        error_count = self.file_problems.error_count
        warning_count = self.file_problems.warning_count
        count_text = []
        if error_count:
            count_text.append(f"{error_count} errors")
        if warning_count:
            count_text.append(f"{warning_count} warnings")
        self.count_label.set_label(" · ".join(count_text) if count_text else "No problems")

    def _build_problems_list(self) -> Gtk.Box:
        """Build the problems list."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(-1, 100)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.problems_list = Gtk.ListBox()
        self.problems_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.problems_list.connect("row-activated", self._on_problem_activated)

        # Add problem rows
        for problem in self.file_problems.problems:
            row = self._create_problem_row(problem)
            self.problems_list.append(row)

        scrolled.set_child(self.problems_list)
        box.append(scrolled)

        return box

    def _create_problem_row(self, problem: Problem) -> Gtk.ListBoxRow:
        """Create a row for a problem."""
        row = Gtk.ListBoxRow()
        row.problem = problem

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("problem-row")

        # Line:column
        location_label = Gtk.Label(label=f":{problem.line}")
        location_label.add_css_class("problem-location")
        location_label.set_size_request(50, -1)
        location_label.set_xalign(1)
        box.append(location_label)

        # Severity icon
        if problem.severity == "error":
            icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
            icon.add_css_class("problem-error")
        elif problem.severity == "warning":
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("problem-warning")
        else:
            icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
            icon.add_css_class("problem-info")
        icon.set_pixel_size(16)
        box.append(icon)

        # Code
        code_label = Gtk.Label(label=problem.code)
        code_label.add_css_class("problem-code")
        if problem.severity == "error":
            code_label.add_css_class("problem-error")
        elif problem.severity == "warning":
            code_label.add_css_class("problem-warning")
        box.append(code_label)

        # Message
        message_label = Gtk.Label(label=problem.message)
        message_label.add_css_class("problem-message")
        message_label.set_xalign(0)
        message_label.set_hexpand(True)
        message_label.set_ellipsize(3)
        message_label.set_tooltip_text(problem.message)
        box.append(message_label)

        # Source badge
        source_label = Gtk.Label(label=problem.source)
        source_label.add_css_class("dim-label")
        source_label.add_css_class("caption")
        box.append(source_label)

        # Copy button
        copy_btn = Gtk.Button()
        copy_btn.set_icon_name("edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy this problem")
        copy_btn.connect("clicked", lambda b: self._copy_problem(problem))
        box.append(copy_btn)

        row.set_child(box)
        return row

    def _build_code_view(self) -> Gtk.Box:
        """Build the code preview."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        # Source view
        self.source_buffer = GtkSource.Buffer()
        self.source_view = GtkSource.View(buffer=self.source_buffer)
        self.source_view.set_editable(False)
        self.source_view.set_show_line_numbers(True)
        self.source_view.set_monospace(True)
        self.source_view.set_tab_width(4)
        self.source_view.set_cursor_visible(False)

        # Apply settings
        settings = SettingsService.get_instance()

        # Syntax scheme
        scheme_id = settings.get("appearance.syntax_scheme", "Adwaita-dark")
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = scheme_manager.get_scheme(scheme_id)
        if scheme:
            self.source_buffer.set_style_scheme(scheme)

        # Font
        font_family = settings.get("editor.font_family", "Monospace")
        font_size = settings.get("editor.font_size", 12)
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(f"""
            textview {{
                font-family: "{font_family}";
                font-size: {font_size}pt;
            }}
        """.encode())
        self.source_view.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        scrolled.set_child(self.source_view)
        box.append(scrolled)

        return box

    def _load_file(self):
        """Load file content into code view."""
        full_path = self.project_path / self.file_path
        try:
            content = full_path.read_text()
            self.source_buffer.set_text(content)

            # Set language for syntax highlighting
            lang_manager = GtkSource.LanguageManager.get_default()
            language = lang_manager.guess_language(str(full_path), None)
            if language:
                self.source_buffer.set_language(language)

            # Create error line tags (check if they exist first)
            tag_table = self.source_buffer.get_tag_table()
            self._error_tag = tag_table.lookup("error-line")
            if not self._error_tag:
                self._error_tag = self.source_buffer.create_tag(
                    "error-line",
                    background="rgba(231, 76, 60, 0.2)"
                )
            self._warning_tag = tag_table.lookup("warning-line")
            if not self._warning_tag:
                self._warning_tag = self.source_buffer.create_tag(
                    "warning-line",
                    background="rgba(241, 196, 15, 0.2)"
                )

            # Mark all problem lines
            self._mark_problem_lines()

            # Select first problem if available
            if self.file_problems.problems:
                first_row = self.problems_list.get_row_at_index(0)
                if first_row:
                    self.problems_list.select_row(first_row)
                    self._scroll_to_problem(self.file_problems.problems[0])

        except Exception as e:
            self.source_buffer.set_text(f"Error loading file: {e}")

    def _mark_problem_lines(self):
        """Mark all problem lines with background color."""
        for problem in self.file_problems.problems:
            line = problem.line - 1  # 0-indexed
            if line < 0:
                continue

            # GTK4: get_iter_at_line returns (bool, TextIter)
            success, start_iter = self.source_buffer.get_iter_at_line(line)
            if not success:
                continue

            end_iter = start_iter.copy()
            end_iter.forward_to_line_end()

            tag = self._error_tag if problem.severity == "error" else self._warning_tag
            self.source_buffer.apply_tag(tag, start_iter, end_iter)

    def _on_problem_activated(self, listbox, row):
        """Handle problem selection."""
        if hasattr(row, "problem"):
            self._selected_problem = row.problem
            self._scroll_to_problem(row.problem)

    def _scroll_to_problem(self, problem: Problem):
        """Scroll code view to problem line."""
        line = problem.line - 1  # 0-indexed
        if line < 0:
            return

        # GTK4: get_iter_at_line returns (bool, TextIter)
        success, iter = self.source_buffer.get_iter_at_line(line)
        if success:
            self.source_view.scroll_to_iter(iter, 0.2, True, 0.0, 0.5)

    def _on_copy_all(self, button):
        """Copy all problems to clipboard."""
        problems = self.file_problems.problems
        if not problems:
            return

        # Get unique sources
        sources = set(p.source for p in problems)
        linters = " and ".join(sorted(sources)) if sources else "linter"
        header = f"Please review and fix the following problems detected by {linters}:\n\n"

        lines = [p.format_full() for p in problems]
        text = header + "\n".join(lines)
        self._copy_to_clipboard(text)

    def _copy_problem(self, problem: Problem):
        """Copy single problem to clipboard."""
        header = f"Please review and fix this problem detected by {problem.source}:\n\n"
        self._copy_to_clipboard(header + problem.format_full())

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

        from ..services import ToastService
        ToastService.show("Copied to clipboard")

    def update(self, file_path: str, file_problems: FileProblems):
        """Update view with new file."""
        self.file_path = file_path
        self.file_problems = file_problems

        # Update header
        self.file_label.set_label(file_path)
        self._update_count_label()

        # Clear and reload problems list
        while True:
            row = self.problems_list.get_row_at_index(0)
            if row is None:
                break
            self.problems_list.remove(row)

        for problem in self.file_problems.problems:
            row = self._create_problem_row(problem)
            self.problems_list.append(row)

        self._load_file()
