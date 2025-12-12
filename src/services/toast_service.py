"""Toast notification service for application-wide notifications."""

import gi

gi.require_version("Adw", "1")

from gi.repository import Adw


class ToastService:
    """Singleton service for showing toast notifications.

    Usage:
        # Initialize once in main window
        ToastService.init(toast_overlay)

        # Use anywhere in the app
        ToastService.show("File saved")
        ToastService.show_error("Failed to save file")
    """

    _instance: "ToastService | None" = None
    _overlay: Adw.ToastOverlay | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def init(cls, overlay: Adw.ToastOverlay):
        """Initialize the service with a toast overlay."""
        cls._overlay = overlay

    @classmethod
    def show(cls, message: str, timeout: int = 3):
        """Show an info toast.

        Args:
            message: The message to display
            timeout: Seconds to show (0 = until dismissed)
        """
        if cls._overlay is None:
            print(f"[Toast not initialized] {message}")
            return

        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        cls._overlay.add_toast(toast)

    @classmethod
    def show_error(cls, message: str, timeout: int = 5):
        """Show an error toast (longer timeout).

        Args:
            message: The error message to display
            timeout: Seconds to show (default 5 for errors)
        """
        if cls._overlay is None:
            print(f"[Toast not initialized] ERROR: {message}")
            return

        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        # Add error styling via priority
        toast.set_priority(Adw.ToastPriority.HIGH)
        cls._overlay.add_toast(toast)

    @classmethod
    def show_with_action(cls, message: str, action_label: str, action_name: str, timeout: int = 5):
        """Show a toast with an action button.

        Args:
            message: The message to display
            action_label: Label for the action button
            action_name: Action name to trigger (must be registered)
            timeout: Seconds to show
        """
        if cls._overlay is None:
            print(f"[Toast not initialized] {message}")
            return

        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        toast.set_button_label(action_label)
        toast.set_action_name(action_name)
        cls._overlay.add_toast(toast)
