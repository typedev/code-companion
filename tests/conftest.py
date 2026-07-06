"""Shared fixtures for the cross-machine sync tests."""

import pytest

from tests.helpers import init_repo


@pytest.fixture
def git_repo(tmp_path):
    """A committed git repo with no remote."""
    return init_repo(tmp_path / "proj", commit=True)
