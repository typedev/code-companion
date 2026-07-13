"""Shared GitHub credentials dialog for the Issues feature.

Mirrors the push/pull credentials dialog in git_changes_panel.py so that an
AuthenticationRequired from the GitHub API can be resolved with the same UX.
Follows the CLAUDE.md dialog gotcha: Adw.AlertDialog + set_extra_child, no
EventControllerKey.
"""

from gi.repository import Gtk, Adw


def show_github_credentials_dialog(
    parent: Gtk.Widget, remote_url: str, on_credentials, *, show_remember: bool = False
):
    """Prompt for GitHub credentials (PAT) and invoke ``on_credentials(creds, remember)``.

    This is the single credential prompt for the whole app (clone, push, pull, repo
    picker, issues). Persistence goes through :class:`CredentialService`.

    Args:
        parent: Any widget inside the window (used to find the root for present()).
        remote_url: The remote URL shown to the user.
        on_credentials: Callback receiving a ``((username, password), remember)`` pair
            on submit. ``remember`` is the checkbox state, or ``True`` when the
            checkbox is hidden (``show_remember=False``) — preserving the prior
            unconditional-store behavior for callers that don't opt in.
        show_remember: Show a "Remember" opt-in checkbox (for git clone/push/pull).
    """
    dialog = Adw.AlertDialog()
    dialog.set_heading("GitHub Authentication Required")
    dialog.set_body(f"Enter credentials for {remote_url}")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_start(12)
    box.set_margin_end(12)

    username_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    username_label = Gtk.Label(label="Username:")
    username_label.set_xalign(0)
    username_label.set_size_request(80, -1)
    username_box.append(username_label)
    username_entry = Gtk.Entry()
    username_entry.set_hexpand(True)
    username_box.append(username_entry)
    box.append(username_box)

    password_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    password_label = Gtk.Label(label="Token:")
    password_label.set_xalign(0)
    password_label.set_size_request(80, -1)
    password_box.append(password_label)
    password_entry = Gtk.PasswordEntry()
    password_entry.set_hexpand(True)
    password_entry.set_show_peek_icon(True)
    password_box.append(password_entry)
    box.append(password_box)

    hint = Gtk.Label(
        label="Tip: For GitHub, use a Personal Access Token (scope: repo) as the token."
    )
    hint.add_css_class("dim-label")
    hint.add_css_class("caption")
    hint.set_wrap(True)
    hint.set_xalign(0)
    box.append(hint)

    # Opt-in persistence. Default on where a keyring exists; otherwise it would fall
    # back to the plaintext store, so default off and say so.
    remember_check = None
    if show_remember:
        from ..services.credential_service import CredentialService

        keyring = CredentialService.get_instance().available()
        remember_check = Gtk.CheckButton(
            label="Remember in keyring" if keyring
            else "Remember (plaintext — no keyring found)"
        )
        remember_check.set_active(keyring)
        box.append(remember_check)

    dialog.set_extra_child(box)

    dialog.add_response("cancel", "Cancel")
    dialog.add_response("authenticate", "Authenticate")
    dialog.set_response_appearance("authenticate", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("authenticate")
    dialog.set_close_response("cancel")

    def on_response(_dialog, response):
        if response == "authenticate":
            username = username_entry.get_text().strip()
            password = password_entry.get_text()
            if username and password:
                remember = remember_check.get_active() if remember_check else True
                on_credentials((username, password), remember)

    dialog.connect("response", on_response)
    dialog.present(parent.get_root())
