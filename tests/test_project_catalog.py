"""Cross-project catalog: shaping (list_catalog) + hint resolution (resolve).

Git identity, clone-url lookup, and the on-disk check are injected so these tests never
spawn git or touch real projects.
"""

import os
import subprocess
from pathlib import Path

from src.services import project_catalog
from src.services.project_registry import ProjectRegistry
from src.utils.project_identity import ProjectIdentity


class _FakeRegistry:
    def __init__(self, projects):
        self._projects = projects

    def get_projects(self):
        return list(self._projects)


# A representative set: a remote project, a local-only git project (root-commit id, no
# canonical remote), a non-git project, and one that has vanished from disk.
_PROJECTS = [
    {"path": "/proj/font-rover", "name": ""},
    {"path": "/proj/local-thing", "name": "My Local"},
    {"path": "/proj/plain", "name": ""},
    {"path": "/proj/gone", "name": "Ghost"},
]
_EXISTING = {"/proj/font-rover", "/proj/local-thing", "/proj/plain"}
_IDENTITY = {
    "/proj/font-rover": ProjectIdentity(
        project_id="github.com_typedev_font-rover",
        id_source="remote",
        canonical_remote="github.com/typedev/font-rover",
    ),
    "/proj/local-thing": ProjectIdentity(
        project_id="root-abcdef0123456789",
        id_source="root-commit",
        canonical_remote=None,
    ),
    "/proj/plain": None,
}
_CLONE = {"/proj/font-rover": "git@github.com:typedev/font-rover.git"}


def _catalog(projects=_PROJECTS):
    return dict(
        registry=_FakeRegistry(projects),
        resolve_identity=lambda p: _IDENTITY.get(p),
        clone_url=lambda p: _CLONE.get(p),
        path_exists=lambda p: p in _EXISTING,
    )


def test_list_catalog_shapes_each_project_kind():
    entries = {e.local_path: e for e in project_catalog.list_catalog(**_catalog())}
    assert len(entries) == 4

    rover = entries["/proj/font-rover"]
    assert rover.name == "font-rover"  # folder-name fallback
    assert rover.remote_url == "github.com/typedev/font-rover"
    assert rover.project_id == "github.com_typedev_font-rover"
    assert rover.clone_url == "git@github.com:typedev/font-rover.git"
    assert rover.exists is True

    local = entries["/proj/local-thing"]
    assert local.name == "My Local"  # custom label
    assert local.remote_url is None
    assert local.project_id == "root-abcdef0123456789"
    assert local.clone_url is None  # no remote -> clone lookup skipped

    plain = entries["/proj/plain"]
    assert plain.project_id is None and plain.remote_url is None
    assert plain.exists is True

    gone = entries["/proj/gone"]
    assert gone.exists is False
    assert gone.project_id is None and gone.clone_url is None


def test_list_catalog_skips_clone_lookup_without_remote():
    calls = []

    def _clone(path):
        calls.append(path)
        return _CLONE.get(path)

    inject = _catalog()
    inject["clone_url"] = _clone
    project_catalog.list_catalog(**inject)
    # Only the project with a canonical remote triggers a clone-url lookup.
    assert calls == ["/proj/font-rover"]


def test_resolve_exact_name_single_match():
    res = project_catalog.resolve("font-rover", **_catalog())
    assert res["ambiguous"] is False
    assert res["candidates"] == []
    assert res["match"]["local_path"] == "/proj/font-rover"


def test_resolve_by_owner_repo():
    res = project_catalog.resolve("typedev/font-rover", **_catalog())
    assert res["match"]["remote_url"] == "github.com/typedev/font-rover"
    assert res["ambiguous"] is False


def test_resolve_by_repo_suffix():
    # host/owner/repo ends with "/<repo>" -> medium tier.
    res = project_catalog.resolve("font-rover", **_catalog())
    assert res["match"]["local_path"] == "/proj/font-rover"


def test_resolve_substring_single_match():
    res = project_catalog.resolve("plai", **_catalog())
    assert res["match"]["local_path"] == "/proj/plain"


def test_resolve_ambiguous_lists_candidates():
    projects = [
        {"path": "/proj/font-rover", "name": ""},
        {"path": "/proj/font-goggles", "name": ""},
    ]
    existing = {"/proj/font-rover", "/proj/font-goggles"}
    res = project_catalog.resolve(
        "font",
        registry=_FakeRegistry(projects),
        resolve_identity=lambda p: None,
        clone_url=lambda p: None,
        path_exists=lambda p: p in existing,
    )
    assert res["ambiguous"] is True
    assert res["match"] is None
    assert {c["local_path"] for c in res["candidates"]} == set(existing)


def test_resolve_exact_name_beats_substring_tie():
    projects = [
        {"path": "/proj/font-rover", "name": ""},
        {"path": "/proj/font-goggles", "name": ""},
    ]
    existing = {"/proj/font-rover", "/proj/font-goggles"}
    res = project_catalog.resolve(
        "font-rover",
        registry=_FakeRegistry(projects),
        resolve_identity=lambda p: None,
        clone_url=lambda p: None,
        path_exists=lambda p: p in existing,
    )
    # "font-rover" is an exact name (strong); "font-goggles" only a weak substring miss.
    assert res["ambiguous"] is False
    assert res["match"]["local_path"] == "/proj/font-rover"


def test_resolve_no_match():
    res = project_catalog.resolve("nonexistent-xyz", **_catalog())
    assert res["match"] is None
    assert res["candidates"] == []
    assert res["ambiguous"] is False


# --------------------------------------------------------------------------- #
# Integration: real git repos + real identity resolution (no injection).
# --------------------------------------------------------------------------- #
def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _init_repo(path):
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "f.txt").write_text("x", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


def test_list_catalog_resolves_real_git_identity(tmp_path):
    remote_proj = tmp_path / "remote-proj"
    local_proj = tmp_path / "local-proj"
    _init_repo(remote_proj)
    _init_repo(local_proj)
    _git(remote_proj, "remote", "add", "origin",
         "https://github.com/typedev/remote-proj.git")

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    reg = ProjectRegistry()
    reg.config_dir = cfg
    reg.config_file = cfg / "projects.json"
    reg.register_project(str(remote_proj))
    reg.register_project(str(local_proj))

    entries = {
        Path(e.local_path).name: e
        for e in project_catalog.list_catalog(registry=reg)
    }

    remote = entries["remote-proj"]
    assert remote.remote_url == "github.com/typedev/remote-proj"
    assert remote.project_id == "github.com_typedev_remote-proj"
    assert remote.clone_url == "https://github.com/typedev/remote-proj.git"
    assert remote.exists is True

    local = entries["local-proj"]
    assert local.remote_url is None
    assert local.project_id and local.project_id.startswith("root-")
    assert local.clone_url is None
