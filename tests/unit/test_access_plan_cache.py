"""Reuse saved access rules without retaining staged changes or live network state."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import FrozenInstanceError, replace
from ipaddress import IPv4Address
from unittest.mock import patch

import pytest

from jittersim.config.models import (
    AccessOptions,
    DataLimit,
    Device,
    DeviceGroup,
    Impairment,
    MacPolicy,
    Profile,
    SavedSettings,
    SiteBlock,
    UnaffectedDevice,
    ValidationError,
    WebsitePolicy,
)
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine import access, general, runtime, website
from jittersim.engine.service import compile_stored_plan


@pytest.fixture
def repository():
    with ConfigRepository(":memory:") as saved:
        saved.replace_settings(SavedSettings(
            profiles=(Profile("p", "P"),),
            devices=tuple(Device(f"d{i}", f"D{i}", f"192.168.50.{i + 10}", "p",
                                 f"02:00:00:00:01:{i:02x}") for i in range(15)),
            website_policy=WebsitePolicy(blocked_domains=tuple(
                f"ads{i:04d}.tag-0301.example.com" for i in range(2000))),
        ))
        yield saved


def test_repeated_content_reuses_one_plan_with_fresh_payloads(repository):
    with patch.object(access, "build_access_plan", wraps=access.build_access_plan) as build:
        original = repository.access_plan()
        for _ in range(3):
            assert repository.access_plan() is original
        repository.save_profile(Profile("p", "Renamed profile"))
        assert repository.access_plan() is original
        assert build.call_count == 1

    payload = original.payload()
    payload["options"]["enabled"] = False
    payload["clients"][0].pop("address")
    payload["websites"]["blocked_domains"] = ("changed.example.com",)
    assert repository.access_plan().payload() == access.build_access_plan(repository.access_settings()).payload()
    with pytest.raises(FrozenInstanceError):
        original.options.enabled = False
    repository.close()
    assert repository._access_plan_cache is None


def test_policy_device_group_and_address_edits_replace_the_cached_content(repository):
    previous = repository.access_plan()
    changes = (
        lambda: repository.save_website_policy(WebsitePolicy(blocked_domains=("new.example.com",))),
        lambda: repository.save_mac_policy(MacPolicy("denylist", denylist=("02:00:00:00:02:00",))),
        lambda: repository.save_access_policies(None, None, options=AccessOptions(dns_failure_percent=10)),
        lambda: repository.save_device(replace(repository.get_device("d0"), mac_address="02:00:00:00:02:01")),
        lambda: repository.move_device_addresses({"d0": ("192.168.50.10", "192.168.50.40")}),
        lambda: repository.save_device_group(DeviceGroup("g", "G", ("d0",))),
        lambda: repository.save_access_policies(None, None, blocks=(
            SiteBlock("b", "B", ("group.example.com",), group_id="g"),)),
        lambda: repository.save_device_group(DeviceGroup("g", "G", ("d1",))),
        lambda: repository.save_access_policies(None, None, blocks=()),
        lambda: repository.delete_device_group("g"),
        lambda: repository.save_device(Device("new", "New", "192.168.50.41", "p")),
        lambda: repository.delete_device("new"),
        lambda: repository.delete_device("d0"),
    )
    for change in changes:
        change()
        current = repository.access_plan()
        assert current is not previous
        assert current == access.build_access_plan(repository.access_settings())
        previous = current
    assert len(current.clients) == 14
    assert current.websites.blocked_domains == ("new.example.com",)


def test_settings_replacement_and_nested_rollbacks_compare_staged_content(repository):
    original = repository.saved_settings()
    imported = replace(original, website_policy=WebsitePolicy(blocked_domains=("imported.example.com",)))
    repository.replace_settings(imported)
    assert repository.access_plan().websites == imported.website_policy
    repository.replace_settings(original)
    initial = repository.access_plan()

    with pytest.raises(RuntimeError, match="outer rollback"):
        with repository.transaction():
            repository.replace_settings(imported)
            assert repository.access_plan().websites == imported.website_policy
            abandoned_version = repository.get_runtime_state().configuration_version
            with pytest.raises(RuntimeError, match="inner rollback"):
                with repository.transaction():
                    repository.save_access_policies(None, None, options=AccessOptions(enabled=False))
                    staged = repository.access_plan()
                    assert not staged.options.enabled
                    assert repository.access_plan() is staged
                    raise RuntimeError("inner rollback")
            assert repository.access_plan().options.enabled
            assert repository.access_plan().websites == imported.website_policy
            raise RuntimeError("outer rollback")

    assert repository.access_plan() == initial
    # Repeat the same component saves so the rolled-back version is reused.
    different = replace(original, website_policy=WebsitePolicy(blocked_domains=("different.example.com",)))
    repository.replace_settings(different)
    assert repository.get_runtime_state().configuration_version == abandoned_version
    assert repository.access_plan().websites == different.website_policy


def test_invalid_staged_policy_and_malformed_reads_cannot_use_a_warm_entry(repository):
    initial = repository.access_plan()
    with pytest.raises(ValidationError, match="combinations"):
        with repository.transaction():
            repository.save_access_policies(None, None, blocks=tuple(
                SiteBlock(f"b{i}", f"B{i}", (f"extra{i}.example.com",), device_id=f"d{i}")
                for i in range(8)))
            repository.access_plan()
    assert repository.access_plan() is initial
    with pytest.raises(StorageError, match="access options"):
        with repository.transaction():
            repository._connection.execute("UPDATE network_policies SET access_options_json = 'invalid' WHERE id = 1")
            repository.access_plan()
    assert repository.access_plan() is initial


def test_repository_entries_are_separate_and_other_connection_edits_are_read(tmp_path):
    path = tmp_path / "settings.db"
    with ConfigRepository(path) as first, ConfigRepository(path) as second, ConfigRepository(":memory:") as other:
        first.save_website_policy(WebsitePolicy(blocked_domains=("first.example.com",)))
        other.save_website_policy(WebsitePolicy(blocked_domains=("other.example.com",)))
        assert first.get_runtime_state().configuration_version == other.get_runtime_state().configuration_version
        initial = second.access_plan()
        assert initial.websites != other.access_plan().websites
        first.save_website_policy(WebsitePolicy(blocked_domains=("second.example.com",)))
        assert second.access_plan().websites == first.access_plan().websites
        assert second.access_plan() is not initial
        # Every lookup reads content even when a maintenance edit did not bump the version.
        first._connection.execute("UPDATE network_policies SET website_blocked_json = ? WHERE id = 1",
                                  (json.dumps(["raw.example.com"]),))
        first._connection.commit()
        assert second.access_plan().websites.blocked_domains == ("raw.example.com",)
    with ConfigRepository(path) as reopened:
        assert reopened._access_plan_cache is None
        assert reopened.access_plan().websites.blocked_domains == ("raw.example.com",)


def test_same_repository_save_waits_for_a_plan_being_built(repository):
    entered, release, writing = threading.Event(), threading.Event(), threading.Event()
    original_build = access.build_access_plan

    def held_build(settings):
        entered.set()
        assert release.wait(5)
        return original_build(settings)

    def save():
        writing.set()
        repository.save_website_policy(WebsitePolicy(blocked_domains=("changed.example.com",)))

    with patch.object(access, "build_access_plan", side_effect=held_build), ThreadPoolExecutor(2) as workers:
        reading = workers.submit(repository.access_plan)
        try:
            assert entered.wait(5)
            saving = workers.submit(save)
            assert writing.wait(5)
            with pytest.raises(TimeoutError):
                saving.result(timeout=0.05)
        finally:
            release.set()
        assert len(reading.result(timeout=5).websites.blocked_domains) == 2000
        saving.result(timeout=5)
    assert repository.access_plan().websites.blocked_domains == ("changed.example.com",)


def test_engine_builds_reuse_access_rules_while_dns_and_quota_inputs_change(repository, monkeypatch):
    repository.save_website_policy(WebsitePolicy(blocked_domains=("engine-cache.example.com",)))
    repository.save_profile(Profile("limited", "Limited", upload=Impairment(delay_ms=900)))
    repository.save_data_limit(DataLimit("d0", "limited", 100))
    address = [IPv4Address("203.0.113.10")]

    def resolve(*, domains, resolver=None):
        return tuple(address) if domains else ()

    monkeypatch.setattr(website, "resolve_domains", resolve)
    monkeypatch.setattr(general, "resolve_domains", resolve)
    monkeypatch.setattr(runtime, "resolve_domains", resolve)
    scheduler = runtime.RuntimeScheduler(database_path=":memory:", helper_client=None)
    with patch.object(access, "build_access_plan", wraps=access.build_access_plan) as build:
        plans = (
            compile_stored_plan(repository=repository),
            general.compile_general_plan(repository=repository, profile=repository.get_profile("p")),
            scheduler.prepare_plan(repository=repository, now=100).plan,
        )
        address[0] = IPv4Address("203.0.113.20")
        repository.record_data_usage("d0", 100)
        changed = (
            compile_stored_plan(repository=repository),
            general.compile_general_plan(repository=repository, profile=repository.get_profile("p")),
            scheduler.prepare_plan(repository=repository, now=101).plan,
        )
        assert build.call_count == 1
    for before, after in zip(plans, changed):
        assert before.access is after.access
        assert before.global_blocked_addresses == (IPv4Address("203.0.113.10"),)
        assert after.global_blocked_addresses == (IPv4Address("203.0.113.20"),)
    for plan in (changed[0], changed[2]):
        assert next(q for q in plan.queues if q.device_id == "d0").impairment.delay_ms == 900


def test_live_client_identity_changes_do_not_reuse_an_entire_network_plan(repository):
    repository.save_website_policy(WebsitePolicy())
    repository.save_unaffected_device(UnaffectedDevice("02:00:00:00:ff:ff"))
    device = repository.get_device("d0")
    snapshot = {"association_status_available": True, "devices": [{
        "association_verified": True, "mac_address": device.mac_address,
        "ipv4_address": "192.168.50.10",
    }]}
    first = compile_stored_plan(repository=repository, connected_snapshot=snapshot)
    snapshot["devices"][0]["ipv4_address"] = "192.168.50.40"
    second = compile_stored_plan(repository=repository, connected_snapshot=snapshot)
    assert first.access is second.access
    assert first.mark_rules[0].ipv4_address == IPv4Address("192.168.50.10")
    assert second.mark_rules[0].ipv4_address == IPv4Address("192.168.50.40")
