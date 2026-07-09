"""Stash management popover for the Changes panel."""

from gi.repository import Adw, GObject, Gtk

from ..services import GitService, ToastService


class StashPopover(Gtk.Popover):
    """Popover to create, apply (pop), and drop git stashes."""

    __gsignals__ = {
        # Emitted after a stash/pop/drop so the Changes panel re-reads the tree.
        "stash-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, git_service: GitService):
        super().__init__()
        self.service = git_service
        self._build_ui()
        self.connect("show", lambda _p: self._load_stashes())

    def _build_ui(self):
        self.set_size_request(300, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header + create-stash form.
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header.set_margin_start(12)
        header.set_margin_end(12)
        header.set_margin_top(12)
        header.set_margin_bottom(8)

        title = Gtk.Label(label="Stashes")
        title.add_css_class("heading")
        title.set_xalign(0)
        header.append(title)

        self.message_entry = Gtk.Entry()
        self.message_entry.set_placeholder_text("Stash message (optional)")
        self.message_entry.connect("activate", lambda _e: self._on_stash_clicked())
        header.append(self.message_entry)

        form_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.untracked_check = Gtk.CheckButton(label="Include untracked")
        self.untracked_check.set_hexpand(True)
        form_row.append(self.untracked_check)
        stash_btn = Gtk.Button(label="Stash")
        stash_btn.add_css_class("suggested-action")
        stash_btn.connect("clicked", lambda _b: self._on_stash_clicked())
        form_row.append(stash_btn)
        header.append(form_row)

        box.append(header)
        box.append(Gtk.Separator())

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

    def _load_stashes(self):
        while (row := self.list_box.get_row_at_index(0)) is not None:
            self.list_box.remove(row)

        stashes = self.service.stash_list()
        if not stashes:
            empty = Gtk.ListBoxRow()
            empty.set_selectable(False)
            empty.set_activatable(False)
            label = Gtk.Label(label="No stashes")
            label.add_css_class("dim-label")
            label.set_margin_top(8)
            label.set_margin_bottom(8)
            empty.set_child(label)
            self.list_box.append(empty)
            return

        for entry in stashes:
            self.list_box.append(self._create_stash_row(entry))

    def _create_stash_row(self, entry: dict) -> Gtk.ListBoxRow:
        ref = entry["ref"]
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_hexpand(True)
        msg = Gtk.Label(label=entry.get("message", ref))
        msg.set_xalign(0)
        msg.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        msg.set_tooltip_text(entry.get("message", ref))
        text.append(msg)
        if entry.get("relative"):
            when = Gtk.Label(label=entry["relative"])
            when.set_xalign(0)
            when.add_css_class("dim-label")
            when.add_css_class("caption")
            text.append(when)
        box.append(text)

        pop_btn = Gtk.Button()
        pop_btn.set_icon_name("edit-paste-symbolic")
        pop_btn.set_tooltip_text("Pop (apply and remove)")
        pop_btn.add_css_class("flat")
        pop_btn.connect("clicked", lambda _b, r=ref: self._on_pop_clicked(r))
        box.append(pop_btn)

        drop_btn = Gtk.Button()
        drop_btn.set_icon_name("user-trash-symbolic")
        drop_btn.set_tooltip_text("Drop (delete without applying)")
        drop_btn.add_css_class("flat")
        drop_btn.connect("clicked", lambda _b, r=ref, m=entry.get("message", ref): self._on_drop_clicked(r, m))
        box.append(drop_btn)

        row.set_child(box)
        return row

    # -- actions ----------------------------------------------------------- #
    def _after_change(self, toast: str):
        ToastService.show(toast)
        self._load_stashes()
        self.emit("stash-changed")

    def _on_stash_clicked(self):
        message = self.message_entry.get_text().strip()
        include_untracked = self.untracked_check.get_active()
        try:
            self.service.stash_save(message, include_untracked)
        except Exception as e:
            ToastService.show_error(str(e))
            return
        self.message_entry.set_text("")
        self._after_change("Changes stashed")

    def _on_pop_clicked(self, ref: str):
        try:
            self.service.stash_pop(ref)
        except Exception as e:
            ToastService.show_error(str(e))
            return
        self._after_change("Stash applied")

    def _on_drop_clicked(self, ref: str, message: str):
        dialog = Adw.AlertDialog()
        dialog.set_heading("Drop Stash?")
        dialog.set_body(f"Delete stash '{message}'?\n\nThis cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("drop", "Drop")
        dialog.set_response_appearance("drop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: self._do_drop(ref) if r == "drop" else None)
        dialog.present(self)

    def _do_drop(self, ref: str):
        try:
            self.service.stash_drop(ref)
        except Exception as e:
            ToastService.show_error(str(e))
            return
        self._after_change("Stash dropped")
