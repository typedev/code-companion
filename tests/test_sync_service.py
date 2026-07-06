"""CP4 tests: SyncService end-to-end via a two-machine (HOME-override) harness.

Everything under Code Companion derives from Path.home(), so two "machines" are
simulated by swapping HOME and resetting the singletons. A single bare repo plays
the private sync remote.
"""

import json
import os
from pathlib import Path

import pytest

from src.models.sync import SyncState
from src.services.settings_service import SettingsService
from src.services.sync_service import SyncService
from src.utils.paths import encode_project_path

from tests.helpers import git, init_repo, make_bare


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _restore_home():
    saved = os.environ.get("HOME")
    yield
    if saved is not None:
        os.environ["HOME"] = saved
    SettingsService._instance = None
    SyncService._instance = None


def fresh_service(home: Path, bare_url: str) -> SyncService:
    """Reset singletons, point HOME at `home`, return a configured SyncService."""
    os.environ["HOME"] = str(home)
    SettingsService._instance = None
    SyncService._instance = None
    svc = SyncService.get_instance()
    svc.settings.set("sync.enabled", True)
    svc.settings.set("sync.repo_url", bare_url)
    return svc


def make_project(home: Path, name: str, origin: str) -> Path:
    """A git project repo with a shared origin (drives a machine-independent id)."""
    return init_repo(home / "work" / name, remote=origin)


def claude_proj_dir(home: Path, project_path: Path) -> Path:
    enc = encode_project_path(str(project_path.resolve()))
    return home / ".claude" / "projects" / enc


def seed_memory(home: Path, project_path: Path, files: dict[str, str]) -> None:
    mem = claude_proj_dir(home, project_path) / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (mem / name).write_text(text, encoding="utf-8")


def seed_sessions(home: Path, project_path: Path, files: dict[str, str]) -> None:
    d = claude_proj_dir(home, project_path)
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")


def read_repo_file(tmp_path: Path, bare: Path, rel: str) -> str | None:
    """Clone the bare remote fresh and read a file from it."""
    check = tmp_path / f"check-{abs(hash(rel)) % 10000}"
    if check.exists():
        import shutil

        shutil.rmtree(check)
    git(tmp_path, "clone", "-q", str(bare), str(check))
    p = check / rel
    return p.read_text(encoding="utf-8") if p.exists() else None


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

def test_history_and_memory_roundtrip_between_machines(tmp_path):
    bare = make_bare(tmp_path)
    origin = "https://github.com/test/proj.git"

    # Machine A: seed memory + a session, then sync (seeds the repo).
    homeA = tmp_path / "mA"
    projA = make_project(homeA, "proj", origin)
    seed_memory(homeA, projA, {"MEMORY.md": "factA\n"})
    seed_sessions(homeA, projA, {"s1.jsonl": '{"a":1}\n'})
    svcA = fresh_service(homeA, str(bare))
    resA = svcA.sync([str(projA)])
    assert resA.error is None
    assert resA.per_project[str(projA.resolve())].state == SyncState.AHEAD

    # Machine B: same origin (=> same id) at a DIFFERENT path.
    homeB = tmp_path / "mB"
    projB = make_project(homeB, "proj-elsewhere", origin)
    svcB = fresh_service(homeB, str(bare))
    resB = svcB.sync([str(projB)])
    assert resB.error is None

    # B materialized A's memory + session into B's local (id -> local path remap).
    pdirB = claude_proj_dir(homeB, projB)
    assert (pdirB / "memory" / "MEMORY.md").read_text() == "factA\n"
    assert (pdirB / "s1.jsonl").read_text() == '{"a":1}\n'
    assert resB.per_project[str(projB.resolve())].state == SyncState.BEHIND


