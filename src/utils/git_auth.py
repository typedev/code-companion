"""Shared git HTTPS authentication helpers.

Extracted from GitService so the cross-machine sync repo wrapper can reuse the
exact same GIT_ASKPASS credential-injection mechanism. Behaviour is preserved
verbatim from the original GitService private methods.

Only HTTPS username/password (or token-as-password) is handled here; SSH relies
on the ambient agent.
"""

import os
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

# Substrings that identify a git authentication failure in stderr/stdout.
AUTH_INDICATORS = [
    "could not read Username",
    "could not read Password",
    "Authentication failed",
    "Invalid username or password",
    "fatal: Authentication failed",
    "Permission denied",
    "remote: Invalid username or password",
]

# Askpass helper: echoes credentials passed via environment variables.
_ASKPASS_SCRIPT = """#!/bin/bash
if [[ "$1" == *"Username"* ]] || [[ "$1" == *"username"* ]]; then
    echo "$GIT_USERNAME"
elif [[ "$1" == *"Password"* ]] || [[ "$1" == *"password"* ]]; then
    echo "$GIT_PASSWORD"
fi
"""


def is_auth_error(error: str) -> bool:
    """Check if an error message indicates authentication failure."""
    return any(indicator in error for indicator in AUTH_INDICATORS)


def _is_http_remote(url: str) -> bool:
    """GIT_ASKPASS credentials only apply to http(s) remotes."""
    return url.strip().lower().startswith(("http://", "https://"))


def normalize_remote_url(url: str) -> str:
    """Canonicalize a git remote URL to a stable ``host/owner/repo`` identity.

    Collapses scheme, embedded credentials, ssh/https forms, a trailing ``.git``
    and case, so both machines derive the same project id from the same repo.

    Examples::

        git@github.com:typedev/code-companion.git -> github.com/typedev/code-companion
        https://github.com/typedev/code-companion  -> github.com/typedev/code-companion
    """
    u = url.strip()
    # scp-like syntax "git@host:owner/repo.git" has no scheme -> rewrite to ssh://
    if "://" not in u and "@" in u and ":" in u:
        _, rest = u.split("@", 1)
        host, path = rest.split(":", 1)
        u = f"ssh://{host}/{path}"
    parsed = urllib.parse.urlparse(u)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    path = path.lower()
    return f"{host}/{path}".strip("/")


def _credential_cwd(repo_path: str | Path | None) -> str | None:
    """Return an existing directory to run ``git credential`` in.

    During a clone the repo path may not exist yet, so fall back to its parent
    or the home directory.
    """
    if repo_path:
        p = Path(repo_path)
        if p.exists():
            return str(p)
        if p.parent.exists():
            return str(p.parent)
    return str(Path.home())


def get_stored_credentials(
    remote_url: str, repo_path: str | Path | None = None
) -> tuple[str, str] | None:
    """Try to get stored credentials from the git credential helper.

    Returns (username, password) if found, None otherwise.
    """
    try:
        parsed = urllib.parse.urlparse(remote_url)
        protocol = parsed.scheme or "https"
        host = parsed.hostname or ""

        credential_input = f"protocol={protocol}\nhost={host}\n\n"

        # Use credential.helper=store explicitly to match store_credentials.
        result = subprocess.run(
            ["git", "-c", "credential.helper=store", "credential", "fill"],
            input=credential_input,
            cwd=_credential_cwd(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            username = None
            password = None
            for line in result.stdout.strip().split("\n"):
                if line.startswith("username="):
                    username = line[9:]
                elif line.startswith("password="):
                    password = line[9:]

            if username and password:
                return (username, password)

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    return None


def build_auth_env(
    remote_url: str,
    credentials: tuple[str, str] | None,
    repo_path: str | Path | None = None,
) -> tuple[dict, str | None]:
    """Build an environment dict with GIT_ASKPASS for the given credentials.

    Returns ``(env, askpass_script_path)``. ``askpass_script_path`` is the temp
    script path (or None); the caller owns cleanup if it wishes to unlink it.
    """
    env = os.environ.copy()

    # If no credentials provided, try to get stored ones (http(s) only — ssh and
    # local/path remotes never use GIT_ASKPASS, and probing the credential helper
    # for them is pointless and slow).
    if not credentials and _is_http_remote(remote_url):
        credentials = get_stored_credentials(remote_url, repo_path)

    if credentials:
        username, password = credentials
        # Pass credentials via environment (safer than embedding in the script).
        env["GIT_USERNAME"] = username
        env["GIT_PASSWORD"] = password

        askpass_path: str | None = None
        fd, path = tempfile.mkstemp(prefix="git_askpass_", suffix=".sh")
        try:
            os.write(fd, _ASKPASS_SCRIPT.encode())
            os.close(fd)
            os.chmod(path, 0o700)
            env["GIT_ASKPASS"] = path
            env["GIT_TERMINAL_PROMPT"] = "0"
            askpass_path = path
        except Exception:
            pass
        return env, askpass_path

    # No credentials: disable terminal prompts so git fails with an auth error.
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env, None


def store_credentials(
    remote_url: str,
    credentials: tuple[str, str],
    repo_path: str | Path | None = None,
) -> None:
    """Persist credentials via the git credential-store helper (~/.git-credentials)."""
    username, password = credentials
    try:
        parsed = urllib.parse.urlparse(remote_url)
        protocol = parsed.scheme or "https"
        host = parsed.hostname or ""

        credential_input = (
            f"protocol={protocol}\nhost={host}\n"
            f"username={username}\npassword={password}\n\n"
        )

        subprocess.run(
            ["git", "-c", "credential.helper=store", "credential", "approve"],
            input=credential_input,
            cwd=_credential_cwd(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        pass  # Silently fail - credentials will just not be stored.
