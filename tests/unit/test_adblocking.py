"""Protect optional filtering, bounded lists and recovery without lookup history."""

import hashlib
import sqlite3
import time
from dataclasses import replace
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest

from jittersim.config.adblocking import (
    MAX_LIST_BYTES,
    ActiveBlocking,
    DomainPolicy,
    ad_domain,
    allowed_domains,
    blocking_macs,
    domain_bytes,
    parse_hosts,
)
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import AccessOptions, SiteBlock, WebsitePolicy
from tests.adblocking_support import provision_guard
from jittersim.config.storage import ConfigRepository
from jittersim.config.transfer import check_settings, settings_to_payload
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.networking.access import read_access
from jittersim.networking.adblocking import (
    AdController,
    AdRouting,
    AdStore,
    atomic_write,
    download_bytes,
    download_domains,
    filtering_config,
    read_json,
    write_json,
)
from jittersim.networking.executor import FakeExecutor

ALICE = "02:00:00:00:00:01"
BOB = "02:00:00:00:00:02"


@pytest.fixture
def store(tmp_path):
    return AdStore(
        AdRouting("ads0", FakeExecutor()),
        path=tmp_path / "policy.json",
        lock_path=tmp_path / "lock",
        active_path=tmp_path / "active.json",
        status_path=tmp_path / "status.json",
        intent_path=tmp_path / "intent.json", panic_guard=provision_guard(tmp_path),
    )


def applied(store, tmp_path, *, now=None, domains=("advert.test",), allowances=()):
    now = time.monotonic() if now is None else now
    data = domain_bytes(domains)
    atomic_write(tmp_path / "domains", data)
    state = {
        "mac_addresses": [ALICE],
        "allowed_domains": allowances,
        "expires_at": now + 5,
        "list_digest": hashlib.sha256(data).hexdigest(),
        "generation": store.read()["generation"],
    }
    write_json(store.active_path, state)
    return state


def controller(store, tmp_path):
    item = AdController(
        store,
        domains_path=tmp_path / "domains",
        info_path=tmp_path / "info",
        config_path=tmp_path / "dns.conf",
        dns_user="nobody",
        downloader=lambda: ("advert.test",),
        access_path=tmp_path / "access-policy",
    )
    item.next_fetch = time.monotonic() + 86400
    item.update_request = store.read()["update_request"]
    return item


def test_protection_defaults_off_survives_rename_backup_and_cascades(tmp_path):
    path = tmp_path / "config.db"
    with ConfigRepository(path) as repo:
        repo.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
        assert repo.list_ad_blocking_devices() == ()
        with pytest.raises(ValueError):
            repo.set_ad_blocking(BOB, True)
        repo.set_ad_blocking(ALICE, True)
        repo.set_ad_blocking_allowances(["EXAMPLE.COM.", "example.com"])
        repo.save_unaffected_device(UnaffectedDevice(ALICE, "Renamed", 1))
        assert repo.list_ad_blocking_devices() == (ALICE,)
        backup = tmp_path / "backup.db"
        backup.write_bytes(repo.backup_bytes())
        with ConfigRepository(backup) as copy:
            assert copy.list_ad_blocking_devices() == (ALICE,)
            assert copy.list_ad_blocking_allowances() == ("example.com",)
        repo.delete_unaffected_device(ALICE)
        assert repo.list_ad_blocking_devices() == ()
    # The previous schema can still read protection without learning new columns.
    with sqlite3.connect(backup) as db:
        assert db.execute(
            "SELECT mac_address, display_name, created_at FROM unaffected_devices"
        ).fetchone() == (ALICE, "Renamed", 1)


def test_database_upgrade_and_invalid_changes_are_atomic(tmp_path):
    path = tmp_path / "old.db"
    with ConfigRepository(path) as repo:
        repo.save_unaffected_device(UnaffectedDevice(ALICE, "", 1))
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE ad_blocking_devices")
        db.execute("DROP TABLE ad_blocking_allowances")
    with ConfigRepository(path) as repo:
        assert repo.list_ad_blocking_devices() == ()
        repo.set_ad_blocking_allowances(["working.test"])
        for value in ("false", 1, None):
            with pytest.raises(ValueError):
                repo.set_ad_blocking(ALICE, value)
        with pytest.raises(ValueError):
            repo.set_ad_blocking_allowances(["example.com", "address=/evil.test/"])
        assert repo.list_ad_blocking_allowances() == ("working.test",)
        with pytest.raises(RuntimeError), repo.transaction():
            repo.set_ad_blocking(ALICE, True)
            raise RuntimeError("rollback")
        assert repo.list_ad_blocking_devices() == ()