def test_different_projects_on_two_machines_do_not_clobber(tmp_path):
    bare = make_bare(tmp_path)
    origin_x = "https://github.com/test/x.git"
    origin_y = "https://github.com/test/y.git"

    homeA = tmp_path / "mA"
    xA = make_project(homeA, "x", origin_x)
    yA = make_project(homeA, "y", origin_y)
    seed_memory(homeA, xA, {"M.md": "x0\n"})
    seed_memory(homeA, yA, {"M.md": "y0\n"})

    homeB = tmp_path / "mB"
    xB = make_project(homeB, "x", origin_x)
    yB = make_project(homeB, "y", origin_y)

    # Converge both machines on x0 / y0.
    fresh_service(homeA, str(bare)).sync([str(xA), str(yA)])
    fresh_service(homeB, str(bare)).sync([str(xB), str(yB)])

    # A edits X only.
    seed_memory(homeA, xA, {"M.md": "x1\n"})
    fresh_service(homeA, str(bare)).sync([str(xA), str(yA)])

    # B edits Y only (B's X is untouched / stale x0) and syncs.
    seed_memory(homeB, yB, {"M.md": "y1\n"})
    resB = fresh_service(homeB, str(bare)).sync([str(xB), str(yB)])
    assert resB.error is None

    from src.utils.project_identity import resolve_project_identity

    idx = resolve_project_identity(xB).project_id
    idy = resolve_project_identity(yB).project_id

    # Repo must hold A's x1 (NOT reverted to x0) AND B's y1.
    assert read_repo_file(tmp_path, bare, f"projects/{idx}/memory/M.md") == "x1\n"
    assert read_repo_file(tmp_path, bare, f"projects/{idy}/memory/M.md") == "y1\n"
    # And B pulled x1 into its own local X.
    assert (claude_proj_dir(homeB, xB) / "memory" / "M.md").read_text() == "x1\n"


def test_same_file_conflict_is_not_destructive(tmp_path):
    bare = make_bare(tmp_path)
    origin = "https://github.com/test/proj.git"

    homeA = tmp_path / "mA"
    projA = make_project(homeA, "proj", origin)
    seed_memory(homeA, projA, {"M.md": "base\n"})
    homeB = tmp_path / "mB"
    projB = make_project(homeB, "proj", origin)

    # Converge on "base".
    fresh_service(homeA, str(bare)).sync([str(projA)])
    fresh_service(homeB, str(bare)).sync([str(projB)])

    # Both edit the same file differently.
    seed_memory(homeA, projA, {"M.md": "AAA\n"})
    fresh_service(homeA, str(bare)).sync([str(projA)])
    seed_memory(homeB, projB, {"M.md": "BBB\n"})
    resB = fresh_service(homeB, str(bare)).sync([str(projB)])

    status = resB.per_project[str(projB.resolve())]
    assert status.state == SyncState.CONFLICT
    # B's local edit is preserved (never destroyed by the conflict).
    assert (claude_proj_dir(homeB, projB) / "memory" / "M.md").read_text() == "BBB\n"


def test_schema_guard_refuses_newer_repo(tmp_path):
    bare = make_bare(tmp_path)
    origin = "https://github.com/test/proj.git"

    homeA = tmp_path / "mA"
    projA = make_project(homeA, "proj", origin)
    seed_memory(homeA, projA, {"M.md": "hi\n"})
    fresh_service(homeA, str(bare)).sync([str(projA)])

    # Poison the repo manifest with a higher schema version.
    work = tmp_path / "poison"
    git(tmp_path, "clone", "-q", str(bare), str(work))
    (work / "manifest.json").write_text(json.dumps({"schemaVersion": 999}), encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "bump schema")
    git(work, "push", "-q")

    # Machine B refuses to import; local stays untouched.
    homeB = tmp_path / "mB"
    projB = make_project(homeB, "proj", origin)
    resB = fresh_service(homeB, str(bare)).sync([str(projB)])
    assert resB.per_project[str(projB.resolve())].state == SyncState.ERROR
    assert not (claude_proj_dir(homeB, projB) / "memory" / "M.md").exists()
