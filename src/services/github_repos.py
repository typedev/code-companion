"""List the authenticated user's GitHub repositories (for the Clone picker).

Host-level companion to the repo-scoped ``IssuesService``: it reuses the same PAT (from the
keyring / git credential store) and REST conventions, but hits ``GET /user/repos`` — which is
scoped to the authenticated token, so no username is needed. The HTTP call and token lookup are
dependency-injected so the pagination/parse logic is unit-testable without a network or keyring.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .git_service import AuthenticationRequired
from .issues_service import GitHubError

API_ROOT = "https://api.github.com"
MAX_PAGES = 10
PER_PAGE = 100


def _default_token_lookup() -> tuple[str, str] | None:
    from .credential_service import CredentialService

    return CredentialService.get_instance().lookup("https://github.com")


def _default_fetch(url: str, token: str) -> list:
    """Perform one authenticated GET and return the parsed JSON list."""
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "code-companion")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthenticationRequired("GitHub authentication failed", "github.com") from exc
        raise GitHubError(exc.code, f"GitHub error: {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubError(0, f"Network error: {exc}") from exc


def _to_repo(data: dict) -> dict:
    """Project a GitHub repo JSON object onto the fields the picker needs."""
    return {
        "full_name": data.get("full_name") or "",
        "name": data.get("name") or "",
        "clone_url": data.get("clone_url") or "",
        "ssh_url": data.get("ssh_url") or "",
        "private": bool(data.get("private")),
        "description": data.get("description") or "",
    }


def list_user_repos(
    credentials: tuple[str, str] | None = None,
    *,
    fetch=_default_fetch,
    token_lookup=_default_token_lookup,
) -> list[dict]:
    """Return the authenticated user's repositories (newest activity first).

    Each item is ``{full_name, name, clone_url, ssh_url, private, description}``. Raises
    ``AuthenticationRequired`` when no token is available (or the token is rejected) and
    ``GitHubError`` on other failures.
    """
    if credentials:
        token = credentials[1]
    else:
        creds = token_lookup()
        token = creds[1] if creds else None
    if not token:
        raise AuthenticationRequired(
            "Sign in to GitHub to list your repositories", "github.com"
        )

    repos: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"{API_ROOT}/user/repos?per_page={PER_PAGE}&sort=updated"
            f"&affiliation=owner,collaborator,organization_member&page={page}"
        )
        batch = fetch(url, token) or []
        repos.extend(_to_repo(r) for r in batch if isinstance(r, dict))
        if len(batch) < PER_PAGE:
            break
    return repos
