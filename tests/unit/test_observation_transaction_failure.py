"""A failed profile commit must not leave work for a later request to commit."""

import sqlite3
from contextlib import nullcontext
from dataclasses import replace

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.models import Impairment, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.config.workflows import WorkflowStore


def history(repository):
    return [
        tuple(row)
        for row in repository._connection.execute(
            "SELECT id, happened_at, label, digest, settings_json FROM settings_history"
        ).fetchall()
    ]


@pytest.fixture
def locked_profile(tmp_path):
    database = tmp_path / "settings.db"
    with ConfigRepository(database) as repository:
        original = Profile(
            id="slow", name="Slow", upload=Impairment(), download=Impairment()
        )
        repository.save_profile(original)
        WorkflowStore(repository).checkpoint("Before the add")
        version = repository.get_runtime_state().configuration_version
        original_history = history(repository)
        repository._connection.execute("PRAGMA busy_timeout = 0")
        reader = sqlite3.connect(database)
        reader.execute("BEGIN")
        reader.execute("SELECT settings_json FROM profiles").fetchone()
        try:
            yield database, repository, reader, original, version, original_history
        finally:
            reader.rollback()
            reader.close()


@pytest.mark.parametrize("nested", [False, True], ids=["outer", "nested"])
def test_commit_failure_rolls_back_and_a_later_transaction_recovers(locked_profile, nested):
    database, repository, reader, original, version, original_history = locked_profile
    failed = replace(
        original, website_mode="allow_only", website_domains=("failed.example.com",)
    )

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        with repository.transaction():
            with repository.transaction() if nested else nullcontext():
                repository.save_profile(failed)

    assert repository._transaction_depth == 0
    assert not repository._connection.in_transaction
    assert repository.get_profile(original.id) == original
    assert repository.get_runtime_state().configuration_version == version
    assert history(repository) == original_history

    reader.rollback()
    recovered = replace(original, name="Renamed after the failure")
    with repository.transaction():
        repository.save_profile(recovered)

    assert repository.get_runtime_state().configuration_version == version + 1
    with ConfigRepository(database) as verifier:
        assert verifier.get_profile(original.id) == recovered


def test_profile_add_commit_failure_returns_503_without_a_later_partial_save(
    locked_profile,
):
    database, repository, reader, original, version, original_history = locked_profile
    # Exercise API dispatch without starting an HTTP listener.
    server = ControlServer.__new__(ControlServer)
    server.repository = repository
    server.scheduler = None

    status, _, _ = server.write_response_for(
        "/api/observations/add-to-profile",
        {
            "profile_id": original.id,
            "domain": "failed.example.com",
            "confirm_allow_only": True,
        },
    )

    assert status == 503
    assert not repository._connection.in_transaction
    assert repository.get_profile(original.id) == original
    assert repository.get_runtime_state().configuration_version == version
    assert history(repository) == original_history

    reader.rollback()
    repository.save_traffic_rate_settings(upload_kbit=None, download_kbit=100_000)
    with ConfigRepository(database) as verifier:
        assert verifier.get_profile(original.id) == original

    status, _, _ = server.write_response_for(
        "/api/observations/add-to-profile",
        {"profile_id": original.id, "domain": "recovery.example.com"},
    )
    assert status == 409
    assert repository.get_profile(original.id) == original
    assert history(repository) == original_history

    status, _, _ = server.write_response_for(
        "/api/observations/add-to-profile",
        {
            "profile_id": original.id,
            "domain": "recovery.example.com",
            "confirm_allow_only": True,
        },
    )
    assert status == 200
    saved = repository.get_profile(original.id)
    assert saved.website_mode == "allow_only"
    assert saved.website_domains == ("recovery.example.com",)
    assert len(history(repository)) == len(original_history) + 1