def test_saved_settings_roundtrip_keeps_choices_and_invalid_replacement_is_atomic(tmp_path):
    with ConfigRepository(tmp_path / "settings.db") as repo:
        repo.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
        repo.set_ad_blocking(ALICE, True)
        repo.set_ad_blocking_allowances(["working.example.net"])
        saved = repo.saved_settings()
        payload = settings_to_payload(saved, exported_at=0)
        assert payload["ad_blocking_devices"] == [ALICE]
        assert payload["ad_blocking_allowances"] == ["working.example.net"]
        assert not any("intent" in key or "running" in key for key in payload)
        repo.replace_settings(saved)
        assert repo.list_ad_blocking_devices() == (ALICE,)
        assert repo.list_ad_blocking_allowances() == ("working.example.net",)
        for invalid in (
            replace(saved, ad_blocking_devices=(BOB,)),
            replace(saved, ad_blocking_devices=(ALICE,) * 257),
            replace(saved, ad_blocking_allowances=("evil\nconfig",)),
            replace(saved, ad_blocking_allowances=("working.example.net",) * 257),
        ):
            with pytest.raises(ValueError):
                check_settings(invalid, max_traffic_classes=256, max_service_domains=512)
            with pytest.raises(ValueError):
                repo.replace_settings(invalid)
            assert repo.list_ad_blocking_devices() == (ALICE,)
            assert repo.list_ad_blocking_allowances() == ("working.example.net",)


@pytest.mark.parametrize(
    "value",
    [
        "localhost",
        "192.168.50.1",
        "*.test",
        "a.test\nserver=evil",
        "https://example.com",
        None,
        "bad..test",
    ],
)
def test_domains_reject_configuration_injection(value):
    with pytest.raises(ValueError):
        ad_domain(value)


def test_list_limits_and_hosts_parsing():
    assert parse_hosts(b"0.0.0.0 0.0.0.0\n0.0.0.0 ads_with_underscore.test\n") == (
        "ads_with_underscore.test",
    )
    assert parse_hosts(b"# header\n127.0.0.1 localhost\n0.0.0.0 Advert.Test advert.test\n") == (
        "advert.test",
    )
    for data in (b"0.0.0.0 invalid\n", b"server=/evil.test/8.8.8.8\n", b"\xff"):
        with pytest.raises(ValueError):
            parse_hosts(data)
    with pytest.raises(ValueError):
        parse_hosts(b"#" * (MAX_LIST_BYTES + 1))
    with pytest.raises(ValueError):
        allowed_domains(["example.com"] * 257)
    with pytest.raises(ValueError):
        blocking_macs([ALICE] * 257)
    assert blocking_macs([ALICE.upper().replace(":", "-")]) == (ALICE,)


def test_domain_suffixes_and_specific_allowance_match_dns_config():
    policy = DomainPolicy(
        frozenset({"advert.test", "ads.content.test"}),
        frozenset({"good.advert.test", "content.test"}),
    )
    assert policy.blocked("CHILD.advert.test.")
    assert not policy.blocked("good.advert.test")
    assert not policy.blocked("child.good.advert.test")
    assert not policy.blocked("ads.content.test")
    assert not policy.blocked("notadvert.test")
    assert not policy.blocked("192.168.50.1")
    config = filtering_config(policy.domains, policy.allowances, AccessPlan()).decode()
    assert 'local-zone: "advert.test." always_nxdomain' in config
    assert 'local-zone: "ads.content.test."' not in config
    assert 'local-zone: "good.advert.test." always_transparent' in config
    assert "log-queries: no" in config and 'logfile: "/dev/null"' in config
    assert "msg-cache-size: 4m" in config and "num-queries-per-thread: 256" in config
    assert "view-first: yes" in config and 'local-zone: "." noview' in config


