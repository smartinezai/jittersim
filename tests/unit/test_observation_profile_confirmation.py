"""Require current permission before observed websites restrict a profile."""

import json
import sqlite3
import threading
from dataclasses import asdict, replace

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.models import Impairment, Profile
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.config.workflows import WorkflowStore

PATH = "/api/observations/add-to-profile"
PROFILE = Profile(
    "slow", "Slow", upload=Impairment(delay_ms=123, bandwidth_kbit=800),
    download=Impairment(loss_percent=13), random_seed=17, reroll_interval_seconds=30,
    outage_probability=15, outage_duration_seconds=4, outage_interval_seconds=10,
    website_domains=("old.example",),
)


@pytest.fixture
def control(tmp_path):
    database = tmp_path / "settings.sqlite"
    with ConfigRepository(database) as repository:
        repository.save_profile(PROFILE)
        WorkflowStore(repository).checkpoint("Before observed names")
        server = ControlServer(("127.0.0.1", 0), repository)
        try:
            yield repository, server, database
        finally:
            server.server_close()


def payload(action, **extra):
    names = {"domain": " ADDED.Example. "} if action == "single" else {
        "domains": [" ADDED.Example. ", "old.example", "second.example"],
    }
    return {"profile_id": "slow", **names, **extra}


def request(server, body):
    status, content_type, response = server.write_response_for(PATH, body)
    assert content_type == "application/json"
    return status, json.loads(response)


def snapshot(repository):
    return (
        repository.get_profile("slow"), repository.get_runtime_state(),
        tuple(tuple(row) for row in repository._connection.execute(
            "SELECT * FROM settings_history ORDER BY id"
        )),
        tuple(tuple(row) for row in repository._connection.execute(
            "SELECT * FROM settings_history_status ORDER BY id"
        )),
    )


@pytest.mark.parametrize("action", ("single", "group"))
@pytest.mark.parametrize("confirmed", ("absent", False))
def test_stale_allowlist_action_requires_confirmation_without_changing_saved_state(
    control, action, confirmed
):
    repository, server, _ = control
    repository.save_profile(replace(PROFILE, website_mode="allow_only"))
    WorkflowStore(repository).checkpoint("A tab saw an allow list")
    stale_tab_profile = repository.get_profile("slow")
    repository.save_profile(replace(stale_tab_profile, website_mode="unrestricted"))
    WorkflowStore(repository).checkpoint("Another tab made it unrestricted")
    before = snapshot(repository)
    body = payload(action)
    if confirmed != "absent":
        body["confirm_allow_only"] = confirmed

    status, data = request(server, body)

    assert status == 409
    assert data["code"] == "allow_only_confirmation_required"
    assert data["profile"] == json.loads(json.dumps(asdict(before[0])))
    assert snapshot(repository) == before


@pytest.mark.parametrize("action", ("single", "group"))
def test_deliberate_confirmation_restricts_the_profile_and_preserves_other_fields(control, action):
    repository, server, _ = control
    before = snapshot(repository)

    status, data = request(server, payload(action, confirm_allow_only=True))

    assert status == 200
    names = ("old.example", "added.example")
    if action == "group":
        names += ("second.example",)
    expected = replace(PROFILE, website_mode="allow_only", website_domains=names)
    assert repository.get_profile("slow") == expected
    assert data["profile"] == json.loads(json.dumps(asdict(expected)))
    assert repository.get_runtime_state().configuration_version == (
        before[1].configuration_version + 1
    )
    assert len(WorkflowStore(repository).history()) == len(before[2]) + 1


@pytest.mark.parametrize("action", ("single", "group"))
@pytest.mark.parametrize("confirmed", ("absent", False))
def test_existing_allowlists_accept_older_clients_without_confirmation(control, action, confirmed):
    repository, server, _ = control
    repository.save_profile(replace(PROFILE, website_mode="allow_only"))
    body = payload(action)
    if confirmed != "absent":
        body["confirm_allow_only"] = confirmed

    status, _ = request(server, body)

    assert status == 200
    names = ("old.example", "added.example") + (("second.example",) if action == "group" else ())
    assert repository.get_profile("slow") == replace(
        PROFILE, website_mode="allow_only", website_domains=names,
    )


@pytest.mark.parametrize("action", ("single", "group"))
@pytest.mark.parametrize("mode", ("unrestricted", "allow_only"))
@pytest.mark.parametrize("confirmed", (None, 0, 1, "true", "false", [], {}))
def test_confirmation_must_be_a_boolean_even_for_an_existing_allowlist(
    control, action, mode, confirmed
):
    repository, server, _ = control
    repository.save_profile(replace(PROFILE, website_mode=mode))
    before = snapshot(repository)

    status, _ = request(server, payload(action, confirm_allow_only=confirmed))

    assert status == 400
    assert snapshot(repository) == before


@pytest.mark.parametrize("body", (
    {"domains": ["one.example"] * 65},
    {"domains": [f"host{index}.example" for index in range(65)]},
    {"domains": []},
    {"domains": ["fine.example", "bad name"]},
    {"domain": "fine.example", "domains": ["another.example"]},
))
def test_confirmed_actions_keep_batch_validation_and_rollback(control, body):
    repository, server, _ = control
    before = snapshot(repository)

    status, _ = request(server, {"profile_id": "slow", "confirm_allow_only": True, **body})

    assert status == 400
    assert snapshot(repository) == before


