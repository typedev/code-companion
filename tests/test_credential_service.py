"""Roadmap 3.7: CredentialService keyring path + graceful store-helper fallback."""

import src.services.credential_service as cs
from src.services.credential_service import CredentialService


class _FakeSecret:
    """Minimal in-memory stand-in for gi.repository.Secret (no real keyring)."""
    COLLECTION_DEFAULT = "default"

    def __init__(self):
        self.store = {}

    def password_store_sync(self, schema, attrs, collection, label, value, cancellable):
        self.store[attrs["remote"]] = value
        return True

    def password_lookup_sync(self, schema, attrs, cancellable):
        return self.store.get(attrs["remote"])

    def password_clear_sync(self, schema, attrs, cancellable):
        self.store.pop(attrs["remote"], None)
        return True


def test_available_reflects_secret_flag(monkeypatch):
    monkeypatch.setattr(cs, "_SECRET_OK", False)
    assert CredentialService().available() is False
    monkeypatch.setattr(cs, "_SECRET_OK", True)
    assert CredentialService().available() is True


def test_fallback_to_store_helper_when_unavailable(monkeypatch):
    monkeypatch.setattr(cs, "_SECRET_OK", False)
    stored = {}
    monkeypatch.setattr(cs.git_auth, "store_credentials",
                        lambda url, creds, repo=None: stored.update({url: creds}))
    monkeypatch.setattr(cs.git_auth, "get_stored_credentials",
                        lambda url, repo=None: stored.get(url))

    svc = CredentialService()
    svc.store("https://github.com/o/r.git", "alice", "tok")
    assert stored == {"https://github.com/o/r.git": ("alice", "tok")}
    assert svc.lookup("https://github.com/o/r.git") == ("alice", "tok")


def test_keyring_roundtrip_uses_secret_not_store_helper(monkeypatch):
    fake = _FakeSecret()
    monkeypatch.setattr(cs, "_SECRET_OK", True)
    monkeypatch.setattr(cs, "Secret", fake)
    monkeypatch.setattr(cs, "_SCHEMA", object())

    def _boom(*a, **k):
        raise AssertionError("store helper must not be used when keyring works")

    monkeypatch.setattr(cs.git_auth, "store_credentials", _boom)
    monkeypatch.setattr(cs.git_auth, "get_stored_credentials", _boom)

    svc = CredentialService()
    svc.store("https://github.com/o/r.git", "bob", "pat")
    assert svc.lookup("https://github.com/o/r.git") == ("bob", "pat")


def test_keyring_entry_is_host_scoped_for_http_remotes(monkeypatch):
    # ca0ade6: access tokens are per-host, so one entry serves every repo on the
    # host — keyed by credential_key (bare host), NOT normalize_remote_url.
    fake = _FakeSecret()
    monkeypatch.setattr(cs, "_SECRET_OK", True)
    monkeypatch.setattr(cs, "Secret", fake)
    monkeypatch.setattr(cs, "_SCHEMA", object())
    monkeypatch.setattr(cs.git_auth, "get_stored_credentials",
                        lambda url, repo=None: None)  # keyring must answer, not the helper

    svc = CredentialService()
    svc.store("https://github.com/o/r.git", "bob", "pat")
    assert list(fake.store) == ["github.com"]
    # A different repo on the same host hits the same token...
    assert svc.lookup("https://github.com/other/repo") == ("bob", "pat")
    # ...while another host does not.
    assert svc.lookup("https://gitlab.com/o/r") is None


def test_clear_removes_what_store_wrote(monkeypatch):
    # store/lookup/clear must agree on the key: clear() used to compute
    # normalize_remote_url ("github.com/o/r") while store() wrote the bare host,
    # so it silently cleared nothing.
    fake = _FakeSecret()
    monkeypatch.setattr(cs, "_SECRET_OK", True)
    monkeypatch.setattr(cs, "Secret", fake)
    monkeypatch.setattr(cs, "_SCHEMA", object())
    monkeypatch.setattr(cs.git_auth, "get_stored_credentials",
                        lambda url, repo=None: None)

    svc = CredentialService()
    svc.store("https://github.com/o/r.git", "bob", "pat")
    svc.clear("https://github.com/o/r.git")
    assert fake.store == {}
    assert svc.lookup("https://github.com/o/r.git") is None


def test_lookup_falls_back_when_keyring_empty(monkeypatch):
    fake = _FakeSecret()  # empty
    monkeypatch.setattr(cs, "_SECRET_OK", True)
    monkeypatch.setattr(cs, "Secret", fake)
    monkeypatch.setattr(cs, "_SCHEMA", object())
    monkeypatch.setattr(cs.git_auth, "get_stored_credentials",
                        lambda url, repo=None: ("legacy", "old"))

    svc = CredentialService()
    # Nothing in the keyring -> falls through to the store helper.
    assert svc.lookup("https://github.com/o/r") == ("legacy", "old")
