"""Actual actions remain distinct from questions, list membership and DNS errors."""

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.dns_outcomes import (
    DNS_OUTCOME_WINDOW_SECONDS,
    MAX_DNS_OUTCOME_BATCH,
    MAX_DNS_OUTCOME_ROWS,
    DnsDecision,
    dns_outcome_transaction,
    eligible_dns_identities,
)
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import DomainObservation, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.networking.dns_decisions import DecisionLines

MAC = "02:00:00:00:00:01"
OTHER = "02:00:00:00:00:02"
IP = "192.168.50.10"
GENERATION = "a" * 64
EPOCH = "b" * 32
SESSION = "c" * 32


def snapshot(**changes):
    from jittersim.networking.helper import _merge_connected_device_output

    # Exercise the real helper producer rather than a hand-invented identity row.
    devices = _merge_connected_device_output(
        f"Station {MAC} (on wlan0)\n\tsignal: -40 dBm\n",
        f"{IP} dev wlan0 lladdr {MAC} REACHABLE\n",
        "",
        observed_at=100,
    )
    result = {"association_status_available": True, "devices": devices}
    result.update(changes)
    return result


def test_actual_helper_payload_is_eligible_and_british_alias_is_not():
    actual = snapshot()
    assert actual["devices"][0]["neighbor_state"] == "reachable"
    assert "neighbour_state" not in actual["devices"][0]
    assert eligible_dns_identities(actual, set()) == {IP: MAC}
    alias = dict(actual["devices"][0])
    alias["neighbour_state"] = alias.pop("neighbor_state")
    assert eligible_dns_identities(snapshot(devices=[alias]), set()) == {}
    alias["neighbor_state"] = "stale"
    assert eligible_dns_identities(snapshot(devices=[alias]), set()) == {}


def test_duplicate_mac_without_an_ip_cannot_make_the_other_row_unique():
    actual = snapshot()
    duplicate = dict(actual["devices"][0], ipv4_address=None, neighbor_state=None)
    assert eligible_dns_identities(snapshot(devices=actual["devices"] + [duplicate]), set()) == {}


def action(name="blocked.example", address=IP, generation=GENERATION):
    return (
        f"[100] jittersim-dns-{generation}[123:0] info: "
        f"blocked.example. always_nxdomain {address}@50000 {name}. A IN\n"
    ).encode()


def reply(rcode="NOERROR"):
    return (
        f"[100] jittersim-dns-{GENERATION}[123:0] reply: "
        f"{IP} blocked.example. A IN {rcode} 0.000100 1 52\n"
    ).encode()


def begin(repository, now=100):
    values = dict(
        epoch=EPOCH,
        session=SESSION,
        generation=repository.access_plan().digest(),
        producer_started_ns=time.monotonic_ns(),
        expected_version=repository.get_runtime_state().configuration_version,
        snapshot=snapshot(),
        now=now,
    )
    repository.begin_dns_outcome_epoch(**values)
    return {key: values[key] for key in ("epoch", "session", "generation", "expected_version")}


def event(sequence=1, kind="blocked_decisions", **changes):
    values = dict(
        sequence=sequence, ipv4_address=IP, domain="blocked.example", kind=kind, observed_at=100
    )
    values.update(changes)
    return DnsDecision(**values)


def record(repository, context, events=None, **changes):
    values = dict(snapshot=snapshot(), now=100, **context)
    values.update(changes)
    repository.record_dns_decisions([event()] if events is None else events, **values)


def test_actual_action_and_reply_are_independent_not_inferred_from_nxdomain():
    parser = DecisionLines()
    events = parser.feed(action() + reply() + reply("NXDOMAIN") + reply("SERVFAIL"))
    assert [item.event.kind for item in events] == [
        "blocked_decisions",
        "answered_noerror",
        "other_dns_errors",
        "other_dns_errors",
    ]
    assert all(item.generation == GENERATION for item in events)
    assert len({item.event.sequence for item in events}) == 4
    assert parser.feed(b"192.168.50.10 blocked.example NXDOMAIN\n") == ()
    assert parser.feed(action().replace(b"always_nxdomain", b"static")) == ()


