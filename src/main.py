"""Code Companion - GTK4/libadwaita application for AI coding assistants."""

import argparse
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk, Gdk

from .project_manager import ProjectManagerWindow
from .project_window import ProjectWindow


class Application(Adw.Application):
    """Main application class."""

    def __init__(self, project_path: str | None = None):
        super().__init__(
            application_id="dev.typedev.CodeCompanion",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.project_path = project_path

    def do_startup(self):
        """Set up application-wide actions and accelerators."""
        Adw.Application.do_startup(self)

        # Register the bundled symbolic icons so they recolor with the theme.
        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            theme.add_search_path(
                str(Path(__file__).parent / "resources" / "icons-symbolic")
            )

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _on_quit(self, action, param):
        """Quit via the active window's close path so the unsaved-changes guard runs.

        Closing the window triggers ProjectWindow's close-request handler, which
        prompts for unsaved editors instead of quitting straight through them.
        """
        win = self.get_active_window()
        if win is not None:
            win.close()
        else:
            self.quit()

    def do_activate(self):
        """Called when the application is activated."""
        if self.project_path:
            # Open specific project
            win = ProjectWindow(application=self, project_path=self.project_path)
        else:
            # Open project manager
            win = ProjectManagerWindow(application=self)
        win.present()


def main():
    """Application entry point."""
    parser = argparse.ArgumentParser(prog="code-companion", description="Code Companion")
    parser.add_argument(
        "--project", "-p",
        type=str,
        help="Path to project directory to open"
    )

    # Parse known args to allow GTK to handle its own args
    args, remaining = parser.parse_known_args()

    # Validate project path if provided
    project_path = None
    if args.project:
        path = Path(args.project).expanduser().resolve()
        if path.is_dir():
            project_path = str(path)
        else:
            print(f"Error: Project path does not exist: {args.project}", file=sys.stderr)
            return 1

    app = Application(project_path=project_path)
    # Pass remaining args to GTK
    return app.run([sys.argv[0]] + remaining)


if __name__ == "__main__":
    sys.exit(main())
