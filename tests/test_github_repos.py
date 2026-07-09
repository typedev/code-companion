"""github_repos.list_user_repos: pagination, parsing, and the no-token path."""

import pytest

from src.services import github_repos
from src.services.git_service import AuthenticationRequired


def _repo_json(name, owner="me", private=False, desc="d"):
    return {
        "full_name": f"{owner}/{name}",
        "name": name,
        "clone_url": f"https://github.com/{owner}/{name}.git",
        "ssh_url": f"git@github.com:{owner}/{name}.git",
        "private": private,
        "description": desc,
    }


def test_paginates_and_parses():
    page1 = [_repo_json(f"r{i}") for i in range(github_repos.PER_PAGE)]  # full page
    page2 = [_repo_json("last", private=True)]  # short page -> stop
    calls = []

    def fake_fetch(url, token):
        calls.append(url)
        return page1 if url.endswith("page=1") else page2

    repos = github_repos.list_user_repos(credentials=("u", "tok"), fetch=fake_fetch)
    assert len(repos) == github_repos.PER_PAGE + 1
    assert len(calls) == 2  # stopped after the short second page
    last = repos[-1]
    assert last["full_name"] == "me/last"
    assert last["name"] == "last"
    assert last["clone_url"] == "https://github.com/me/last.git"
    assert last["private"] is True


def test_tolerates_null_fields():
    def fake_fetch(url, token):
        return [{"full_name": None, "name": None, "clone_url": None,
                 "private": None, "description": None}]

    repos = github_repos.list_user_repos(credentials=("u", "tok"), fetch=fake_fetch)
    assert repos == [{
        "full_name": "", "name": "", "clone_url": "", "ssh_url": "",
        "private": False, "description": "",
    }]


def test_no_token_raises_without_fetching():
    called = []

    def fake_fetch(url, token):
        called.append(url)
        return []

    with pytest.raises(AuthenticationRequired):
        github_repos.list_user_repos(fetch=fake_fetch, token_lookup=lambda: None)
    assert called == []  # never hit the network


def test_uses_looked_up_token():
    seen = {}

    def fake_fetch(url, token):
        seen["token"] = token
        return []

    github_repos.list_user_repos(
        fetch=fake_fetch, token_lookup=lambda: ("user", "secret-pat")
    )
    assert seen["token"] == "secret-pat"
