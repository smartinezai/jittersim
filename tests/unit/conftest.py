"""Fixtures of the remote command tests."""

import os

import pytest

from jittersim import updates

from tests.remote_support import fake_dashboard


@pytest.fixture
def remote_environment(tmp_path, monkeypatch):
    """A home folder without a saved connection and no JITTERSIM_* variables."""

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("JITTERSIM_URL", "JITTERSIM_CA_CERT", "JITTERSIM_ADMIN_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def dashboard(tmp_path, remote_environment):
    """A disposable HTTPS dashboard with its own certificate."""

    with fake_dashboard(tmp_path) as server:
        yield server


@pytest.fixture(autouse=True)
def test_files_count_as_root_owned(monkeypatch):
    """Files that a test makes belong to the test user. On the Pi the trusted key list
    belongs to root, so tests of the release code stand in for root with their own user.
    The tests of the owner check itself set the real value back."""

    monkeypatch.setattr(updates, "TRUSTED_OWNER_UID", os.geteuid())
