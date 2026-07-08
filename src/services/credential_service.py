"""Secure git-credential storage via libsecret, with a graceful fallback.

Git HTTPS credentials used to be persisted, silently and unconditionally, in
plaintext at ``~/.git-credentials`` (git's ``credential.helper=store``). This
service stores them in the desktop keyring (gnome-keyring / KWallet via the
Secret Service API) instead — encrypted at rest, unlocked with the login
session — keyed per-remote via :func:`git_auth.normalize_remote_url`.

libsecret is optional. When ``gi.repository.Secret`` isn't importable (or the
keyring is unavailable), :meth:`CredentialService.available` is False and every
call transparently falls back to the existing git credential-store helper, so
behavior never regresses on machines without a keyring (headless/cron included).
"""

from __future__ import annotations

from pathlib import Path

from ..utils import git_auth

# libsecret is optional — degrade gracefully if the typelib isn't installed.
try:
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    _SCHEMA = Secret.Schema.new(
        "dev.typedev.CodeCompanion.git",
        Secret.SchemaFlags.NONE,
        {"remote": Secret.SchemaAttributeType.STRING},
    )
    _SECRET_OK = True
except Exception:  # noqa: BLE001 - any import/typelib failure disables libsecret
    Secret = None
    _SCHEMA = None
    _SECRET_OK = False


class CredentialService:
    """Singleton fronting the desktop keyring for git HTTPS credentials."""

    _instance: "CredentialService | None" = None

    @classmethod
    def get_instance(cls) -> "CredentialService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def available(self) -> bool:
        """True if libsecret is usable (else callers use the store-helper fallback)."""
        return _SECRET_OK

    # -- store ---------------------------------------------------------- #
    def store(self, remote_url: str, username: str, password: str,
              repo_path: str | Path | None = None) -> None:
        """Persist credentials for ``remote_url`` (keyring if available, else store helper)."""
        if _SECRET_OK:
            try:
                key = git_auth.normalize_remote_url(remote_url)
                Secret.password_store_sync(
                    _SCHEMA, {"remote": key}, Secret.COLLECTION_DEFAULT,
                    f"Code Companion — git {key}", f"{username}\n{password}", None,
                )
                return
            except Exception:  # noqa: BLE001 - fall back on any keyring error
                pass
        git_auth.store_credentials(remote_url, (username, password), repo_path)

    # -- lookup --------------------------------------------------------- #
    def lookup(self, remote_url: str,
               repo_path: str | Path | None = None) -> tuple[str, str] | None:
        """Return (username, password) for ``remote_url`` or None.

        Tries the keyring first, then the git credential-store helper (so
        entries saved before libsecret, or on machines without it, still work).
        """
        if _SECRET_OK:
            try:
                key = git_auth.normalize_remote_url(remote_url)
                value = Secret.password_lookup_sync(_SCHEMA, {"remote": key}, None)
                if value and "\n" in value:
                    username, password = value.split("\n", 1)
                    if username and password:
                        return (username, password)
            except Exception:  # noqa: BLE001 - fall through to the store helper
                pass
        return git_auth.get_stored_credentials(remote_url, repo_path)

    # -- clear ---------------------------------------------------------- #
    def clear(self, remote_url: str) -> None:
        """Remove a stored keyring entry for ``remote_url`` (no-op without libsecret)."""
        if not _SECRET_OK:
            return
        try:
            key = git_auth.normalize_remote_url(remote_url)
            Secret.password_clear_sync(_SCHEMA, {"remote": key}, None)
        except Exception:  # noqa: BLE001
            pass
