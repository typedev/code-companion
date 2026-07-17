"""CP4 tests: SyncService end-to-end via a two-machine (HOME-override) harness.

Everything under Code Companion derives from Path.home(), so two "machines" are
simulated by swapping HOME and resetting the singletons. A single bare repo plays
the private sync remote.
"""

import json
import os
from pathlib import Path

import pytest

from src.models.sync import BackupEntry, SyncState
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


def seed_summary(home: Path, project_path: Path, content: str, title: str = "") -> str:
    """Write a session summary under home's config dir; return its project_id key."""
    from src.utils.project_identity import resolve_project_identity

    d = home / ".config" / "code-companion" / "session-summaries"
    d.mkdir(parents=True, exist_ok=True)
    key = resolve_project_identity(project_path).project_id
    (d / f"{key}.md").write_text(
        f"---\ntitle: {title}\nupdated: 2026-01-01T00:00:00\n---\n{content}",
        encoding="utf-8",
    )
    return key


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

    # A clean sync overwrites nothing, so it costs no snapshot disk at all.
    assert not svcA.snapshots_dir.exists()
    assert not svcB.snapshots_dir.exists()


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


# --------------------------------------------------------------------------- #
# CP6 — backup mode
# --------------------------------------------------------------------------- #

def test_backup_mode_exports_registry_and_lists_restorable(tmp_path):
    bare = make_bare(tmp_path)
    origin = "https://github.com/test/proj.git"

    homeA = tmp_path / "mA"
    projA = make_project(homeA, "proj", origin)
    seed_memory(homeA, projA, {"M.md": "factA\n"})
    svcA = fresh_service(homeA, str(bare))
    svcA.settings.set("sync.mode", "backup")
    svcA.sync([str(projA)])

    # Registry manifest is written into the repo.
    assert read_repo_file(tmp_path, bare, "global/registry.json") is not None

    # Machine B with nothing registered: the project shows up as restorable.
    homeB = tmp_path / "mB"
    svcB = fresh_service(homeB, str(bare))
    svcB.settings.set("sync.mode", "backup")
    svcB.sync([])  # clones the repo; nothing local
    restorable = svcB.list_restorable([])
    assert [e.project_id for e in restorable] == ["github.com_test_proj"]
    assert restorable[0].name == "proj"
    assert restorable[0].remote_url == origin


