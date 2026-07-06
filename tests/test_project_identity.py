"""CP1 tests: project identity resolution."""

from src.utils import git_auth
from src.utils.project_identity import resolve_project_identity

from tests.helpers import git, init_repo


def test_non_git_folder_is_not_syncable(tmp_path):
    (tmp_path / "plain").mkdir()
    assert resolve_project_identity(tmp_path / "plain") is None


def test_remote_url_identity(tmp_path):
    repo = init_repo(
        tmp_path / "proj",
        remote="https://github.com/typedev/code-companion-sync.git",
    )
    ident = resolve_project_identity(repo)
    assert ident is not None
    assert ident.id_source == "remote"
    assert ident.canonical_remote == "github.com/typedev/code-companion-sync"
    assert ident.project_id == "github.com_typedev_code-companion-sync"


def test_ssh_and_https_remotes_yield_same_id(tmp_path):
    a = init_repo(tmp_path / "a", remote="https://github.com/typedev/repo.git")
    b = init_repo(tmp_path / "b", remote="git@github.com:typedev/repo.git")
    id_a = resolve_project_identity(a)
    id_b = resolve_project_identity(b)
    assert id_a is not None and id_b is not None
    assert id_a.project_id == id_b.project_id


def test_root_commit_identity_when_no_remote(tmp_path):
    repo = init_repo(tmp_path / "proj", commit=True)  # no remote
    ident = resolve_project_identity(repo)
    assert ident is not None
    assert ident.id_source == "root-commit"
    root = git(repo, "rev-list", "--max-parents=0", "HEAD")
    assert ident.project_id == f"root-{root[:16]}"


def test_root_commit_stable_across_clone(tmp_path):
    origin = init_repo(tmp_path / "origin", commit=True)
    # Clone with no configured remote-derived identity divergence: remove origin
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    git(clone, "remote", "remove", "origin")
    id_origin = resolve_project_identity(origin)
    id_clone = resolve_project_identity(clone)
    assert id_origin is not None and id_clone is not None
    # Same root commit -> same id even though paths differ.
    assert id_origin.project_id == id_clone.project_id


def test_committed_uuid_file_when_no_commits(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-q")  # no commits, no remote
    id_dir = repo / ".code-companion"
    id_dir.mkdir()
    (id_dir / "project-id").write_text("My-Project-UUID-123\n", encoding="utf-8")
    ident = resolve_project_identity(repo)
    assert ident is not None
    assert ident.id_source == "committed-uuid"
    assert ident.project_id == "my-project-uuid-123"


def test_empty_repo_without_id_file_is_not_syncable(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-q")
    assert resolve_project_identity(repo) is None


def test_normalize_remote_url_forms():
    n = git_auth.normalize_remote_url
    assert n("https://github.com/A/B.git") == "github.com/a/b"
    assert n("git@github.com:A/B.git") == "github.com/a/b"
    assert n("https://github.com/A/B/") == "github.com/a/b"