def test_proxy_policy_expires_and_rejects_corrupt_or_mismatched_state(store, tmp_path):
    state = applied(store, tmp_path, now=100)
    clock = Mock(return_value=100)
    active = ActiveBlocking(store.active_path, tmp_path / "domains", clock=clock)
    assert active.blocks(ALICE, "advert.test")
    assert not active.blocks(BOB, "advert.test")
    assert not active.blocks(None, "advert.test")
    clock.return_value = 105
    assert not active.blocks(ALICE, "advert.test")
    for state in (
        {**state, "expires_at": 1000},
        {**state, "expires_at": True},
        {**state, "list_digest": "wrong"},
        [],
        {"expires_at": 101},
    ):
        write_json(store.active_path, state)
        clock.return_value = 100
        assert not active.enabled()
    store.active_path.write_bytes(b"not json")
    assert not active.enabled()


def test_proxy_policy_can_refresh_concurrently_without_mixing_identity(store, tmp_path):
    state = applied(store, tmp_path)
    active = ActiveBlocking(store.active_path, tmp_path / "domains")

    def read():
        for _ in range(100):
            active.blocks(ALICE, "advert.test")
            assert not active.blocks(BOB, "advert.test")

    with ThreadPoolExecutor(max_workers=4) as pool:
        readers = [pool.submit(read) for _ in range(3)]
        for index in range(30):
            write_json(
                store.active_path,
                {**state, "allowed_domains": ["advert.test"] if index % 2 else []},
            )
        for future in readers:
            future.result(timeout=5)


def test_saved_policy_pause_resume_and_expired_state(store, tmp_path):
    store.configure([ALICE], [], resume=True)
    applied(store, tmp_path)
    assert store.status()["ready"]
    store.pause()
    assert store.status()["paused"] and not store.status()["ready"]
    assert store.read()["mac_addresses"] == (ALICE,)
    store.configure([BOB], ["working.test"])
    assert store.read()["running"] is False
    store.resume()
    assert store.read()["mac_addresses"] == (BOB,)
    assert store.read()["allowed_domains"] == ("working.test",)
    assert not store.status()["paused"] and not store.status()["ready"]
    assert read_json(store.active_path)["expires_at"] == 0
    assert store.path.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".policy.json*"))


def test_maximum_valid_choices_fit_the_owned_state_files(store):
    domains = [
        f"{index:03d}" + "a" * 60 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 61
        for index in range(256)
    ]
    macs = [f"02:00:00:00:00:{index:02x}" for index in range(256)]
    store.configure(macs, domains)
    assert len(store.read()["mac_addresses"]) == 256
    assert len(store.read()["allowed_domains"]) == 256
    assert store.path.stat().st_size > 64 * 1024
    assert store.path.stat().st_size < 128 * 1024


def test_restoring_settings_cannot_reverse_a_durable_stop(store, tmp_path):
    assert store.status()["message"] == "Ad blocking is off."
    assert not store.status()["paused"]
    store.configure([ALICE], ["working.test"], resume=True)
    snapshot = store.path.read_bytes()
    store.pause()
    store.path.write_bytes(snapshot)
    restored = AdStore(
        store.routing, path=store.path, lock_path=store.lock_path,
        active_path=store.active_path, status_path=store.status_path,
        intent_path=store.intent_path, panic_guard=store.panic_guard,
    )
    assert restored.read()["mac_addresses"] == (ALICE,)
    assert restored.read()["allowed_domains"] == ("working.test",)
    assert restored.status()["paused"]
    item = controller(restored, tmp_path)
    with patch("jittersim.networking.adblocking.subprocess.Popen") as child:
        item.tick()
        child.assert_not_called()
        assert item.future is None
    item.close()
    restored.resume()
    assert restored.read()["running"] is True
    restored.intent_path.unlink()
    assert restored.read()["running"] is False
    assert restored.status()["paused"]


def test_invalid_stop_state_never_grants_permission_and_panic_can_replace_it(store):
    store.configure([ALICE], [], resume=True)
    write_json(store.intent_path, {"version": True, "running": True})
    with pytest.raises(ValueError):
        store.read()
    store.pause()
    assert store.read()["running"] is False
    # Save stop intent even when unrelated backed-up settings cannot be read.
    store.resume()
    store.path.write_bytes(b"broken settings")
    store.pause()
    assert read_json(store.intent_path)["running"] is False
    assert read_json(store.active_path)["expires_at"] == 0
    assert store.path.read_bytes() == b"broken settings"


