"""Search panel for sidebar - content search with find/replace."""

import subprocess
import shutil
import re
from pathlib import Path
from dataclasses import dataclass

from gi.repository import Gtk, GObject, GLib


@dataclass
class SearchMatch:
    """A single search match."""
    file_path: Path
    line_number: int
    line_content: str


@dataclass
class FileMatches:
    """All matches in a single file."""
    file_path: Path
    matches: list[SearchMatch]


class SearchPanel(Gtk.Box):
    """Search panel for finding and replacing text across project files."""

    __gsignals__ = {
        # path, line_number, search_term
        "open-file-at-line": (GObject.SignalFlags.RUN_FIRST, None, (str, int, str)),
    }

    def __init__(self, project_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self._results: list[FileMatches] = []
        self._refresh_pending = False

        self._build_ui()

    def _build_ui(self):
        """Build the panel UI."""
        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search in files...")
        self.search_entry.set_margin_start(8)
        self.search_entry.set_margin_end(8)
        self.search_entry.set_margin_top(8)
        self.search_entry.connect("activate", self._on_search)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.append(self.search_entry)

        # Replace entry
        replace_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        replace_box.set_margin_start(8)
        replace_box.set_margin_end(8)
        replace_box.set_margin_top(4)

        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_placeholder_text("Replace with...")
        self.replace_entry.set_hexpand(True)
        replace_box.append(self.replace_entry)

        self.replace_btn = Gtk.Button(icon_name="edit-find-replace-symbolic")
        self.replace_btn.set_tooltip_text("Replace All")
        self.replace_btn.set_sensitive(False)
        self.replace_btn.connect("clicked", self._on_replace_all)
        replace_box.append(self.replace_btn)

        self.append(replace_box)

        # Options row
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        options_box.set_margin_start(8)
        options_box.set_margin_end(8)
        options_box.set_margin_top(4)

        self.regex_btn = Gtk.ToggleButton(label=".*")
        self.regex_btn.set_tooltip_text("Use regex")
        self.regex_btn.add_css_class("flat")
        options_box.append(self.regex_btn)

        self.case_btn = Gtk.ToggleButton(label="Aa")
        self.case_btn.set_tooltip_text("Match case")
        self.case_btn.add_css_class("flat")
        options_box.append(self.case_btn)

        self.word_btn = Gtk.ToggleButton(label="W")
        self.word_btn.set_tooltip_text("Whole word")
        self.word_btn.add_css_class("flat")
        options_box.append(self.word_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        options_box.append(spacer)

        # File filter button with popover
        self.filter_btn = Gtk.MenuButton()
        self.filter_btn.set_icon_name("funnel-symbolic")
        self.filter_btn.set_tooltip_text("File filters")
        self.filter_btn.add_css_class("flat")
        self._setup_filter_popover()
        options_box.append(self.filter_btn)

        self.append(options_box)

        # Status label
        self.status_label = Gtk.Label(label="")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("dim-label")
        self.status_label.set_margin_start(8)
        self.status_label.set_margin_top(8)
        self.append(self.status_label)

        # Results list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_margin_start(8)
        scrolled.set_margin_end(8)
        scrolled.set_margin_top(4)
        scrolled.set_margin_bottom(8)

        self.results_list = Gtk.ListBox()
        self.results_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.results_list.add_css_class("boxed-list")
        scrolled.set_child(self.results_list)

        self.append(scrolled)

    def _setup_filter_popover(self):
        """Setup file filter popover."""
        popover = Gtk.Popover()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        # Include
        include_label = Gtk.Label(label="Include files:")
        include_label.set_xalign(0)
        box.append(include_label)

        self.include_entry = Gtk.Entry()
        self.include_entry.set_text("*.py, *.js, *.ts")
        self.include_entry.set_placeholder_text("*.py, *.js")
        box.append(self.include_entry)

        # Exclude
        exclude_label = Gtk.Label(label="Exclude:")
        exclude_label.set_xalign(0)
        exclude_label.set_margin_top(8)
        box.append(exclude_label)

        self.exclude_entry = Gtk.Entry()
        self.exclude_entry.set_text("node_modules, .venv, dist")
        self.exclude_entry.set_placeholder_text("node_modules, dist")
        box.append(self.exclude_entry)

        popover.set_child(box)
        self.filter_btn.set_popover(popover)

    def _on_search_changed(self, entry):
        """Handle search text change - debounced search."""
        query = entry.get_text().strip()
        if len(query) < 2:
            self._clear_results()
            self.status_label.set_text("")
            return

        # Debounce
        if not self._refresh_pending:
            self._refresh_pending = True
            GLib.timeout_add(300, lambda: self._delayed_search(query))

    def _delayed_search(self, query):
        """Perform delayed search."""
        self._refresh_pending = False
        current_query = self.search_entry.get_text().strip()
        if current_query == query and len(query) >= 2:
            self._do_search()
        return False

    def _on_search(self, entry):
        """Handle Enter - immediate search."""
        self._do_search()

    def _do_search(self):
        """Perform the search."""
        query = self.search_entry.get_text().strip()
        if not query:
            return

        self._clear_results()
        self.status_label.set_text("Searching...")

        # Find search tool
        rg_path = shutil.which("rg")
        if rg_path:
            cmd = self._build_rg_command(query)
        else:
            cmd = self._build_grep_command(query)

        # Run search in background
        def run_search():
            try:
                result = subprocess.run(
                    cmd, cwd=self.project_path,
                    capture_output=True, text=True, timeout=30
                )
                GLib.idle_add(lambda: self._process_results(result.stdout, query))
            except Exception as e:
                GLib.idle_add(lambda: self.status_label.set_text(f"Error: {e}"))

        import threading
        thread = threading.Thread(target=run_search, daemon=True)
        thread.start()

    def _build_rg_command(self, query: str) -> list[str]:
        """Build ripgrep command."""
        cmd = ["rg", "--line-number", "--no-heading", "--max-count=100"]

        if not self.case_btn.get_active():
            cmd.append("--ignore-case")
        if self.word_btn.get_active():
            cmd.append("--word-regexp")
        if not self.regex_btn.get_active():
            cmd.append("--fixed-strings")

        for pat in self.include_entry.get_text().split(","):
            pat = pat.strip()
            if pat:
                cmd.extend(["--glob", pat])

        for pat in self.exclude_entry.get_text().split(","):
            pat = pat.strip()
            if pat:
                cmd.extend(["--glob", f"!{pat}"])

        cmd.extend(["--glob", "!.git", query, "."])
        return cmd

    def _build_grep_command(self, query: str) -> list[str]:
        """Build grep command as fallback."""
        cmd = ["grep", "-rn", "-m", "100"]

        if not self.case_btn.get_active():
            cmd.append("-i")
        if self.word_btn.get_active():
            cmd.append("-w")
        if not self.regex_btn.get_active():
            cmd.append("-F")

        for pat in self.include_entry.get_text().split(","):
            pat = pat.strip()
            if pat:
                cmd.append(f"--include={pat}")

        for pat in self.exclude_entry.get_text().split(","):
            pat = pat.strip()
            if pat:
                cmd.append(f"--exclude-dir={pat}")

        cmd.extend(["--exclude-dir=.git", query, "."])
        return cmd

    def _process_results(self, output: str, query: str):
        """Process search results."""
        if not output.strip():
            self.status_label.set_text("No results")
            self.replace_btn.set_sensitive(False)
            return

        file_matches: dict[str, list[SearchMatch]] = {}

        for line in output.strip().split("\n"):
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue

            file_path = parts[0]
            try:
                line_num = int(parts[1])
            except ValueError:
                continue
            content = parts[2]

            match = SearchMatch(
                file_path=self.project_path / file_path,
                line_number=line_num,
                line_content=content
            )

            if file_path not in file_matches:
                file_matches[file_path] = []
            file_matches[file_path].append(match)

        self._results = [
            FileMatches(file_path=self.project_path / fp, matches=matches)
            for fp, matches in file_matches.items()
        ]

        total = sum(len(fm.matches) for fm in self._results)
        self.status_label.set_text(f"{total} results in {len(self._results)} files")
        self.replace_btn.set_sensitive(total > 0)

        self._display_results(query)

    def _clear_results(self):
        """Clear results list."""
        while True:
            row = self.results_list.get_row_at_index(0)
            if not row:
                break
            self.results_list.remove(row)
        self._results = []
        self.replace_btn.set_sensitive(False)

    def _display_results(self, query: str):
        """Display results."""
        for fm in self._results:
            # File header (expandable)
            expander = Gtk.Expander()
            expander.set_margin_start(4)
            expander.set_margin_top(4)
            expander.set_margin_bottom(2)

            try:
                rel = str(fm.file_path.relative_to(self.project_path))
            except:
                rel = str(fm.file_path)

            header = Gtk.Box(spacing=6)
            icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
            header.append(icon)
            header.append(Gtk.Label(label=f"{rel} ({len(fm.matches)})"))
            expander.set_label_widget(header)
            expander.set_expanded(True)

            # Matches inside expander
            matches_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

            for match in fm.matches[:10]:
                match_btn = Gtk.Button()
                match_btn.add_css_class("flat")
                match_btn.match = match

                btn_box = Gtk.Box(spacing=8)
                btn_box.set_margin_start(20)

                line_label = Gtk.Label(label=f"{match.line_number}:")
                line_label.set_width_chars(5)
                line_label.set_xalign(1)
                line_label.add_css_class("dim-label")
                btn_box.append(line_label)

                content = match.line_content.strip()[:60]
                content_label = Gtk.Label(label=content)
                content_label.set_xalign(0)
                content_label.set_ellipsize(2)
                btn_box.append(content_label)

                match_btn.set_child(btn_box)
                match_btn.connect("clicked", self._on_match_clicked)
                matches_box.append(match_btn)

            if len(fm.matches) > 10:
                more = Gtk.Label(label=f"  ... +{len(fm.matches) - 10} more")
                more.add_css_class("dim-label")
                more.set_xalign(0)
                more.set_margin_start(20)
                matches_box.append(more)

            expander.set_child(matches_box)

            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_child(expander)
            self.results_list.append(row)

    def _on_match_clicked(self, button):
        """Handle match click - open file at line."""
        if hasattr(button, "match"):
            match = button.match
            search_term = self.search_entry.get_text().strip()
            self.emit("open-file-at-line", str(match.file_path), match.line_number, search_term)

    def _on_replace_all(self, button):
        """Replace all matches."""
        search = self.search_entry.get_text().strip()
        replace = self.replace_entry.get_text()

        if not search or not self._results:
            return

        count = 0
        for fm in self._results:
            try:
                content = fm.file_path.read_text()
                if self.regex_btn.get_active():
                    flags = 0 if self.case_btn.get_active() else re.IGNORECASE
                    new_content, n = re.subn(search, replace, content, flags=flags)
                else:
                    if self.case_btn.get_active():
                        new_content = content.replace(search, replace)
                        n = content.count(search)
                    else:
                        pattern = re.compile(re.escape(search), re.IGNORECASE)
                        new_content, n = pattern.subn(replace, content)

                if n > 0:
                    fm.file_path.write_text(new_content)
                    count += n
            except Exception as e:
                print(f"Error replacing in {fm.file_path}: {e}")

        self.status_label.set_text(f"Replaced {count} occurrences")
        self._clear_results()

        # Re-run search to show updated results
        if self.search_entry.get_text().strip():
            GLib.timeout_add(500, self._do_search)
