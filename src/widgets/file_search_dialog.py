"""File search dialog with fuzzy matching."""

from pathlib import Path

from gi.repository import Gtk, Adw, GObject, Gdk, GLib


class FileSearchDialog(Adw.Dialog):
    """Dialog for quick file search with fuzzy matching."""

    __gsignals__ = {
        "file-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, project_path: Path, file_list: list[Path]):
        super().__init__()

        self.project_path = project_path
        self.file_list = file_list
        self._filtered_files: list[Path] = []

        self.set_title("Go to File")
        self.set_content_width(500)
        self.set_content_height(400)

        self._build_ui()
        self._update_results("")

    def _build_ui(self):
        """Build the dialog UI."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Type to search files...")
        self.search_entry.set_margin_start(12)
        self.search_entry.set_margin_end(12)
        self.search_entry.set_margin_top(12)
        self.search_entry.set_margin_bottom(8)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_activate)
        box.append(self.search_entry)

        # Key controller for navigation
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.search_entry.add_controller(key_controller)

        # Results list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_margin_start(12)
        scrolled.set_margin_end(12)
        scrolled.set_margin_bottom(12)

        self.results_list = Gtk.ListBox()
        self.results_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.results_list.add_css_class("boxed-list")
        self.results_list.connect("row-activated", self._on_row_activated)

        scrolled.set_child(self.results_list)
        box.append(scrolled)

        # Status label
        self.status_label = Gtk.Label()
        self.status_label.add_css_class("dim-label")
        self.status_label.set_margin_start(12)
        self.status_label.set_margin_end(12)
        self.status_label.set_margin_bottom(12)
        self.status_label.set_xalign(0)
        box.append(self.status_label)

        self.set_child(box)

    def _on_search_changed(self, entry):
        """Handle search text change."""
        query = entry.get_text().strip()
        self._update_results(query)

    def _update_results(self, query: str):
        """Update results based on search query."""
        # Clear existing results
        self.results_list.remove_all()

        if not query:
            # Show recent/all files when no query
            self._filtered_files = self.file_list[:50]
        else:
            # Fuzzy match
            self._filtered_files = self._fuzzy_search(query, limit=50)

        # Add results
        for file_path in self._filtered_files:
            row = self._create_result_row(file_path, query)
            self.results_list.append(row)

        # Select first result
        first_row = self.results_list.get_row_at_index(0)
        if first_row:
            self.results_list.select_row(first_row)

        # Update status
        total = len(self.file_list)
        shown = len(self._filtered_files)
        if query:
            self.status_label.set_text(f"Found {shown} of {total} files")
        else:
            self.status_label.set_text(f"{total} files in project")

    def _fuzzy_search(self, query: str, limit: int = 50) -> list[Path]:
        """Perform fuzzy search on file list."""
        query_lower = query.lower()
        results: list[tuple[int, Path]] = []

        for file_path in self.file_list:
            # Get relative path for matching
            try:
                rel_path = str(file_path.relative_to(self.project_path))
            except ValueError:
                rel_path = str(file_path)

            score = self._fuzzy_score(query_lower, rel_path.lower())
            if score > 0:
                results.append((score, file_path))

        # Sort by score (descending)
        results.sort(key=lambda x: x[0], reverse=True)

        return [path for _, path in results[:limit]]

    def _fuzzy_score(self, query: str, text: str) -> int:
        """Calculate fuzzy match score.

        Higher score = better match. 0 = no match.

        Scoring:
        - Exact filename match: +100
        - Filename contains query: +50
        - Path contains query: +20
        - Sequential character match: +10 per char
        - Non-sequential match: +1 per char
        """
        if not query:
            return 1

        filename = Path(text).name.lower()
        score = 0

        # Exact filename match
        if filename == query:
            return 1000

        # Filename starts with query
        if filename.startswith(query):
            score += 100

        # Filename contains query
        if query in filename:
            score += 50

        # Path contains query
        if query in text:
            score += 20

        # Character-by-character fuzzy match
        query_idx = 0
        consecutive = 0
        last_match_idx = -2

        for i, char in enumerate(text):
            if query_idx < len(query) and char == query[query_idx]:
                if i == last_match_idx + 1:
                    consecutive += 1
                    score += 10 + consecutive * 2  # Bonus for consecutive matches
                else:
                    consecutive = 0
                    score += 1

                last_match_idx = i
                query_idx += 1

        # All characters matched?
        if query_idx == len(query):
            score += 10
        else:
            return 0  # Not all characters found

        return score

    def _create_result_row(self, file_path: Path, query: str) -> Gtk.ListBoxRow:
        """Create a row for a search result."""
        row = Gtk.ListBoxRow()
        row.file_path = file_path

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # Filename
        name_label = Gtk.Label(label=file_path.name)
        name_label.set_xalign(0)
        name_label.add_css_class("heading")
        box.append(name_label)

        # Relative path
        try:
            rel_path = str(file_path.relative_to(self.project_path).parent)
            if rel_path == ".":
                rel_path = ""
        except ValueError:
            rel_path = str(file_path.parent)

        if rel_path:
            path_label = Gtk.Label(label=rel_path)
            path_label.set_xalign(0)
            path_label.add_css_class("dim-label")
            path_label.set_ellipsize(2)  # MIDDLE
            box.append(path_label)

        row.set_child(box)
        return row

    def _on_row_activated(self, list_box, row):
        """Handle row activation."""
        if row and hasattr(row, "file_path"):
            self.emit("file-selected", str(row.file_path))
            self.close()

    def _on_activate(self, entry):
        """Handle Enter key in search entry."""
        selected_row = self.results_list.get_selected_row()
        if selected_row:
            self._on_row_activated(self.results_list, selected_row)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard navigation."""
        if keyval == Gdk.KEY_Down:
            self._select_next()
            return True
        elif keyval == Gdk.KEY_Up:
            self._select_prev()
            return True
        elif keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _select_next(self):
        """Select next row in results."""
        selected = self.results_list.get_selected_row()
        if selected:
            idx = selected.get_index()
            next_row = self.results_list.get_row_at_index(idx + 1)
            if next_row:
                self.results_list.select_row(next_row)
                next_row.grab_focus()

    def _select_prev(self):
        """Select previous row in results."""
        selected = self.results_list.get_selected_row()
        if selected:
            idx = selected.get_index()
            if idx > 0:
                prev_row = self.results_list.get_row_at_index(idx - 1)
                if prev_row:
                    self.results_list.select_row(prev_row)
                    prev_row.grab_focus()

    def present_dialog(self, parent):
        """Present the dialog and focus search entry."""
        self.present(parent)
        # Focus search entry after dialog is shown
        GLib.idle_add(self._focus_entry)

    def _focus_entry(self):
        """Focus the search entry."""
        self.search_entry.grab_focus()
        return False