def test_unreadable_ad_settings_do_not_prevent_household_recovery(store):
    store.configure([ALICE], [], resume=True)
    store.path.write_bytes(b"broken settings")
    entered = False
    with store.access_transition():
        entered = True
        assert read_json(store.intent_path)["running"] is False
    assert entered
    assert store.path.read_bytes() == b"broken settings"
    assert read_json(store.active_path)["expires_at"] == 0


def test_settings_restored_during_a_probe_cannot_renew_a_stopped_lease(store, tmp_path):
    store.configure([ALICE], [], resume=True)
    snapshot = store.path.read_bytes()
    applied(store, tmp_path)
    item = controller(store, tmp_path)
    child = Mock()
    child.poll.return_value = None

    def stop_then_restore():
        store.pause()
        store.path.write_bytes(snapshot)
        return True

    with (
        patch("jittersim.networking.adblocking.subprocess.run"),
        patch("jittersim.networking.adblocking.subprocess.Popen", return_value=child),
        patch("jittersim.networking.adblocking.healthy", side_effect=stop_then_restore),
    ):
        item.tick()
    assert store.status()["paused"]
    assert not store.status()["ready"]
    assert read_json(store.active_path)["expires_at"] == 0
    item.close()


def test_older_ad_permission_and_invalid_witness_stay_paused_until_explicit_enable(store):
    store.configure([ALICE], [], resume=True)
    write_json(store.intent_path, {"version": 1, "running": True})
    assert not store.read()["running"]
    store.resume()
    assert store.read()["running"]
    for witness in (None, True, 123, "0" * 63, "X" * 64, "0" * 65):
        write_json(store.intent_path, {"version": 2, "running": True, "panic_witness": witness})
        assert not store.read()["running"]
    store.configure([ALICE], [], resume=True)
    assert store.read()["running"]


def test_a_panic_record_replaced_during_probe_cannot_renew_routing(store, tmp_path):
    store.configure([ALICE], [], resume=True)
    applied(store, tmp_path)
    item = controller(store, tmp_path)
    child = Mock()
    child.poll.return_value = None
    state = store.panic_guard.read()
    with (
        patch("jittersim.networking.adblocking.subprocess.run"),
        patch("jittersim.networking.adblocking.subprocess.Popen", return_value=child),
        patch("jittersim.networking.adblocking.healthy",
              side_effect=lambda: store.panic_guard.write(state) or True),
    ):
        item.tick()
    assert not store.status()["ready"]
    assert read_json(store.active_path)["expires_at"] == 0
    item.close()


def test_access_transition_keeps_panic_and_saved_preferences_on_failure(store, tmp_path):
    store.configure([ALICE], [], resume=True)
    applied(store, tmp_path)
    with pytest.raises(RuntimeError), store.access_transition():
        assert not store.status()["ready"]
        assert store.read()["suspended"]
        store.pause()
        raise RuntimeError("household update failed")
    assert store.read()["mac_addresses"] == (ALICE,)
    assert not store.read()["suspended"]
    assert not store.read()["running"]
    assert read_json(store.active_path)["expires_at"] == 0


def test_filtering_keeps_household_rules_in_views_and_one_shared_advertising_tree():
    plan = AccessPlan(
        options=AccessOptions(enabled=True),
        websites=WebsitePolicy("allow_only", ("content.test",), ("global.test",)),
        blocks=(SiteBlock("private", "Private", ("private.test",), device_id="alice"),),
        clients=(AccessClient("alice", "192.168.50.10", ALICE, ("private",)),),
    )
    config = filtering_config(["advert.test"], ["global.test", "private.test"], plan).decode()
    server, views = config.split("forward-zone:", 1)
    assert server.count('local-zone: "advert.test." always_nxdomain') == 1
    assert 'local-zone: "global.test." always_transparent' in server
    assert 'local-zone: "global.test." always_nxdomain' in views
    assert 'local-zone: "private.test." always_nxdomain' in views
    assert 'local-zone: "." always_nxdomain' in views
    assert 'local-zone: "content.test." noview' in views
    assert 'local-zone: "advert.test."' not in views
    assert 'access-control-view: 192.168.50.10/32 "group1"' in server


