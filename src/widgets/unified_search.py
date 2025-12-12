"""Unified search widget for Files sidebar - searches both filenames and content."""

import subprocess
import shutil
import re
import threading
from pathlib import Path
from dataclasses import dataclass

from gi.repository import Gtk, GObject, GLib


@dataclass
class ContentMatch:
    """A single content search match."""
    file_path: Path
    line_number: int
    line_content: str


@dataclass
class FileContentMatches:
    """All content matches in a single file."""
    file_path: Path
    matches: list[ContentMatch]


class UnifiedSearch(Gtk.Box):
    """Unified search widget that searches both filenames and file contents."""

    __gsignals__ = {
        # path, line_number, search_term
        "open-file-at-line": (GObject.SignalFlags.RUN_FIRST, None, (str, int, str)),
        # path (for filename matches)
        "open-file": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, project_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self._content_results: list[FileContentMatches] = []
        self._file_results: list[Path] = []
        self._search_pending = False
        self._current_query = ""

        self._build_ui()
        self._setup_css()

    def _setup_css(self):
        """Set up CSS styles."""
        css = b"""
        .search-section-header {
            font-weight: bold;
            font-size: 0.9em;
        }
        .search-match-btn {
            padding: 2px 4px;
        }
        .search-match-btn:hover {
            background: alpha(@accent_color, 0.1);
        }
        .search-line-num {
            font-family: monospace;
            font-size: 0.85em;
            min-width: 3em;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        """Build the search UI."""
        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search files and content...")
        self.search_entry.set_margin_start(8)
        self.search_entry.set_margin_end(8)
        self.search_entry.set_margin_top(8)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_activate)
        self.append(self.search_entry)

        # Results container (hidden when empty)
        self.results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.results_box.set_visible(False)

        # Scrolled window for results
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_max_content_height(300)
        scrolled.set_propagate_natural_height(True)

        results_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Content results section
        self.content_expander = Gtk.Expander()
        self.content_expander.set_margin_start(8)
        self.content_expander.set_margin_end(8)
        self.content_expander.set_margin_top(4)
        self._build_content_header()

        self.content_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_expander.set_child(self.content_list)
        results_inner.append(self.content_expander)

        # Replace row (inside content section)
        self.replace_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.replace_box.set_margin_start(8)
        self.replace_box.set_margin_end(8)
        self.replace_box.set_margin_top(4)
        self.replace_box.set_visible(False)

        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_placeholder_text("Replace with...")
        self.replace_entry.set_hexpand(True)
        self.replace_box.append(self.replace_entry)

        self.replace_btn = Gtk.Button(label="Replace All")
        self.replace_btn.add_css_class("destructive-action")
        self.replace_btn.connect("clicked", self._on_replace_all)
        self.replace_box.append(self.replace_btn)

        results_inner.append(self.replace_box)

        # File results section
        self.files_expander = Gtk.Expander()
        self.files_expander.set_margin_start(8)
        self.files_expander.set_margin_end(8)
        self.files_expander.set_margin_top(4)
        self.files_expander.set_margin_bottom(4)
        self._build_files_header()

        self.files_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.files_expander.set_child(self.files_list)
        results_inner.append(self.files_expander)

        scrolled.set_child(results_inner)
        self.results_box.append(scrolled)

        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(4)
        self.results_box.append(separator)

        self.append(self.results_box)

    def _build_content_header(self):
        """Build content section header."""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.content_label = Gtk.Label(label="Content")
        self.content_label.add_css_class("search-section-header")
        header.append(self.content_label)

        self.content_count = Gtk.Label(label="")
        self.content_count.add_css_class("dim-label")
        header.append(self.content_count)

        self.content_expander.set_label_widget(header)
        self.content_expander.set_expanded(True)

    def _build_files_header(self):
        """Build files section header."""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.files_label = Gtk.Label(label="Files")
        self.files_label.add_css_class("search-section-header")
        header.append(self.files_label)

        self.files_count = Gtk.Label(label="")
        self.files_count.add_css_class("dim-label")
        header.append(self.files_count)

        self.files_expander.set_label_widget(header)
        self.files_expander.set_expanded(True)

    def _on_search_changed(self, entry):
        """Handle search text change - debounced search."""
        query = entry.get_text().strip()
        self._current_query = query

        if len(query) < 2:
            self._clear_results()
            self.results_box.set_visible(False)
            return

        # Debounce
        if not self._search_pending:
            self._search_pending = True
            GLib.timeout_add(250, lambda: self._delayed_search(query))

    def _on_search_activate(self, entry):
        """Handle Enter - immediate search."""
        query = entry.get_text().strip()
        if len(query) >= 2:
            self._do_search(query)

    def _delayed_search(self, query):
        """Perform delayed search."""
        self._search_pending = False
        current = self.search_entry.get_text().strip()
        if current == query and len(query) >= 2:
            self._do_search(query)
        return False

    def _do_search(self, query: str):
        """Perform both filename and content search."""
        self._clear_results()
        self.results_box.set_visible(True)
        self.content_count.set_label("searching...")
        self.files_count.set_label("searching...")

        # Run both searches in parallel
        def run_searches():
            # Content search
            content_results = self._search_content(query)
            # Filename search
            file_results = self._search_filenames(query)

            GLib.idle_add(lambda: self._display_results(content_results, file_results, query))

        thread = threading.Thread(target=run_searches, daemon=True)
        thread.start()

    def _search_content(self, query: str) -> list[FileContentMatches]:
        """Search file contents using ripgrep or grep."""
        rg_path = shutil.which("rg")

        if rg_path:
            cmd = [
                "rg", "--line-number", "--no-heading",
                "--max-count=50", "--ignore-case",
                "--glob", "!.git",
                "--glob", "*.py", "--glob", "*.js", "--glob", "*.ts",
                "--glob", "*.jsx", "--glob", "*.tsx", "--glob", "*.json",
                "--glob", "*.md", "--glob", "*.txt", "--glob", "*.yaml",
                "--glob", "*.yml", "--glob", "*.toml", "--glob", "*.html",
                "--glob", "*.css", "--glob", "*.scss",
                query, "."
            ]
        else:
            cmd = [
                "grep", "-rn", "-m", "50", "-i",
                "--include=*.py", "--include=*.js", "--include=*.ts",
                "--exclude-dir=.git", "--exclude-dir=node_modules",
                "--exclude-dir=.venv", "--exclude-dir=__pycache__",
                query, "."
            ]

        try:
            result = subprocess.run(
                cmd, cwd=self.project_path,
                capture_output=True, text=True, timeout=10
            )
            return self._parse_content_results(result.stdout)
        except Exception:
            return []

    def _parse_content_results(self, output: str) -> list[FileContentMatches]:
        """Parse grep/rg output into structured results."""
        if not output.strip():
            return []

        file_matches: dict[str, list[ContentMatch]] = {}

        for line in output.strip().split("\n"):
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue

            file_path = parts[0].lstrip("./")
            try:
                line_num = int(parts[1])
            except ValueError:
                continue
            content = parts[2]

            match = ContentMatch(
                file_path=self.project_path / file_path,
                line_number=line_num,
                line_content=content
            )

            if file_path not in file_matches:
                file_matches[file_path] = []
            file_matches[file_path].append(match)

        return [
            FileContentMatches(file_path=self.project_path / fp, matches=matches)
            for fp, matches in file_matches.items()
        ]

    def _search_filenames(self, query: str) -> list[Path]:
        """Search for files by name using find or fd."""
        fd_path = shutil.which("fd")

        if fd_path:
            cmd = ["fd", "--type", "f", "--ignore-case", query]
        else:
            cmd = ["find", ".", "-type", "f", "-iname", f"*{query}*",
                   "-not", "-path", "*/.git/*", "-not", "-path", "*/__pycache__/*"]

        try:
            result = subprocess.run(
                cmd, cwd=self.project_path,
                capture_output=True, text=True, timeout=5
            )
            files = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    path = line.lstrip("./")
                    files.append(self.project_path / path)
            return files[:20]  # Limit results
        except Exception:
            return []

    def _display_results(self, content_results: list[FileContentMatches],
                         file_results: list[Path], query: str):
        """Display search results."""
        self._content_results = content_results
        self._file_results = file_results

        # Content section
        self._clear_box(self.content_list)

        if content_results:
            total = sum(len(fm.matches) for fm in content_results)
            self.content_count.set_label(f"({total} in {len(content_results)} files)")
            self.content_expander.set_visible(True)
            self.replace_box.set_visible(True)

            for fm in content_results:
                self._add_content_file(fm, query)
        else:
            self.content_count.set_label("(no matches)")
            self.content_expander.set_visible(True)
            self.replace_box.set_visible(False)

        # Files section
        self._clear_box(self.files_list)

        if file_results:
            self.files_count.set_label(f"({len(file_results)})")
            self.files_expander.set_visible(True)

            for file_path in file_results:
                self._add_file_result(file_path)
        else:
            self.files_count.set_label("(no matches)")
            self.files_expander.set_visible(True)

    def _add_content_file(self, fm: FileContentMatches, query: str):
        """Add a file with content matches."""
        try:
            rel = str(fm.file_path.relative_to(self.project_path))
        except ValueError:
            rel = str(fm.file_path)

        # File header
        file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        file_box.set_margin_top(4)

        file_label = Gtk.Label(label=f"{rel} ({len(fm.matches)})")
        file_label.set_xalign(0)
        file_label.add_css_class("dim-label")
        file_label.set_margin_start(4)
        file_box.append(file_label)

        # Matches (limit to 5 per file)
        for match in fm.matches[:5]:
            match_btn = Gtk.Button()
            match_btn.add_css_class("flat")
            match_btn.add_css_class("search-match-btn")
            match_btn.match = match
            match_btn.query = query

            btn_box = Gtk.Box(spacing=6)
            btn_box.set_margin_start(8)

            line_label = Gtk.Label(label=f"{match.line_number}:")
            line_label.add_css_class("search-line-num")
            line_label.add_css_class("dim-label")
            line_label.set_xalign(1)
            btn_box.append(line_label)

            content = match.line_content.strip()[:50]
            content_label = Gtk.Label(label=content)
            content_label.set_xalign(0)
            content_label.set_ellipsize(2)
            btn_box.append(content_label)

            match_btn.set_child(btn_box)
            match_btn.connect("clicked", self._on_content_match_clicked)
            file_box.append(match_btn)

        if len(fm.matches) > 5:
            more_label = Gtk.Label(label=f"  +{len(fm.matches) - 5} more")
            more_label.add_css_class("dim-label")
            more_label.set_xalign(0)
            more_label.set_margin_start(12)
            file_box.append(more_label)

        self.content_list.append(file_box)

    def _add_file_result(self, file_path: Path):
        """Add a filename match."""
        try:
            rel = str(file_path.relative_to(self.project_path))
        except ValueError:
            rel = str(file_path)

        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("search-match-btn")
        btn.file_path = file_path

        label = Gtk.Label(label=rel)
        label.set_xalign(0)
        label.set_ellipsize(2)
        btn.set_child(label)
        btn.connect("clicked", self._on_file_match_clicked)

        self.files_list.append(btn)

    def _on_content_match_clicked(self, button):
        """Handle content match click."""
        match = button.match
        query = button.query
        self.emit("open-file-at-line", str(match.file_path), match.line_number, query)

    def _on_file_match_clicked(self, button):
        """Handle filename match click."""
        self.emit("open-file", str(button.file_path))

    def _on_replace_all(self, button):
        """Replace all content matches."""
        search = self._current_query
        replace = self.replace_entry.get_text()

        if not search or not self._content_results:
            return

        count = 0
        for fm in self._content_results:
            try:
                content = fm.file_path.read_text()
                pattern = re.compile(re.escape(search), re.IGNORECASE)
                new_content, n = pattern.subn(replace, content)
                if n > 0:
                    fm.file_path.write_text(new_content)
                    count += n
            except Exception as e:
                print(f"Error replacing in {fm.file_path}: {e}")

        # Re-search to update results
        if count > 0:
            GLib.timeout_add(200, lambda: self._do_search(self._current_query))

    def _clear_results(self):
        """Clear all results."""
        self._clear_box(self.content_list)
        self._clear_box(self.files_list)
        self._content_results = []
        self._file_results = []

    def _clear_box(self, box: Gtk.Box):
        """Clear all children from a box."""
        while True:
            child = box.get_first_child()
            if not child:
                break
            box.remove(child)

    def grab_focus(self):
        """Focus the search entry."""
        self.search_entry.grab_focus()

    def clear_and_hide(self):
        """Clear search and hide results."""
        self.search_entry.set_text("")
        self._clear_results()
        self.results_box.set_visible(False)