def test_partial_lines_limits_and_reset_never_reinterpret_a_truncated_suffix():
    parser = DecisionLines()
    valid = action()
    assert parser.feed(valid[:30]) == ()
    assert parser.feed(valid[30:])[0].event.kind == "blocked_decisions"
    assert parser.feed(b"x" * 1025 + valid) == ()
    assert parser.feed(valid)[0].event.sequence == 2
    assert len(parser.buffer) <= 1024
    parser.feed(valid[:30])
    parser.clear()
    assert parser.feed(valid[30:]) == ()
    with pytest.raises(ValueError):
        parser.feed(b"x" * 4097)
    assert parser.feed(action(name="bad?<name>")) == ()
    assert parser.rejected >= 2


@pytest.mark.parametrize(
    "changes",
    [
        {"stale": True},
        {"association_status_available": False},
        {"devices": []},
        {"devices": [{"ipv4_address": IP, "mac_address": MAC, "association_verified": False}]},
        {"devices": snapshot()["devices"] * 2},
        {"devices": snapshot()["devices"] + [{"ipv4_address": IP, "mac_address": OTHER}]},
        {
            "devices": snapshot()["devices"]
            + [{"ipv4_address": "192.168.50.11", "mac_address": MAC}]
        },
        {
            "devices": [
                {
                    "ipv4_address": IP,
                    "mac_address": MAC,
                    "association_verified": True,
                    "neighbor_state": "stale",
                }
            ]
        },
    ],
)
def test_unknown_stale_ambiguous_or_unreachable_subjects_are_excluded(changes):
    assert eligible_dns_identities(snapshot(**changes), set()) == {}
    assert eligible_dns_identities(snapshot(), {MAC}) == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"sequence": True},
        {"sequence": -1},
        {"sequence": 2**53},
        {"observed_at": True},
        {"domain": "bad\n.example"},
        {"kind": "inferred_block"},
        {"ipv4_address": "garbage"},
    ],
)
def test_event_validation_is_bounded(changes):
    with pytest.raises(ValueError):
        event(**changes)


def test_memory_only_counts_replay_clock_and_snapshot_filtering():
    with ConfigRepository(":memory:") as repository:
        version = repository.get_runtime_state().configuration_version
        context = begin(repository)
        record(repository, context, [event(), event(), event(2, "answered_noerror")])
        rows, health = repository.dns_outcome_snapshot(snapshot=snapshot(), now=100)
        assert rows[(IP, MAC, "blocked.example")] == {
            "blocked_decisions": 1,
            "answered_noerror": 1,
            "other_dns_errors": 0,
            "last_observed_at": 100,
        }
        assert health["status"] == "partial" and health["lower_bound"]
        assert not health["coverage_complete"] and health["dropped_events"] is None
        record(repository, context, [event(3, observed_at=101)])
        assert repository.dns_outcome_snapshot(snapshot=snapshot(), now=100)[0] == rows
        assert repository.get_runtime_state().configuration_version == version
        assert repository.list_domain_observations() == ()
        assert repository.saved_settings().devices == ()
        assert repository.dns_outcome_snapshot(snapshot=snapshot(stale=True), now=100)[0] == {}
        assert repository._dns_outcomes.rows == {}


def test_capacity_batch_and_finite_epoch_window():
    with ConfigRepository(":memory:") as repository:
        context = begin(repository)
        with pytest.raises(ValueError):
            record(repository, context, [event()] * (MAX_DNS_OUTCOME_BATCH + 1))
        for index in range(MAX_DNS_OUTCOME_ROWS + 1):
            record(repository, context, [event(index, domain=f"site{index}.example")])
        assert len(repository._dns_outcomes.rows) == MAX_DNS_OUTCOME_ROWS
        assert (
            repository.dns_outcome_snapshot(snapshot=snapshot(), now=100)[1]["coverage_reason"]
            == "aggregate_limit_reached"
        )
        rows, health = repository.dns_outcome_snapshot(
            snapshot=snapshot(), now=101 + DNS_OUTCOME_WINDOW_SECONDS
        )
        assert rows == {} and health["status"] == "unavailable" and health["epoch"] is None