def test_restore_project_clones_and_registers(tmp_path):
    # A real (local bare) project origin we can actually clone.
    proj_origin = make_bare(tmp_path, "po.git")
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(proj_origin), str(seed))
    (seed / "f.txt").write_text("hi\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "branch", "-M", "main")
    git(seed, "push", "-q", "-u", "origin", "main")

    homeB = tmp_path / "mB"
    svc = fresh_service(homeB, str(make_bare(tmp_path, "sync.git")))
    entry = BackupEntry(project_id="x", name="My Proj!", remote_url=str(proj_origin))
    new_path = svc.restore_project(entry, str(homeB / "restored"))

    assert Path(new_path).exists()
    assert (Path(new_path) / "f.txt").read_text() == "hi\n"
    from src.services.project_registry import ProjectRegistry

    assert ProjectRegistry().is_registered(new_path)


def test_session_summary_rides_the_sync(tmp_path):
    bare = make_bare(tmp_path)
    origin = "https://github.com/test/proj.git"

    # Machine A: save a session summary, then sync (seeds the repo).
    homeA = tmp_path / "mA"
    projA = make_project(homeA, "proj", origin)
    svcA = fresh_service(homeA, str(bare))  # sets HOME=homeA
    enc = seed_summary(homeA, projA, "next: ship it\n", title="Handoff")
    resA = svcA.sync([str(projA)])
    assert resA.error is None

    # Backed up to the repo's global layer.
    repo_body = read_repo_file(tmp_path, bare, f"global/session-summaries/{enc}.md")
    assert repo_body is not None and "next: ship it" in repo_body

    # Machine B: a fresh clone materializes the global summary into B's config dir.
    homeB = tmp_path / "mB"
    projB = make_project(homeB, "proj-elsewhere", origin)
    svcB = fresh_service(homeB, str(bare))  # sets HOME=homeB
    resB = svcB.sync([str(projB)])
    assert resB.error is None

    materialized = (
        homeB / ".config" / "code-companion" / "session-summaries" / f"{enc}.md"
    )
    assert materialized.exists()
    assert "next: ship it" in materialized.read_text(encoding="utf-8")

    # Cross-machine remap: B's project (same origin => same project_id, DIFFERENT path)
    # resolves to the same summary — parity with how memory follows a project.
    from src.services import session_summary_service

    loaded = session_summary_service.load(str(projB))
    assert loaded is not None and "next: ship it" in loaded["content"]


def test_messages_converge_between_machines(tmp_path):
    """Two machines each open a thread; after syncing both, each holds both threads.

    Event files are immutable + uuid-named, so the additive global merge unions them
    with no conflict (idea-2 mailbox riding the sync engine).
    """
    from src.services import message_store

    bare = make_bare(tmp_path)
    origin = "https://github.com/test/proj.git"
    p_self = "github.com/test/proj"
    p_other = "github.com/test/other"

    # Machine A opens a thread and syncs (seeds the remote).
    homeA = tmp_path / "mA"
    projA = make_project(homeA, "proj", origin)
    svcA = fresh_service(homeA, str(bare))  # HOME=homeA
    message_store.create_thread(p_self, p_other, "from A", "hi")
    assert svcA.sync([str(projA)]).error is None

    # Machine B opens its own (disjoint) thread, then syncs.
    homeB = tmp_path / "mB"
    projB = make_project(homeB, "proj-elsewhere", origin)
    svcB = fresh_service(homeB, str(bare))  # HOME=homeB
    message_store.create_thread(p_other, p_self, "from B", "yo")
    assert svcB.sync([str(projB)]).error is None
    # B materialized A's thread alongside its own.
    assert {t.subject for t in message_store.list_threads()} == {"from A", "from B"}

    # Machine A syncs again and picks up B's thread -> full convergence.
    svcA = fresh_service(homeA, str(bare))  # HOME=homeA again
    assert svcA.sync([str(projA)]).error is None
    assert {t.subject for t in message_store.list_threads()} == {"from A", "from B"}


def test_snippets_and_rules_ride_the_sync(tmp_path):
    bare = make_bare(tmp_path)
    origin = "https://github.com/test/proj.git"

    # Machine A: user snippets + rules live in the config dir.
    homeA = tmp_path / "mA"
    projA = make_project(homeA, "proj", origin)
    svcA = fresh_service(homeA, str(bare))  # sets HOME=homeA
    for sub, name, body in (
        ("snippets", "Commit.md", "commit template\n"),
        ("rules", "Language Policy.md", "English only\n"),
    ):
        d = homeA / ".config" / "code-companion" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(body, encoding="utf-8")
    assert svcA.sync([str(projA)]).error is None

    # Backed up to the repo's global layer.
    assert read_repo_file(tmp_path, bare, "global/snippets/Commit.md") == "commit template\n"
    assert (
        read_repo_file(tmp_path, bare, "global/rules/Language Policy.md")
        == "English only\n"
    )

    # Machine B: a fresh clone materializes both into B's config dir.
    homeB = tmp_path / "mB"
    projB = make_project(homeB, "proj-elsewhere", origin)
    svcB = fresh_service(homeB, str(bare))  # sets HOME=homeB
    assert svcB.sync([str(projB)]).error is None

    cfgB = homeB / ".config" / "code-companion"
    assert (cfgB / "snippets" / "Commit.md").read_text(encoding="utf-8") == "commit template\n"
    assert (
        (cfgB / "rules" / "Language Policy.md").read_text(encoding="utf-8")
        == "English only\n"
    )


# --------------------------------------------------------------------------- #
# snapshot retention
# --------------------------------------------------------------------------- #

def seed_runs(svc: SyncService, stamps: list[str]) -> None:
    for s in stamps:
        (svc.snapshots_dir / s / "proj").mkdir(parents=True)


def run_dirs(svc: SyncService) -> list[str]:
    return sorted(p.name for p in svc.snapshots_dir.iterdir() if p.is_dir())


def test_prune_snapshots_keeps_newest_three(tmp_path):
    svc = fresh_service(tmp_path / "m", str(make_bare(tmp_path)))
    seed_runs(svc, [
        "20260101T000000", "20260102T000000", "20260103T000000",
        "20260104T000000", "20260105T000000",
    ])
    svc._prune_snapshots()
    assert run_dirs(svc) == ["20260103T000000", "20260104T000000", "20260105T000000"]


def test_prune_snapshots_noop_under_retention(tmp_path):
    svc = fresh_service(tmp_path / "m", str(make_bare(tmp_path)))
    seed_runs(svc, ["20260101T000000", "20260102T000000"])
    svc._prune_snapshots()
    assert run_dirs(svc) == ["20260101T000000", "20260102T000000"]


def test_prune_snapshots_tolerates_missing_dir(tmp_path):
    # Clean install: the dir is only created when something is actually snapshotted.
    svc = fresh_service(tmp_path / "m", str(make_bare(tmp_path)))
    assert not svc.snapshots_dir.exists()
    svc._prune_snapshots()  # must not raise


def test_prune_runs_when_sync_fails(tmp_path, monkeypatch):
    # Pruning lives in sync()'s finally, so a failing run still cleans up.
    svc = fresh_service(tmp_path / "m", str(make_bare(tmp_path)))
    seed_runs(svc, [
        "20260101T000000", "20260102T000000", "20260103T000000",
        "20260104T000000", "20260105T000000",
    ])

    def boom(*a, **kw):
        raise RuntimeError("run exploded")

    monkeypatch.setattr(svc, "_run", boom)
    result = svc.sync([str(tmp_path / "m" / "nope")])
    assert result.error == "run exploded"
    assert len(run_dirs(svc)) == 3


def test_sync_run_prunes_old_snapshots(tmp_path):
    bare = make_bare(tmp_path)
    home = tmp_path / "mA"
    proj = make_project(home, "proj", "https://github.com/test/proj.git")
    seed_memory(home, proj, {"MEMORY.md": "fact\n"})
    svc = fresh_service(home, str(bare))
    seed_runs(svc, [
        "20260101T000000", "20260102T000000", "20260103T000000",
        "20260104T000000", "20260105T000000",
    ])
    assert svc.sync([str(proj)]).error is None
    assert len(run_dirs(svc)) == 3


def test_default_settings_never_mutated_by_set(tmp_path):
    # Regression: _deep_merge used to share nested dicts with DEFAULT_SETTINGS,
    # so set() on one instance polluted the defaults for every later instance.
    from src.services.settings_service import DEFAULT_SETTINGS

    home1 = tmp_path / "m1"
    svc1 = fresh_service(home1, "unused")
    svc1.settings.set("appearance.theme", "dark")
    assert DEFAULT_SETTINGS["appearance"]["theme"] == "system"

    home2 = tmp_path / "m2"
    svc2 = fresh_service(home2, "unused")
    assert svc2.settings.get("appearance.theme") == "system"
