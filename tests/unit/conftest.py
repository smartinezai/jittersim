"""Fixtures of the remote command tests."""

import pytest

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