def test_protection_invalidates_partial_source_and_rejects_old_producer_epoch():
    with ConfigRepository(":memory:") as repository:
        parser = DecisionLines()
        repository._dns_outcomes._clear_source = parser.clear
        context = begin(repository)
        old_started = time.monotonic_ns()
        record(repository, context)
        parser.feed(action()[:20])
        repository.save_unaffected_device(UnaffectedDevice(MAC))
        assert not parser.buffer
        assert repository._dns_outcomes.rows == {}
        record(repository, context)
        assert repository.dns_outcome_snapshot(snapshot=snapshot(), now=100)[0] == {}
        repository.delete_unaffected_device(MAC)
        with pytest.raises(ValueError, match="restart"):
            repository.begin_dns_outcome_epoch(
                epoch="d" * 32,
                session=SESSION,
                generation=repository.access_plan().digest(),
                producer_started_ns=old_started,
                expected_version=repository.get_runtime_state().configuration_version,
                snapshot=snapshot(),
                now=100,
            )


def test_second_repository_protection_commit_is_serialized_with_aggregation(tmp_path):
    path = tmp_path / "settings.db"
    with ConfigRepository(path) as first, ConfigRepository(path) as second:
        context = begin(first)
        entered, release, protecting_started = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        original = first._dns_outcomes.add

        def held_add(*args, **kwargs):
            entered.set()
            assert release.wait(3)
            return original(*args, **kwargs)

        first._dns_outcomes.add = held_add
        with ThreadPoolExecutor(2) as workers:
            pending = workers.submit(record, first, context)
            assert entered.wait(3)

            def protect():
                protecting_started.set()
                second.save_unaffected_device(UnaffectedDevice(MAC))

            protecting = workers.submit(protect)
            assert protecting_started.wait(3)
            assert not protecting.done()
            release.set()
            pending.result(timeout=3)
            protecting.result(timeout=3)
        rows, health = first.dns_outcome_snapshot(snapshot=snapshot(), now=100)
        assert rows == {} and first._dns_outcomes.rows == {}
        assert health["status"] == "unavailable"
        record(first, context)
        assert not first._dns_outcomes.rows


def test_external_write_lock_drops_instead_of_waiting(tmp_path):
    path = tmp_path / "settings.db"
    with ConfigRepository(path) as repository, sqlite3.connect(path) as external:
        context = begin(repository)
        previous = repository._connection.execute("PRAGMA busy_timeout").fetchone()[0]
        external.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            record(repository, context)
        assert time.monotonic() - started < 0.5
        assert repository._connection.execute("PRAGMA busy_timeout").fetchone()[0] == previous
        assert repository._dns_outcomes.rows == {}
        external.rollback()
        with dns_outcome_transaction(repository):
            assert repository._connection.in_transaction


def test_api_preserves_query_counts_membership_and_unknown_defaults():
    with ConfigRepository(":memory:") as repository:
        repository.save_website_policy(WebsitePolicy(blocked_domains=("blocked.example",)))
        repository.save_domain_observations(
            [DomainObservation(IP, "blocked.example", 9, 100, 100, MAC)], now=100
        )
        server = ControlServer.__new__(ControlServer)
        server.repository = repository
        server.helper_client = type(
            "Helper", (), {"connected_device_snapshot": lambda self: snapshot()}
        )()
        status, _, body = server._get_observations("")
        payload = json.loads(body)
        row = payload["devices"][0]["domains"][0]
        assert status == 200 and row["query_count"] == 9 and row["globally_blocked"] is True
        assert row["dns_outcomes"] is None
        assert payload["dns_outcome_collection"]["status"] == "unavailable"
        assert payload["dns_outcome_collection"]["epoch"] is None
        context = begin(repository, now=int(time.time()))
        record(repository, context, [event(observed_at=int(time.time()))], now=int(time.time()))
        row = json.loads(server._get_observations("")[2])["devices"][0]["domains"][0]
        assert row["query_count"] == 9 and row["dns_outcomes"]["blocked_decisions"] == 1
        repository.save_unaffected_device(UnaffectedDevice(MAC))
        assert "blocked.example" not in server._get_observations("")[2].decode()


def test_reopen_and_backup_never_retain_outcome_history(tmp_path):
    database = tmp_path / "settings.db"
    with ConfigRepository(database) as repository:
        context = begin(repository)
        record(repository, context)
        (tmp_path / "backup.db").write_bytes(repository.backup_bytes())
    for path in (database, tmp_path / "backup.db"):
        with ConfigRepository(path) as repository:
            rows, health = repository.dns_outcome_snapshot(snapshot=snapshot(), now=100)
            assert rows == {} and health["status"] == "unavailable"
            assert not repository._connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%dns_outcome%'"
            ).fetchall()
