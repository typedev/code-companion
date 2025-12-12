"""Tasks panel widget for displaying and running VSCode tasks."""

from pathlib import Path

from gi.repository import Gtk, Gio, GLib, GObject, Adw

from ..services import TasksService, Task, TaskInput


class TasksPanel(Gtk.Box):
    """Panel displaying tasks from .vscode/tasks.json."""

    __gsignals__ = {
        "task-run": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),  # label, command
    }

    def __init__(self, project_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self.service = TasksService(self.project_path)
        self._file_monitor = None

        self._build_ui()
        self._setup_file_monitor()
        self.refresh()

    def _build_ui(self):
        """Build the panel UI."""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        label = Gtk.Label(label="Tasks")
        label.add_css_class("heading")
        label.set_xalign(0)
        label.set_hexpand(True)
        header_box.append(label)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Refresh tasks")
        refresh_btn.connect("clicked", lambda b: self.refresh())
        header_box.append(refresh_btn)

        self.append(header_box)

        # Tasks list
        self.tasks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.tasks_box.set_margin_start(12)
        self.tasks_box.set_margin_end(12)
        self.tasks_box.set_margin_bottom(12)
        self.append(self.tasks_box)

    def _setup_file_monitor(self):
        """Set up file monitor for tasks.json."""
        vscode_dir = self.project_path / ".vscode"

        # Monitor the .vscode directory for tasks.json changes
        if vscode_dir.exists():
            gfile = Gio.File.new_for_path(str(vscode_dir))
        else:
            # Monitor project root for .vscode creation
            gfile = Gio.File.new_for_path(str(self.project_path))

        try:
            self._file_monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.NONE,
                None
            )
            self._file_monitor.connect("changed", self._on_file_changed)
        except GLib.Error:
            pass

    def _on_file_changed(self, monitor, file, other_file, event_type):
        """Handle file changes in monitored directory."""
        filename = file.get_basename()

        # Check if it's tasks.json or .vscode directory
        if filename in ("tasks.json", ".vscode"):
            if event_type in (
                Gio.FileMonitorEvent.CREATED,
                Gio.FileMonitorEvent.CHANGED,
                Gio.FileMonitorEvent.DELETED,
            ):
                # Delay refresh slightly to avoid rapid updates
                GLib.timeout_add(100, self._delayed_refresh)

    def _delayed_refresh(self):
        """Delayed refresh to coalesce rapid changes."""
        self.refresh()
        return False  # Don't repeat

    def refresh(self):
        """Refresh the tasks list."""
        # Clear existing - collect children first to avoid modification during iteration
        children = []
        child = self.tasks_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        for child in children:
            self.tasks_box.remove(child)

        # Load tasks
        if not self.service.load():
            # No tasks.json or parse error - hide panel
            self.set_visible(False)
            return

        tasks = self.service.get_tasks()
        if not tasks:
            self.set_visible(False)
            return

        # Show panel and populate tasks
        self.set_visible(True)

        for task in tasks:
            btn = self._create_task_button(task)
            self.tasks_box.append(btn)

    def _create_task_button(self, task: Task) -> Gtk.Button:
        """Create a button for a task."""
        btn = Gtk.Button()
        btn.add_css_class("flat")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Play icon
        icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        icon.add_css_class("dim-label")
        box.append(icon)

        # Label
        label = Gtk.Label(label=task.label)
        label.set_xalign(0)
        label.set_hexpand(True)
        box.append(label)

        btn.set_child(box)
        btn.connect("clicked", self._on_task_clicked, task)

        return btn

    def _on_task_clicked(self, button, task: Task):
        """Handle task button click."""
        # Substitute variables
        command = self.service.substitute_variables(task.command)

        # Check for required inputs
        required_inputs = self.service.get_required_inputs(command)

        if required_inputs:
            self._collect_inputs(task, command, required_inputs)
        else:
            self.emit("task-run", task.label, command)

    def _collect_inputs(self, task: Task, command: str, required_inputs: list[str]):
        """Collect input values from user."""
        inputs = self.service.get_inputs()
        input_values = {}
        pending_inputs = list(required_inputs)

        def collect_next():
            if not pending_inputs:
                # All inputs collected, run task
                final_command = self.service.substitute_inputs(command, input_values)
                self.emit("task-run", task.label, final_command)
                return

            input_id = pending_inputs.pop(0)
            input_def = inputs.get(input_id)

            if not input_def:
                # Unknown input, use empty string
                input_values[input_id] = ""
                collect_next()
                return

            self._show_input_dialog(input_def, lambda value: on_input_received(input_id, value))

        def on_input_received(input_id: str, value: str | None):
            if value is None:
                # User cancelled
                return
            input_values[input_id] = value
            collect_next()

        collect_next()

    def _show_input_dialog(self, input_def: TaskInput, callback):
        """Show dialog to get input value."""
        dialog = Adw.AlertDialog()
        dialog.set_heading(input_def.description or f"Enter {input_def.id}")

        # Entry for input
        entry = Gtk.Entry()
        entry.set_text(input_def.default or "")
        entry.set_hexpand(True)

        # Wrap in box with margins
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(entry)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Run")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")

        def on_response(d, response):
            if response == "ok":
                callback(entry.get_text())
            else:
                callback(None)

        dialog.connect("response", on_response)

        # Get toplevel window
        window = self.get_root()
        dialog.present(window)
