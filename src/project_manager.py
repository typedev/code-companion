"""Project Manager window for selecting and opening projects."""

import subprocess
import sys
from pathlib import Path

from gi.repository import Adw, Gtk, GLib, Gio

from .services.project_registry import ProjectRegistry
from .version import __version__, get_version_info


def escape_markup(text: str) -> str:
    """Escape text for safe use in GTK markup."""
    return GLib.markup_escape_text(text)


class ProjectManagerWindow(Adw.ApplicationWindow):
    """Window for managing and selecting projects."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.registry = ProjectRegistry()

        self._setup_window()
        self._build_ui()
        self._load_projects()

    def _setup_window(self):
        """Configure window properties."""
        self.set_title("Claude Companion")
        self.set_default_size(500, 600)

    def _build_ui(self):
        """Build the UI layout."""
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(self._create_title_widget())

        # About button
        about_button = Gtk.Button(icon_name="help-about-symbolic")
        about_button.set_tooltip_text("About")
        about_button.connect("clicked", self._on_about_clicked)
        header.pack_end(about_button)

        main_box.append(header)

        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        content_box.set_margin_top(16)
        content_box.set_margin_bottom(16)
        content_box.set_spacing(16)

        # Title
        title_label = Gtk.Label(label="Projects")
        title_label.add_css_class("title-1")
        title_label.set_xalign(0)
        content_box.append(title_label)

        # Project list in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.project_list = Gtk.ListBox()
        self.project_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.project_list.add_css_class("boxed-list")

        scrolled.set_child(self.project_list)
        content_box.append(scrolled)

        # Double-click gesture for opening projects
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(1)  # Left mouse button
        click_gesture.connect("released", self._on_list_double_click)
        self.project_list.add_controller(click_gesture)

        # Buttons row
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # Add project button
        add_button = Gtk.Button(label="Add Project...")
        add_button.add_css_class("suggested-action")
        add_button.set_hexpand(True)
        add_button.connect("clicked", self._on_add_project_clicked)
        buttons_box.append(add_button)

        # Remove project button
        self.remove_button = Gtk.Button(label="Remove")
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.set_sensitive(False)
        self.remove_button.connect("clicked", self._on_remove_project_clicked)
        buttons_box.append(self.remove_button)

        content_box.append(buttons_box)

        main_box.append(content_box)
        self.set_content(main_box)

        # Track selection for remove button
        self.project_list.connect("row-selected", self._on_selection_changed)

    def _load_projects(self):
        """Load projects from registry."""
        projects = self.registry.get_registered_projects()

        # Clear existing
        self.project_list.remove_all()

        if not projects:
            self._show_empty_state()
            return

        for project_path in projects:
            path = Path(project_path)
            if path.exists():
                row = self._create_project_row(path)
                self.project_list.append(row)

    def _show_empty_state(self):
        """Show empty state message."""
        label = Gtk.Label(label="No projects yet.\nClick 'Add Project' to get started.")
        label.add_css_class("dim-label")
        label.set_margin_top(48)
        label.set_margin_bottom(48)
        self.project_list.append(label)

    def _create_project_row(self, path: Path) -> Gtk.ListBoxRow:
        """Create a list row for a project."""
        row = Adw.ActionRow()
        row.set_title(escape_markup(path.name))
        row.set_subtitle(escape_markup(str(path)))
        row.set_activatable(False)  # Single click selects, double click opens
        row.project_path = str(path)

        # Folder icon
        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        row.add_prefix(icon)

        return row

    def _on_selection_changed(self, _listbox, row):
        """Handle selection change - enable/disable remove button."""
        self.remove_button.set_sensitive(row is not None and hasattr(row, "project_path"))

    def _on_list_double_click(self, _gesture, n_press, _x, _y):
        """Handle double-click on project list."""
        if n_press == 2:  # Double-click
            row = self.project_list.get_selected_row()
            if row and hasattr(row, "project_path"):
                self._open_project(row.project_path)

    def _on_add_project_clicked(self, _button):
        """Handle add project button click."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Project Folder")

        # Start in home directory
        home = Gio.File.new_for_path(str(Path.home()))
        dialog.set_initial_folder(home)

        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        """Handle folder selection from file dialog."""
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                # Register the project
                self.registry.register_project(path)
                # Refresh the list
                self._load_projects()
                # Open the project
                self._open_project(path)
        except GLib.Error:
            # User cancelled
            pass

    def _on_remove_project_clicked(self, _button):
        """Handle remove project button click."""
        row = self.project_list.get_selected_row()
        if row and hasattr(row, "project_path"):
            self.registry.unregister_project(row.project_path)
            self._load_projects()
            self.remove_button.set_sensitive(False)

    def _open_project(self, project_path: str, force: bool = False):
        """Open a project in a new process."""
        # Check if project is already open via lock file
        from .services.project_lock import ProjectLock

        lock = ProjectLock(project_path)

        if force:
            lock.force_release()
        elif lock.is_locked():
            # Show dialog with option to force open
            pid = lock.get_lock_pid()
            dialog = Adw.AlertDialog()
            dialog.set_heading("Project Already Open")
            dialog.set_body(
                f"The project is already open in another window (PID: {pid}).\n\n"
                "If the window is not visible or the process is hung, "
                "you can force close it and reopen."
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("force", "Force Open")
            dialog.set_response_appearance("force", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", self._on_force_open_response, project_path)
            dialog.present(self)
            return

        # Spawn new process
        subprocess.Popen(
            [sys.executable, "-m", "src.main", "--project", project_path],
            start_new_session=True,
        )

    def _on_force_open_response(self, _dialog, response, project_path):
        """Handle force open dialog response."""
        if response == "force":
            self._open_project(project_path, force=True)

    def _create_title_widget(self):
        """Create header title with version subtitle."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label="Claude Companion")
        title.add_css_class("title")

        subtitle = Gtk.Label(label=f"v{__version__}")
        subtitle.add_css_class("subtitle")

        box.append(title)
        box.append(subtitle)
        return box

    def _on_about_clicked(self, _button):
        """Show about dialog."""
        info = get_version_info()

        about = Adw.AboutDialog()
        about.set_application_name("Claude Companion")
        about.set_version(info["version"])
        about.set_comments("Native GTK4 companion app for Claude Code")

        # License
        about.set_license_type(Gtk.License.APACHE_2_0)
        about.set_copyright("© 2025 Alexander Lubovenko")

        # Links
        about.set_website("https://github.com/typedev")
        about.set_issue_url("https://github.com/typedev/claude-companion/issues")

        # Credits
        about.set_developer_name("Alexander Lubovenko")
        about.set_developers(["Alexander Lubovenko <lubovenko@gmail.com>"])

        # Debug info with commit
        if info["commit"]:
            commit_info = info["commit"]
            if info["dirty"]:
                commit_info += " (modified)"
            about.set_debug_info(f"Commit: {commit_info}")

        about.present(self)
