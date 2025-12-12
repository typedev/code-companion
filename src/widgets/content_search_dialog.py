"""Content search dialog with find and replace."""

import subprocess
import shutil
import re
from pathlib import Path
from dataclasses import dataclass

from gi.repository import Gtk, Adw, GObject, GLib


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


class ContentSearchDialog:
    """Dialog for searching content across project files with replace support."""

    def __init__(self, project_path: Path, on_open_file: callable):
        self.project_path = project_path
        self.on_open_file = on_open_file
        self._results: list[FileMatches] = []

    def present(self, parent):
        """Show the search dialog."""
        self.dialog = Adw.AlertDialog()
        self.dialog.set_heading("Find in Files")
        self.dialog.set_body_use_markup(False)

        # Build content
        content = self._build_content()
        self.dialog.set_extra_child(content)

        self.dialog.add_response("close", "Close")
        self.dialog.set_default_response("close")

        self.dialog.present(parent)

        # Focus search entry
        GLib.idle_add(lambda: self.search_entry.grab_focus() or False)

    def _build_content(self) -> Gtk.Widget:
        """Build dialog content."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)

        # Search entry (use SearchEntry like FileSearchDialog)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search pattern...")
        self.search_entry.connect("activate", self._on_search)
        self.search_entry.set_key_capture_widget(None)  # Don't capture keys globally
        box.append(self.search_entry)

        # Replace entry
        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_placeholder_text("Replace with...")
        box.append(self.replace_entry)

        # Options
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        self.regex_check = Gtk.CheckButton(label="Regex")
        options_box.append(self.regex_check)

        self.case_check = Gtk.CheckButton(label="Match Case")
        options_box.append(self.case_check)

        self.word_check = Gtk.CheckButton(label="Whole Word")
        options_box.append(self.word_check)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        options_box.append(spacer)

        search_btn = Gtk.Button(label="Search")
        search_btn.add_css_class("suggested-action")
        search_btn.connect("clicked", self._on_search)
        options_box.append(search_btn)

        box.append(options_box)

        # Filters
        filters_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        filters_box.append(Gtk.Label(label="Include:"))
        self.include_entry = Gtk.Entry()
        self.include_entry.set_text("*.py, *.js")
        self.include_entry.set_hexpand(True)
        filters_box.append(self.include_entry)

        filters_box.append(Gtk.Label(label="Exclude:"))
        self.exclude_entry = Gtk.Entry()
        self.exclude_entry.set_text("node_modules, .venv")
        self.exclude_entry.set_hexpand(True)
        filters_box.append(self.exclude_entry)

        box.append(filters_box)

        # Results
        self.results_label = Gtk.Label(label="Enter search pattern and click Search")
        self.results_label.set_xalign(0)
        self.results_label.add_css_class("dim-label")
        self.results_label.set_margin_top(8)
        box.append(self.results_label)

        # Results list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(200)
        scrolled.set_max_content_height(300)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.results_list = Gtk.ListBox()
        self.results_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.results_list.add_css_class("boxed-list")
        scrolled.set_child(self.results_list)
        box.append(scrolled)

        # Replace button
        self.replace_btn = Gtk.Button(label="Replace All")
        self.replace_btn.add_css_class("destructive-action")
        self.replace_btn.set_sensitive(False)
        self.replace_btn.connect("clicked", self._on_replace_all)
        box.append(self.replace_btn)

        return box

    def _on_search(self, *args):
        """Perform search."""
        query = self.search_entry.get_text().strip()
        if not query:
            return

        self._clear_results()
        self.results_label.set_text("Searching...")

        # Find search tool (ripgrep or grep)
        rg_path = shutil.which("rg")

        if rg_path:
            cmd = self._build_rg_command(query)
        else:
            cmd = self._build_grep_command(query)

        # Run search
        try:
            result = subprocess.run(
                cmd, cwd=self.project_path,
                capture_output=True, text=True, timeout=30
            )
            self._process_results(result.stdout, query)
        except FileNotFoundError:
            self.results_label.set_text("Error: grep not found. Install ripgrep for better search.")
        except Exception as e:
            self.results_label.set_text(f"Error: {e}")

    def _build_rg_command(self, query: str) -> list[str]:
        """Build ripgrep command."""
        cmd = ["rg", "--line-number", "--no-heading"]

        if not self.case_check.get_active():
            cmd.append("--ignore-case")
        if self.word_check.get_active():
            cmd.append("--word-regexp")
        if not self.regex_check.get_active():
            cmd.append("--fixed-strings")

        # Include patterns
        for pat in self.include_entry.get_text().split(","):
            pat = pat.strip()
            if pat:
                cmd.extend(["--glob", pat])

        # Exclude patterns
        for pat in self.exclude_entry.get_text().split(","):
            pat = pat.strip()
            if pat:
                cmd.extend(["--glob", f"!{pat}"])

        cmd.extend(["--glob", "!.git", query, "."])
        return cmd

    def _build_grep_command(self, query: str) -> list[str]:
        """Build grep command as fallback."""
        cmd = ["grep", "-rn", "--include=*"]

        if not self.case_check.get_active():
            cmd.append("-i")
        if self.word_check.get_active():
            cmd.append("-w")
        if not self.regex_check.get_active():
            cmd.append("-F")

        # Include patterns (grep uses --include)
        include_patterns = []
        for pat in self.include_entry.get_text().split(","):
            pat = pat.strip()
            if pat:
                include_patterns.append(f"--include={pat}")

        if include_patterns:
            # Replace default --include=*
            cmd = ["grep", "-rn"] + include_patterns
            if not self.case_check.get_active():
                cmd.append("-i")
            if self.word_check.get_active():
                cmd.append("-w")
            if not self.regex_check.get_active():
                cmd.append("-F")

        # Exclude patterns
        for pat in self.exclude_entry.get_text().split(","):
            pat = pat.strip()
            if pat:
                cmd.append(f"--exclude-dir={pat}")

        cmd.extend(["--exclude-dir=.git", query, "."])
        return cmd

    def _process_results(self, output: str, query: str):
        """Process search results."""
        if not output.strip():
            self.results_label.set_text("No results found")
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
        self.results_label.set_text(f"{total} results in {len(self._results)} files")
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

    def _display_results(self, query: str):
        """Display results in list."""
        for fm in self._results:
            # File header
            file_row = Gtk.ListBoxRow()
            file_row.set_selectable(False)

            file_box = Gtk.Box(spacing=8)
            file_box.set_margin_start(8)
            file_box.set_margin_top(6)
            file_box.set_margin_bottom(4)

            try:
                rel = str(fm.file_path.relative_to(self.project_path))
            except:
                rel = str(fm.file_path)

            file_label = Gtk.Label(label=f"📄 {rel} ({len(fm.matches)})")
            file_label.set_xalign(0)
            file_label.add_css_class("heading")
            file_box.append(file_label)
            file_row.set_child(file_box)
            self.results_list.append(file_row)

            # Matches (limit to 5 per file)
            for match in fm.matches[:5]:
                match_row = Gtk.ListBoxRow()
                match_row.set_activatable(True)
                match_row.match = match

                match_box = Gtk.Box(spacing=8)
                match_box.set_margin_start(24)
                match_box.set_margin_top(2)
                match_box.set_margin_bottom(2)

                line_label = Gtk.Label(label=f"{match.line_number}:")
                line_label.set_width_chars(5)
                line_label.add_css_class("dim-label")
                match_box.append(line_label)

                content = match.line_content.strip()[:80]
                content_label = Gtk.Label(label=content)
                content_label.set_xalign(0)
                content_label.set_ellipsize(2)
                match_box.append(content_label)

                match_row.set_child(match_box)
                match_row.connect("activate", self._on_match_click)
                self.results_list.append(match_row)

    def _on_match_click(self, row):
        """Open file at line."""
        if hasattr(row, "match"):
            self.on_open_file(str(row.match.file_path), row.match.line_number)

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
                if self.regex_check.get_active():
                    flags = 0 if self.case_check.get_active() else re.IGNORECASE
                    new_content, n = re.subn(search, replace, content, flags=flags)
                else:
                    if self.case_check.get_active():
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

        self.results_label.set_text(f"Replaced {count} occurrences")
        self._clear_results()
        self.replace_btn.set_sensitive(False)
