"""GitHub Issues service using the REST API (stdlib urllib only).

Reuses the GitHub PAT already stored in the git credential helper via
``GitService._get_stored_credentials`` — no new auth infrastructure. All public
API methods perform blocking network calls and MUST be invoked off the GTK main
thread (see widgets/issues_panel.py for the threading pattern).
"""

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .git_service import AuthenticationRequired, GitService

API_ROOT = "https://api.github.com"
# Safety cap for pagination (100 issues/page * 10 = 1000 issues).
MAX_PAGES = 10


class GitHubError(Exception):
    """Raised for non-authentication GitHub API failures (network, 4xx/5xx)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Issue:
    """A GitHub issue (pull requests are excluded by the caller)."""

    number: int
    title: str
    body: str
    state: str  # "open" | "closed"
    labels: list[str] = field(default_factory=list)
    html_url: str = ""
    user: str = ""
    created_at: str = ""
    updated_at: str = ""
    comments: int = 0

    @classmethod
    def from_json(cls, data: dict) -> "Issue":
        """Build an Issue from a GitHub API issue object, defending against nulls."""
        user = data.get("user") or {}
        labels = [
            label["name"]
            for label in (data.get("labels") or [])
            if isinstance(label, dict) and label.get("name")
        ]
        return cls(
            number=data.get("number", 0),
            title=data.get("title") or "",
            body=data.get("body") or "",
            state=data.get("state") or "open",
            labels=labels,
            html_url=data.get("html_url") or "",
            user=user.get("login") or "",
            created_at=data.get("created_at") or "",
            updated_at=data.get("updated_at") or "",
            comments=data.get("comments", 0) or 0,
        )


@dataclass
class IssueComment:
    """A comment on a GitHub issue."""

    user: str
    body: str
    created_at: str = ""
    html_url: str = ""

    @classmethod
    def from_json(cls, data: dict) -> "IssueComment":
        user = data.get("user") or {}
        return cls(
            user=user.get("login") or "",
            body=data.get("body") or "",
            created_at=data.get("created_at") or "",
            html_url=data.get("html_url") or "",
        )


def parse_github_remote(remote_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub remote URL, or None if not GitHub.

    Handles https, scp-like ssh (git@github.com:owner/repo.git) and
    ssh:// forms. Returns None for non-github.com hosts.
    """
    if not remote_url:
        return None

    url = remote_url.strip()

    # scp-like syntax: git@github.com:owner/repo(.git)
    scp_match = re.match(r"^[\w.+-]+@([^:]+):(.+)$", url)
    if scp_match:
        host, path = scp_match.group(1), scp_match.group(2)
    else:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path

    if host.lower() != "github.com":
        return None

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]

    parts = path.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None

    return parts[0], parts[1]


