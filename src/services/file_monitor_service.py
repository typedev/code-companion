"""Centralized file system monitoring service."""

from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("GObject", "2.0")

from gi.repository import Gio, GLib, GObject


class FileMonitorService(GObject.Object):
    """Centralized file monitoring for project directories.

    Provides unified monitoring for:
    - Git internal files (.git/index, refs, logs)
    - Working tree directories
    - Notes and docs directories
    - VSCode tasks directory

    Components connect to signals instead of creating their own monitors.

    Usage:
        service = FileMonitorService(project_path)
        service.connect("git-status-changed", on_git_status_changed)
        service.connect("working-tree-changed", on_working_tree_changed)
        ...
        # When done:
        service.shutdown()
    """

    __gsignals__ = {
        # Git status changed (stage/unstage/commit) - for file_tree icons, git_changes
        "git-status-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Git history changed (new commits, branch changes) - for git_history
        "git-history-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Working tree file changed - path passed as argument
        "working-tree-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Notes or docs changed
        "notes-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # VSCode tasks.json changed
        "tasks-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    # Debounce delays (ms)
    DEBOUNCE_GIT = 200
    DEBOUNCE_WORKING_TREE = 150
    DEBOUNCE_NOTES = 300
    DEBOUNCE_TASKS = 200

    def __init__(self, project_path: Path):
        super().__init__()

        self.project_path = Path(project_path)
        self._monitors: list[Gio.FileMonitor] = []
        self._working_tree_monitors: dict[str, Gio.FileMonitor] = {}

        # Debounce state
        self._pending_signals: dict[str, int] = {}  # signal_name -> timeout_id

        # Check if git repo
        self._git_dir = self.project_path / ".git"
        self._is_git_repo = self._git_dir.is_dir()

        self._setup_monitors()

    def _setup_monitors(self):
        """Set up all file monitors."""
        if self._is_git_repo:
            self._setup_git_monitors()

        self._setup_notes_monitors()
        self._setup_tasks_monitor()

    def _setup_git_monitors(self):
        """Set up monitors for git internal files."""
        # .git/index - stage/unstage operations
        index_file = self._git_dir / "index"
        if index_file.exists():
            self._add_monitor(
                index_file,
                is_file=True,
                callback=self._on_git_index_changed
            )

        # .git/refs/heads/ - branch updates, new commits
        refs_heads = self._git_dir / "refs" / "heads"
        if refs_heads.exists():
            self._add_monitor(
                refs_heads,
                is_file=False,
                callback=self._on_git_refs_changed
            )

        # .git/logs/HEAD - all git operations (commit, reset, checkout, etc)
        logs_head = self._git_dir / "logs" / "HEAD"
        if logs_head.exists():
            self._add_monitor(
                logs_head,
                is_file=True,
                callback=self._on_git_log_changed
            )

        # .git/HEAD - branch switches
        head_file = self._git_dir / "HEAD"
        if head_file.exists():
            self._add_monitor(
                head_file,
                is_file=True,
                callback=self._on_git_refs_changed
            )

    def _setup_notes_monitors(self):
        """Set up monitors for notes and docs directories."""
        for dir_name in ("notes", "docs"):
            dir_path = self.project_path / dir_name
            if dir_path.exists():
                self._add_monitor(
                    dir_path,
                    is_file=False,
                    callback=self._on_notes_changed
                )

        # Also monitor CLAUDE.md
        claude_md = self.project_path / "CLAUDE.md"
        if claude_md.exists():
            self._add_monitor(
                claude_md,
                is_file=True,
                callback=self._on_notes_changed
            )

    def _setup_tasks_monitor(self):
        """Set up monitor for VSCode tasks."""
        vscode_dir = self.project_path / ".vscode"
        if vscode_dir.exists():
            self._add_monitor(
                vscode_dir,
                is_file=False,
                callback=self._on_tasks_changed
            )

    def _add_monitor(self, path: Path, is_file: bool, callback):
        """Add a file or directory monitor."""
        try:
            gfile = Gio.File.new_for_path(str(path))
            if is_file:
                monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            else:
                monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", callback)
            self._monitors.append(monitor)
        except GLib.Error:
            pass  # Path may not exist or be inaccessible

    # --- Working tree monitors (dynamic) ---

    def add_working_tree_monitor(self, directory: Path):
        """Add a monitor for a working tree directory.

        Called by file_tree when directories are expanded.
        """
        path_str = str(directory)

        # Skip if already monitoring
        if path_str in self._working_tree_monitors:
            return

        # Skip .git directory
        if ".git" in directory.parts:
            return

        try:
            gfile = Gio.File.new_for_path(path_str)
            monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
            monitor.connect("changed", self._on_working_tree_changed)
            self._working_tree_monitors[path_str] = monitor
        except GLib.Error:
            pass

    def remove_working_tree_monitor(self, directory: Path):
        """Remove a working tree monitor.

        Called by file_tree when directories are collapsed.
        """
        path_str = str(directory)
        if path_str in self._working_tree_monitors:
            monitor = self._working_tree_monitors.pop(path_str)
            monitor.cancel()

    def get_monitored_directories(self) -> set[str]:
        """Get set of currently monitored working tree directories."""
        return set(self._working_tree_monitors.keys())

    # --- Event handlers ---

    def _on_git_index_changed(self, monitor, file, other_file, event_type):
        """Handle .git/index changes."""
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
        ):
            return
        self._schedule_signal("git-status-changed", self.DEBOUNCE_GIT)

    def _on_git_refs_changed(self, monitor, file, other_file, event_type):
        """Handle .git/refs or .git/HEAD changes."""
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
        ):
            return
        self._schedule_signal("git-history-changed", self.DEBOUNCE_GIT)

    def _on_git_log_changed(self, monitor, file, other_file, event_type):
        """Handle .git/logs/HEAD changes - triggers both status and history."""
        if event_type != Gio.FileMonitorEvent.CHANGED:
            return
        # Log changes affect both status (commit clears staged) and history
        self._schedule_signal("git-status-changed", self.DEBOUNCE_GIT)
        self._schedule_signal("git-history-changed", self.DEBOUNCE_GIT)

    def _on_working_tree_changed(self, monitor, file, other_file, event_type):
        """Handle working tree file changes."""
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.MOVED_IN,
            Gio.FileMonitorEvent.MOVED_OUT,
            Gio.FileMonitorEvent.RENAMED,
        ):
            return

        # Get changed path
        path = file.get_path() if file else None
        if not path:
            return

        # Skip .git internal changes
        if "/.git/" in path or path.endswith("/.git"):
            return

        # Schedule signal with path
        self._schedule_working_tree_signal(path)

    def _on_notes_changed(self, monitor, file, other_file, event_type):
        """Handle notes/docs changes."""
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
        ):
            return
        self._schedule_signal("notes-changed", self.DEBOUNCE_NOTES)

    def _on_tasks_changed(self, monitor, file, other_file, event_type):
        """Handle .vscode changes."""
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
        ):
            return

        # Only care about tasks.json
        if file:
            name = file.get_basename()
            if name != "tasks.json":
                return

        self._schedule_signal("tasks-changed", self.DEBOUNCE_TASKS)

    # --- Debounce logic ---

    def _schedule_signal(self, signal_name: str, delay_ms: int):
        """Schedule a debounced signal emission."""
        # Cancel existing timeout for this signal
        if signal_name in self._pending_signals:
            GLib.source_remove(self._pending_signals[signal_name])

        # Schedule new emission
        timeout_id = GLib.timeout_add(
            delay_ms,
            self._emit_signal,
            signal_name
        )
        self._pending_signals[signal_name] = timeout_id

    def _emit_signal(self, signal_name: str) -> bool:
        """Emit signal and clear pending state."""
        self._pending_signals.pop(signal_name, None)
        self.emit(signal_name)
        return False  # Don't repeat

    def _schedule_working_tree_signal(self, path: str):
        """Schedule working-tree-changed signal with path."""
        signal_key = f"working-tree:{path}"

        # Cancel existing timeout for this path
        if signal_key in self._pending_signals:
            GLib.source_remove(self._pending_signals[signal_key])

        # Schedule new emission
        timeout_id = GLib.timeout_add(
            self.DEBOUNCE_WORKING_TREE,
            self._emit_working_tree_signal,
            path
        )
        self._pending_signals[signal_key] = timeout_id

    def _emit_working_tree_signal(self, path: str) -> bool:
        """Emit working-tree-changed signal."""
        signal_key = f"working-tree:{path}"
        self._pending_signals.pop(signal_key, None)
        self.emit("working-tree-changed", path)
        return False

    # --- Lifecycle ---

    def shutdown(self):
        """Clean up all monitors and pending timeouts."""
        # Cancel all pending signals
        for timeout_id in self._pending_signals.values():
            GLib.source_remove(timeout_id)
        self._pending_signals.clear()

        # Cancel all monitors
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors.clear()

        for monitor in self._working_tree_monitors.values():
            monitor.cancel()
        self._working_tree_monitors.clear()

    @property
    def is_git_repo(self) -> bool:
        """Check if project is a git repository."""
        return self._is_git_repo