def test_a_confirmed_batch_of_64_names_still_works(control):
    repository, server, _ = control
    names = tuple(f"host{index}.example" for index in range(64))

    status, _ = request(server, {
        "profile_id": "slow", "domains": list(names), "confirm_allow_only": True,
    })

    assert status == 200
    assert repository.get_profile("slow") == replace(
        PROFILE, website_mode="allow_only", website_domains=PROFILE.website_domains + names,
    )


@pytest.mark.parametrize("action", ("single", "group"))
def test_saved_profile_limit_rejects_confirmed_additions_without_changes(control, action):
    repository, server, _ = control
    repository.save_profile(replace(
        PROFILE, website_domains=tuple(f"host{index}.example" for index in range(100)),
    ))
    before = snapshot(repository)

    status, _ = request(server, payload(action, confirm_allow_only=True))

    assert status == 400
    assert snapshot(repository) == before


def test_confirmation_keeps_deduplication_at_the_saved_profile_limit(control):
    repository, server, _ = control
    names = tuple(f"host{index}.example" for index in range(99))
    repository.save_profile(replace(PROFILE, website_domains=names))

    status, _ = request(server, {
        "profile_id": "slow", "domains": ["host0.example", "new.example"],
        "confirm_allow_only": True,
    })

    assert status == 200
    assert repository.get_profile("slow") == replace(
        PROFILE, website_mode="allow_only", website_domains=names + ("new.example",),
    )


@pytest.mark.parametrize("action", ("single", "group"))
def test_missing_profile_remains_a_bad_request(control, action):
    repository, server, _ = control
    before = snapshot(repository)

    status, _ = request(server, payload(action, profile_id="missing", confirm_allow_only=True))

    assert status == 400
    assert snapshot(repository) == before


@pytest.mark.parametrize("action", ("single", "group"))
@pytest.mark.parametrize("failure,status", (
    (StorageError("save failed"), 400), (sqlite3.OperationalError("write failed"), 503),
))
def test_a_failure_after_the_profile_write_rolls_back_profile_version_and_history(
    control, monkeypatch, action, failure, status
):
    repository, server, _ = control
    before = snapshot(repository)
    save_profile = repository.save_profile

    def write_then_fail(profile):
        save_profile(profile)
        raise failure

    monkeypatch.setattr(repository, "save_profile", write_then_fail)

    response_status, _ = request(server, payload(action, confirm_allow_only=True))

    assert response_status == status
    assert snapshot(repository) == before


def test_a_busy_transaction_returns_a_controlled_failure_without_changes(control):
    repository, server, database = control
    before = snapshot(repository)
    repository._connection.execute("PRAGMA busy_timeout = 0")
    with ConfigRepository(database) as rival:
        rival._connection.execute("BEGIN IMMEDIATE")
        try:
            status, _ = request(server, payload("single", confirm_allow_only=True))
            assert status == 503
            assert snapshot(repository) == before
        finally:
            rival._connection.rollback()


def test_a_second_connection_cannot_change_mode_between_the_checked_read_and_save(
    control, monkeypatch
):
    repository, server, database = control
    listed = replace(PROFILE, website_mode="allow_only")
    repository.save_profile(listed)
    rival_ready = threading.Event()
    checked_read = threading.Event()
    attempted = threading.Event()
    request_finished = threading.Event()
    outcomes = []
    failures = []

    def rival_writer():
        try:
            with ConfigRepository(database) as rival:
                rival._connection.execute("PRAGMA busy_timeout = 0")
                rival_ready.set()
                assert checked_read.wait(timeout=2)
                try:
                    rival.save_profile(replace(listed, name="Rival", website_mode="unrestricted"))
                except sqlite3.OperationalError as error:
                    assert "locked" in str(error)
                    outcomes.append("blocked until the request finishes")
                    rival._connection.rollback()
                else:
                    outcomes.append("saved between the checked read and save")
                finally:
                    attempted.set()
                assert request_finished.wait(timeout=2)
                rival.save_profile(replace(
                    listed, name="After request", website_mode="unrestricted",
                ))
        except BaseException as error:
            failures.append(error)
            rival_ready.set()
            attempted.set()

    get_profile = repository.get_profile
    gated = False

    def read_then_attempt_rival_write(profile_id):
        nonlocal gated
        profile = get_profile(profile_id)
        if profile_id == "slow" and not gated:
            gated = True
            checked_read.set()
            assert attempted.wait(timeout=2)
        return profile

    monkeypatch.setattr(repository, "get_profile", read_then_attempt_rival_write)
    thread = threading.Thread(target=rival_writer, daemon=True)
    thread.start()
    try:
        assert rival_ready.wait(timeout=2)

        status, _ = request(server, payload("single"))

        assert status == 200
        assert failures == []
        assert outcomes == ["blocked until the request finishes"]
        assert get_profile("slow") == replace(
            listed, website_domains=("old.example", "added.example"),
        )
    finally:
        checked_read.set()
        request_finished.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert failures == []
    assert get_profile("slow").name == "After request"
    assert get_profile("slow").website_mode == "unrestricted"