class IssuesService:
    """Read/write GitHub issues for the repository at ``repo_path``."""

    def __init__(self, repo_path: Path | str):
        self.repo_path = Path(repo_path)
        self.git_service = GitService(self.repo_path)
        self._owner_repo: tuple[str, str] | None = None
        self._owner_repo_resolved = False

    # ------------------------------------------------------------------
    # Repository identity (cheap, no network)
    # ------------------------------------------------------------------
    def _get_remote_url(self) -> str | None:
        """Get the origin remote URL via git CLI (thread-safe, avoids pygit2)."""
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                if url:
                    return url
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def get_owner_repo(self) -> tuple[str, str] | None:
        """Return (owner, repo) for a GitHub origin remote, else None (cached)."""
        if not self._owner_repo_resolved:
            url = self._get_remote_url()
            self._owner_repo = parse_github_remote(url) if url else None
            self._owner_repo_resolved = True
        return self._owner_repo

    def is_github_repo(self) -> bool:
        """True if origin remote points at github.com and parses to owner/repo."""
        return self.get_owner_repo() is not None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _get_token(self, credentials: tuple[str, str] | None = None) -> str:
        """Return the GitHub PAT, raising AuthenticationRequired if unavailable.

        If ``credentials`` is provided (from the retry dialog), uses it directly.
        """
        remote_url = self._get_remote_url() or ""
        if credentials is None:
            credentials = self.git_service._get_stored_credentials(remote_url)
        if not credentials:
            raise AuthenticationRequired(
                "No GitHub credentials found", remote_url
            )
        # password field is the PAT
        return credentials[1]

    # ------------------------------------------------------------------
    # Low-level request
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        credentials: tuple[str, str] | None = None,
    ) -> tuple[object, dict]:
        """Perform an authenticated API request.

        Returns (parsed_json, response_headers). Raises AuthenticationRequired
        on 401/403 and GitHubError on other failures.
        """
        owner_repo = self.get_owner_repo()
        if owner_repo is None:
            raise GitHubError(0, "Not a GitHub repository")
        owner, repo = owner_repo

        token = self._get_token(credentials)

        url = f"{API_ROOT}/repos/{owner}/{repo}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None

        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "code-companion")
        if data is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                payload = json.loads(raw) if raw else None
                headers = dict(resp.headers.items())
                return payload, headers
        except urllib.error.HTTPError as exc:
            message = self._extract_message(exc)
            if exc.code in (401, 403):
                remote_url = self._get_remote_url() or ""
                raise AuthenticationRequired(message, remote_url) from exc
            raise GitHubError(exc.code, message) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubError(0, f"Network error: {exc}") from exc

    @staticmethod
    def _extract_message(exc: urllib.error.HTTPError) -> str:
        """Pull the human-readable message out of a GitHub error response."""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            msg = body.get("message")
            if msg:
                return msg
        except (ValueError, OSError):
            pass
        return f"GitHub API error {exc.code}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def list_issues(
        self, state: str = "open", credentials: tuple[str, str] | None = None
    ) -> list[Issue]:
        """List issues. ``state`` is one of "open", "closed", "all".

        Excludes pull requests. Follows pagination up to MAX_PAGES.
        """
        issues: list[Issue] = []
        page = 1
        while page <= MAX_PAGES:
            payload, headers = self._request(
                "GET",
                "/issues",
                params={"state": state, "per_page": 100, "page": page},
                credentials=credentials,
            )
            if not isinstance(payload, list) or not payload:
                break
            for item in payload:
                # The /issues endpoint also returns pull requests.
                if isinstance(item, dict) and "pull_request" not in item:
                    issues.append(Issue.from_json(item))
            if not self._has_next_page(headers):
                break
            page += 1
        return issues

    @staticmethod
    def _has_next_page(headers: dict) -> bool:
        """Check the Link header for a rel="next" relation."""
        link = headers.get("Link") or headers.get("link") or ""
        return 'rel="next"' in link

    def get_issue(
        self, number: int, credentials: tuple[str, str] | None = None
    ) -> Issue:
        """Fetch a single issue by number."""
        payload, _ = self._request(
            "GET", f"/issues/{number}", credentials=credentials
        )
        return Issue.from_json(payload)

    def list_comments(
        self, number: int, credentials: tuple[str, str] | None = None
    ) -> list[IssueComment]:
        """List comments on an issue, following pagination."""
        comments: list[IssueComment] = []
        page = 1
        while page <= MAX_PAGES:
            payload, headers = self._request(
                "GET",
                f"/issues/{number}/comments",
                params={"per_page": 100, "page": page},
                credentials=credentials,
            )
            if not isinstance(payload, list) or not payload:
                break
            comments.extend(
                IssueComment.from_json(item)
                for item in payload
                if isinstance(item, dict)
            )
            if not self._has_next_page(headers):
                break
            page += 1
        return comments

    def create_issue(
        self,
        title: str,
        body: str = "",
        credentials: tuple[str, str] | None = None,
    ) -> Issue:
        """Create a new issue and return it."""
        payload, _ = self._request(
            "POST",
            "/issues",
            json_body={"title": title, "body": body},
            credentials=credentials,
        )
        return Issue.from_json(payload)

    def set_issue_state(
        self,
        number: int,
        state: str,
        credentials: tuple[str, str] | None = None,
    ) -> Issue:
        """Set issue state to "open" or "closed" and return the updated issue."""
        payload, _ = self._request(
            "PATCH",
            f"/issues/{number}",
            json_body={"state": state},
            credentials=credentials,
        )
        return Issue.from_json(payload)
