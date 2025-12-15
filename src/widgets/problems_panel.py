"""Problems panel widget for sidebar."""

from pathlib import Path

from gi.repository import Gtk, GLib, GObject, Gdk

from ..services import ProblemsService, FileProblems, ToastService, LinterStatus, SettingsService
from ..services.icon_cache import IconCache


class ProblemsPanel(Gtk.Box):
    """Panel displaying linter problems grouped by file."""

    __gsignals__ = {
        # Emitted when a file is selected: (file_path, FileProblems)
        "file-selected": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    def __init__(self, project_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self.service = ProblemsService(self.project_path)
        self._icon_cache = IconCache()
        self._problems: dict[str, FileProblems] = {}
        self._loading = False

        self._build_ui()
        self._setup_css()

    def _build_ui(self):
        """Build the panel UI."""
        # Header with title and refresh button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        # Title with count
        self.title_label = Gtk.Label(label="Problems")
        self.title_label.set_xalign(0)
        self.title_label.add_css_class("heading")
        self.title_label.set_hexpand(True)
        header_box.append(self.title_label)

        # Copy all button
        self.copy_btn = Gtk.Button()
        self.copy_btn.set_icon_name("edit-copy-symbolic")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.set_tooltip_text("Copy all problems")
        self.copy_btn.set_sensitive(False)
        self.copy_btn.connect("clicked", self._on_copy_all_clicked)
        header_box.append(self.copy_btn)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Refresh (re-run linters)")
        refresh_btn.connect("clicked", self._on_refresh_clicked)
        header_box.append(refresh_btn)

        self.append(header_box)

        # Summary labels
        summary_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        summary_box.set_margin_start(12)
        summary_box.set_margin_end(12)
        summary_box.set_margin_bottom(8)

        self.error_label = Gtk.Label(label="0 errors")
        self.error_label.add_css_class("dim-label")
        self.error_label.add_css_class("caption")
        summary_box.append(self.error_label)

        self.warning_label = Gtk.Label(label="0 warnings")
        self.warning_label.add_css_class("dim-label")
        self.warning_label.add_css_class("caption")
        summary_box.append(self.warning_label)

        self.append(summary_box)

        # Spinner for loading
        self.spinner = Gtk.Spinner()
        self.spinner.set_margin_top(24)
        self.spinner.set_margin_bottom(24)
        self.spinner.set_visible(False)
        self.append(self.spinner)

        # File list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.file_list = Gtk.ListBox()
        self.file_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.file_list.add_css_class("navigation-sidebar")
        self.file_list.connect("row-activated", self._on_row_activated)
        scrolled.set_child(self.file_list)

        self.append(scrolled)

        # Empty state
        self.empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.empty_box.set_margin_start(12)
        self.empty_box.set_margin_end(12)
        self.empty_box.set_margin_top(24)
        self.empty_box.set_margin_bottom(24)
        self.empty_box.set_valign(Gtk.Align.CENTER)
        self.empty_box.set_visible(False)

        empty_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        empty_icon.set_pixel_size(48)
        empty_icon.add_css_class("dim-label")
        self.empty_box.append(empty_icon)

        empty_label = Gtk.Label(label="No problems found")
        empty_label.add_css_class("dim-label")
        self.empty_box.append(empty_label)

        hint_label = Gtk.Label(label="Click Refresh to run linters")
        hint_label.add_css_class("dim-label")
        hint_label.add_css_class("caption")
        self.empty_box.append(hint_label)

        self.append(self.empty_box)

        # Linter status box (shown when linters are missing)
        self.linter_status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.linter_status_box.set_margin_start(12)
        self.linter_status_box.set_margin_end(12)
        self.linter_status_box.set_margin_top(8)
        self.linter_status_box.set_visible(False)
        self.append(self.linter_status_box)

    def _setup_css(self):
        """Setup CSS styles."""
        css = """
        .problem-error {
            color: @error_color;
        }
        .problem-warning {
            color: @warning_color;
        }
        .problem-count {
            font-size: 0.85em;
            padding: 2px 6px;
            border-radius: 4px;
            background-color: alpha(@theme_fg_color, 0.1);
        }
        .problem-count-error {
            background-color: alpha(@error_color, 0.2);
            color: @error_color;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _on_refresh_clicked(self, button):
        """Handle refresh button click."""
        self.refresh()

    def _on_copy_all_clicked(self, button):
        """Copy all problems to clipboard."""
        text = self.get_all_problems_text()
        if text:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(text)
            ToastService.show("Copied all problems to clipboard")

    def refresh(self):
        """Refresh problems by running linters in background."""
        if self._loading:
            return

        self._loading = True
        self.spinner.set_visible(True)
        self.spinner.start()
        self.file_list.set_visible(False)
        self.empty_box.set_visible(False)

        # Run linters in background thread
        import threading
        thread = threading.Thread(target=self._run_linters_background, daemon=True)
        thread.start()

    def _run_linters_background(self):
        """Run linters in background thread."""
        try:
            problems = self.service.get_all_problems()
            GLib.idle_add(self._on_linters_done, problems)
        except Exception as e:
            GLib.idle_add(self._on_linters_error, str(e))

    def _on_linters_done(self, problems: dict[str, FileProblems]):
        """Handle linters completion on main thread."""
        self._loading = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._problems = problems
        self._update_ui()

    def _on_linters_error(self, error: str):
        """Handle linters error on main thread."""
        self._loading = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        ToastService.show_error(f"Linter error: {error}")
        self._update_ui()

    def _update_ui(self):
        """Update UI with current problems."""
        # Clear existing items
        while True:
            row = self.file_list.get_row_at_index(0)
            if row is None:
                break
            self.file_list.remove(row)

        # Calculate totals
        total_errors = sum(fp.error_count for fp in self._problems.values())
        total_warnings = sum(fp.warning_count for fp in self._problems.values())

        # Update title
        total = total_errors + total_warnings
        if total > 0:
            self.title_label.set_label(f"Problems ({total})")
        else:
            self.title_label.set_label("Problems")

        # Update summary
        self.error_label.set_label(f"{total_errors} errors")
        self.warning_label.set_label(f"{total_warnings} warnings")

        # Enable/disable copy button
        self.copy_btn.set_sensitive(total > 0)

        # Update linter status
        self._update_linter_status()

        # Show empty state or file list
        if not self._problems:
            self.empty_box.set_visible(True)
            self.file_list.set_visible(False)
            return

        self.empty_box.set_visible(False)
        self.file_list.set_visible(True)

        # Sort files by error count (most errors first)
        sorted_files = sorted(
            self._problems.items(),
            key=lambda x: (-x[1].error_count, -x[1].warning_count, x[0])
        )

        # Add file rows
        for file_path, fp in sorted_files:
            row = self._create_file_row(file_path, fp)
            self.file_list.append(row)

    def _update_linter_status(self):
        """Update linter status display."""
        # Clear existing status
        while child := self.linter_status_box.get_first_child():
            self.linter_status_box.remove(child)

        settings = SettingsService.get_instance()
        missing_linters = []

        # Only show missing if linter is enabled in settings
        if settings.get("linters.ruff_enabled", True):
            if self.service.ruff_status == LinterStatus.NOT_INSTALLED:
                missing_linters.append("ruff")

        if settings.get("linters.mypy_enabled", True):
            if self.service.mypy_status == LinterStatus.NOT_INSTALLED:
                missing_linters.append("mypy")

        if not missing_linters:
            self.linter_status_box.set_visible(False)
            return

        self.linter_status_box.set_visible(True)

        # Show missing linters with install buttons
        for linter in missing_linters:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            # Warning icon
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("warning")
            row.append(icon)

            # Label
            label = Gtk.Label(label=f"{linter} not installed")
            label.set_xalign(0)
            label.set_hexpand(True)
            row.append(label)

            # Install button
            install_btn = Gtk.Button(label="Install")
            install_btn.add_css_class("suggested-action")
            install_btn.add_css_class("pill")
            install_btn.connect("clicked", self._on_install_linter_clicked, linter)
            row.append(install_btn)

            self.linter_status_box.append(row)

        # Show which package manager will be used
        pkg_manager = "uv" if self.service.uses_uv() else "pip"
        hint = Gtk.Label(label=f"Will use {pkg_manager} to install")
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        hint.set_xalign(0)
        self.linter_status_box.append(hint)

    def _on_install_linter_clicked(self, button, linter: str):
        """Handle install linter button click."""
        button.set_sensitive(False)
        button.set_label("Installing...")

        # Run installation in background
        import threading

        def install():
            success, message = self.service.install_linter(linter)
            GLib.idle_add(self._on_install_complete, linter, success, message, button)

        thread = threading.Thread(target=install, daemon=True)
        thread.start()

    def _on_install_complete(self, linter: str, success: bool, message: str, button: Gtk.Button):
        """Handle installation completion."""
        if success:
            ToastService.show(message)
            # Refresh to rerun linters
            self.refresh()
        else:
            ToastService.show_error(f"Failed to install {linter}: {message}")
            button.set_sensitive(True)
            button.set_label("Install")

    def _create_file_row(self, file_path: str, fp: FileProblems) -> Gtk.ListBoxRow:
        """Create a row for a file with problems."""
        row = Gtk.ListBoxRow()
        row.file_path = file_path
        row.file_problems = fp

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # File icon
        path = Path(file_path)
        gicon = self._icon_cache.get_file_gicon(path)
        if gicon:
            icon = Gtk.Image.new_from_gicon(gicon)
        else:
            icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
        icon.set_pixel_size(16)
        box.append(icon)

        # File name
        name_label = Gtk.Label(label=path.name)
        name_label.set_xalign(0)
        name_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        name_label.set_hexpand(True)
        name_label.set_tooltip_text(file_path)
        box.append(name_label)

        # Problem count badge
        count_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        if fp.error_count > 0:
            error_badge = Gtk.Label(label=str(fp.error_count))
            error_badge.add_css_class("problem-count")
            error_badge.add_css_class("problem-count-error")
            count_box.append(error_badge)

        if fp.warning_count > 0:
            warning_badge = Gtk.Label(label=str(fp.warning_count))
            warning_badge.add_css_class("problem-count")
            count_box.append(warning_badge)

        box.append(count_box)

        row.set_child(box)
        return row

    def _on_row_activated(self, listbox, row):
        """Handle row activation."""
        if hasattr(row, "file_path") and hasattr(row, "file_problems"):
            self.emit("file-selected", row.file_path, row.file_problems)

    def load_if_needed(self):
        """Load problems if not already loaded (called on tab show)."""
        if not self._problems and not self._loading:
            self.refresh()

    def get_all_problems_text(self) -> str:
        """Get all problems as copyable text."""
        # Collect all problems and unique sources
        problem_lines = []
        sources = set()
        for file_path, fp in sorted(self._problems.items()):
            for p in fp.problems:
                problem_lines.append(p.format_full())
                sources.add(p.source)

        if not problem_lines:
            return ""

        # Build header with linter names
        linters = " and ".join(sorted(sources)) if sources else "linter"
        header = f"Please review and fix the following problems detected by {linters}:\n\n"

        return header + "\n".join(problem_lines)