def test_nft_rules_are_local_selected_expiring_and_do_not_collect_history(store):
    store.configure([ALICE], [])
    store.routing.refresh([ALICE])
    scripts = "\n".join(command.stdin for command in store.routing.executor.commands)
    assert "timeout 5s" in scripts and "notrack" in scripts
    assert (
        'iifname "ads0" ip saddr 192.168.50.0/24 ip daddr 192.168.50.1 ether saddr @clients'
        in scripts
    )
    assert "counter" not in scripts and "log" not in scripts
    assert "flush ruleset" not in scripts
    assert "5354 drop" in scripts
    with pytest.raises(ValueError):
        AdRouting('ads0"; drop')


def test_healthy_controller_commits_only_current_generation(store, tmp_path):
    store.configure([ALICE], [], resume=True)
    applied(store, tmp_path)
    item = controller(store, tmp_path)
    child = Mock()
    child.poll.return_value = None
    with (
        patch("jittersim.networking.adblocking.subprocess.run"),
        patch("jittersim.networking.adblocking.subprocess.Popen", return_value=child),
        patch("jittersim.networking.adblocking.healthy", return_value=True),
    ):
        item.tick()
        assert store.status()["ready"]
        assert read_json(store.active_path)["mac_addresses"] == [ALICE]
        # Panic can arrive while a health probe is running. Its generation wins.
        with patch(
            "jittersim.networking.adblocking.healthy", side_effect=lambda: store.pause() or True
        ):
            item.tick()
        assert not store.status()["ready"]
        assert read_json(store.active_path)["expires_at"] == 0
    item.close()


def test_failed_dns_validation_has_a_bounded_restart_rate(store, tmp_path):
    store.configure([ALICE], [], resume=True)
    applied(store, tmp_path)
    item = controller(store, tmp_path)
    with patch("jittersim.networking.adblocking.subprocess.run", side_effect=OSError) as check:
        with pytest.raises(OSError):
            item.tick()
        item.tick()
        assert check.call_count == 1
        assert not store.status()["ready"]
    item.close()


def test_unhealthy_or_changed_config_cannot_receive_routing_lease(store, tmp_path):
    store.configure([ALICE], [], resume=True)
    applied(store, tmp_path)
    item = controller(store, tmp_path)
    item._list()
    item.child = Mock()
    item.child.poll.return_value = None
    item.key = (item.digest, (), read_access(item.access_path).digest())
    item.next_start = time.monotonic() + 100
    with patch("jittersim.networking.adblocking.healthy", return_value=False):
        item.tick()
    assert not store.status()["ready"]
    # A changed allowance must not be applied to the old DNS process during cooldown.
    store.configure([ALICE], ["working.test"])
    with patch("jittersim.networking.adblocking.healthy", return_value=True):
        item.tick()
    assert not store.status()["ready"]
    assert read_json(store.active_path)["expires_at"] == 0
    item.close()


def test_failed_update_keeps_list_and_bounds_retries(store, tmp_path):
    store.configure([ALICE], [], resume=True)
    applied(store, tmp_path)
    item = controller(store, tmp_path)
    item._list()
    original = item.domains_path.read_bytes()
    for failure in range(1, 4):
        item.future = Future()
        item.future.set_exception(ValueError("bad list"))
        item._download(store.read(), {})
        assert item.failures == failure
        assert item.next_fetch > time.monotonic()
        assert item.future is None
    assert item.next_fetch >= time.monotonic() + 86399
    assert item.domains_path.read_bytes() == original
    item.close()


def test_download_is_fixed_https_bounded_and_rejects_redirects():
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status_code = 302
    with patch("jittersim.networking.adblocking.requests.get", return_value=response) as get:
        with pytest.raises(ValueError):
            download_bytes()
        assert get.call_args.kwargs == {"stream": True, "timeout": (5, 5), "allow_redirects": False}
        assert get.call_args.args[0].startswith("https://raw.githubusercontent.com/StevenBlack/")
        response.status_code = 200
        response.iter_content.return_value = [b"#" * (MAX_LIST_BYTES + 1)]
        with pytest.raises(ValueError):
            download_bytes()
