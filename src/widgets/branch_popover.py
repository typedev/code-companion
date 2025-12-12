"""Branch management popover widget."""

from gi.repository import Gtk, GObject, Adw, GLib

from ..services import GitService, ToastService


class BranchPopover(Gtk.Popover):
    """Popover for managing git branches."""

    __gsignals__ = {
        "branch-switched": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, git_service: GitService):
        super().__init__()

        self.service = git_service

        self._build_ui()
        self.connect("show", self._on_show)

    def _build_ui(self):
        """Build the popover UI."""
        self.set_size_request(280, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header with "New branch" button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(12)
        header.set_margin_end(12)
        header.set_margin_top(12)
        header.set_margin_bottom(8)

        title = Gtk.Label(label="Branches")
        title.add_css_class("heading")
        title.set_hexpand(True)
        title.set_xalign(0)
        header.append(title)

        new_btn = Gtk.Button()
        new_btn.set_icon_name("list-add-symbolic")
        new_btn.set_tooltip_text("New branch")
        new_btn.add_css_class("flat")
        new_btn.connect("clicked", self._on_new_branch_clicked)
        header.append(new_btn)

        box.append(header)

        # Separator
        box.append(Gtk.Separator())

        # Scrolled list of branches
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_max_content_height(300)
        scrolled.set_propagate_natural_height(True)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("boxed-list")
        self.list_box.set_margin_start(12)
        self.list_box.set_margin_end(12)
        self.list_box.set_margin_top(8)
        self.list_box.set_margin_bottom(12)

        scrolled.set_child(self.list_box)
        box.append(scrolled)

        self.set_child(box)

    def _on_show(self, popover):
        """Refresh branch list when popover is shown."""
        self._load_branches()

    def _load_branches(self):
        """Load and display branches."""
        # Clear existing
        while True:
            row = self.list_box.get_row_at_index(0)
            if row is None:
                break
            self.list_box.remove(row)

        branches = self.service.list_branches()
        current_branch = self.service.get_branch_name()

        # Local branches first
        for name in sorted(branches["local"]):
            row = self._create_branch_row(name, is_current=(name == current_branch), is_remote=False)
            self.list_box.append(row)

        # Remote branches (if any)
        if branches["remote"]:
            # Separator label
            sep_row = Gtk.ListBoxRow()
            sep_row.set_selectable(False)
            sep_row.set_activatable(False)
            sep_label = Gtk.Label(label="Remote")
            sep_label.add_css_class("dim-label")
            sep_label.set_margin_top(8)
            sep_label.set_margin_bottom(4)
            sep_row.set_child(sep_label)
            self.list_box.append(sep_row)

            for name in sorted(branches["remote"]):
                row = self._create_branch_row(name, is_current=False, is_remote=True)
                self.list_box.append(row)

    def _create_branch_row(self, name: str, is_current: bool, is_remote: bool) -> Gtk.ListBoxRow:
        """Create a row for a branch."""
        row = Gtk.ListBoxRow()
        row.branch_name = name
        row.is_remote = is_remote

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # Current indicator
        if is_current:
            indicator = Gtk.Label(label="●")
            indicator.add_css_class("success")
        else:
            indicator = Gtk.Label(label=" ")
        indicator.set_width_chars(2)
        box.append(indicator)

        # Branch name
        label = Gtk.Label(label=name)
        label.set_xalign(0)
        label.set_hexpand(True)
        label.set_ellipsize(2)  # MIDDLE
        if is_remote:
            label.add_css_class("dim-label")
        box.append(label)

        # Actions for local branches (not current)
        if not is_current and not is_remote:
            # Switch button
            switch_btn = Gtk.Button()
            switch_btn.set_icon_name("object-select-symbolic")
            switch_btn.set_tooltip_text("Switch to this branch")
            switch_btn.add_css_class("flat")
            switch_btn.connect("clicked", self._on_switch_clicked, name)
            box.append(switch_btn)

            # Delete button
            delete_btn = Gtk.Button()
            delete_btn.set_icon_name("user-trash-symbolic")
            delete_btn.set_tooltip_text("Delete branch")
            delete_btn.add_css_class("flat")
            delete_btn.connect("clicked", self._on_delete_clicked, name)
            box.append(delete_btn)

        row.set_child(box)
        return row

    def _on_new_branch_clicked(self, button):
        """Show dialog to create new branch."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("New Branch")
        dialog.set_body("Create a new branch from current HEAD")

        entry = Gtk.Entry()
        entry.set_placeholder_text("Branch name")
        entry.set_hexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(entry)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")

        dialog.connect("response", self._on_create_response, entry)
        dialog.present(self.get_root())

    def _on_create_response(self, dialog, response, entry):
        """Handle create branch dialog response."""
        if response != "create":
            return

        name = entry.get_text().strip()
        if not name:
            ToastService.show_error("Branch name cannot be empty")
            return

        # Validate branch name
        if " " in name:
            ToastService.show_error("Branch name cannot contain spaces")
            return

        try:
            self.service.create_branch(name)
            ToastService.show(f"Created branch: {name}")
            self._load_branches()
        except Exception as e:
            ToastService.show_error(str(e))

    def _on_switch_clicked(self, button, branch_name: str):
        """Switch to selected branch."""
        try:
            self.service.switch_branch(branch_name)
            ToastService.show(f"Switched to: {branch_name}")
            self.popdown()
            self.emit("branch-switched")
        except Exception as e:
            ToastService.show_error(str(e))

    def _on_delete_clicked(self, button, branch_name: str):
        """Delete selected branch with confirmation."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete Branch?")
        dialog.set_body(f"Delete branch '{branch_name}'?\n\nThis cannot be undone.")

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        dialog.connect("response", self._on_delete_response, branch_name)
        dialog.present(self.get_root())

    def _on_delete_response(self, dialog, response, branch_name: str):
        """Handle delete confirmation response."""
        if response != "delete":
            return

        try:
            self.service.delete_branch(branch_name)
            ToastService.show(f"Deleted branch: {branch_name}")
            self._load_branches()
        except Exception as e:
            # If not merged, offer force delete
            if "not fully merged" in str(e):
                self._offer_force_delete(branch_name)
            else:
                ToastService.show_error(str(e))

    def _offer_force_delete(self, branch_name: str):
        """Offer to force delete unmerged branch."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Branch Not Merged")
        dialog.set_body(f"Branch '{branch_name}' is not fully merged.\n\nForce delete? You may lose commits.")

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("force", "Force Delete")
        dialog.set_response_appearance("force", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        dialog.connect("response", self._on_force_delete_response, branch_name)
        dialog.present(self.get_root())

    def _on_force_delete_response(self, dialog, response, branch_name: str):
        """Handle force delete response."""
        if response != "force":
            return

        try:
            self.service.delete_branch(branch_name, force=True)
            ToastService.show(f"Deleted branch: {branch_name}")
            self._load_branches()
        except Exception as e:
            ToastService.show_error(str(e))
