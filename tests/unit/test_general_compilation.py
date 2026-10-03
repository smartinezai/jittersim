"""Keep scheduler list values and one lookup budget in general plans."""

from dataclasses import replace
from ipaddress import IPv4Address
import threading
import time

import pytest

from jittersim.config.models import Impairment, Profile, ServiceList, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.engine import website
from jittersim.engine.general import compile_general_plan
from jittersim.engine.service_lists import build_service_list_rules


@pytest.fixture
def repository():
    with ConfigRepository(":memory:") as saved:
        saved.save_profile(Profile("normal", "Normal"))
        saved.save_profile(Profile("video", "Video", upload=Impairment(delay_ms=20)))
        saved.save_service_list(
            ServiceList("streaming", "Streaming", "video", hostnames=("video.example",))
        )
        yield saved


def test_general_compilation_uses_saved_lists_when_none_are_supplied(repository):
    plan = compile_general_plan(repository=repository, profile=repository.get_profile("normal"))

    assert [rule.list_id for rule in plan.service_list_rules] == ["streaming"]
    assert plan.service_list_rules[0].upload_impairment.delay_ms == 20


def test_general_compilation_retains_supplied_roll_and_outage_values(repository):
    rolled = replace(
        build_service_list_rules(repository)[0],
        upload_impairment=Impairment(delay_ms=900),
        outage_active=True,
    )
    plan = compile_general_plan(
        repository=repository,
        profile=repository.get_profile("normal"),
        service_list_rules=(rolled,),
    )

    assert plan.service_list_rules[0].upload_impairment.delay_ms == 900
    assert plan.service_list_rules[0].outage_active


def test_general_compilation_preserves_intentionally_empty_supplied_lists(repository):
    plan = compile_general_plan(
        repository=repository,
        profile=repository.get_profile("normal"),
        service_list_rules=(),
    )

    assert plan.service_list_rules == ()
    assert len(plan.queues) == 2


def test_general_compilation_shares_one_deadline_across_all_website_rules(
    repository, monkeypatch, caplog
):
    names = (
        "blocked-general-budget.example",
        "allowed-general-budget.example",
        "profile-general-budget.example",
    )
    repository.save_website_policy(
        WebsitePolicy(mode="allow_only", blocked_domains=(names[0],), allowed_domains=(names[1],))
    )
    profile = Profile(
        "strict", "Strict", website_mode="allow_only", website_domains=(names[2],)
    )
    repository.save_profile(profile)
    release = threading.Event()
    admitted = []
    original_start = website._start_lookup

    def observed_start(domain, resolver):
        admitted.append(domain)
        return original_start(domain, resolver)

    def held_lookup(*, domain):
        release.wait(5)
        return (IPv4Address("203.0.113.10"),)

    monkeypatch.setattr(website, "RESOLVE_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(website, "_cached_addresses", held_lookup)
    monkeypatch.setattr(website, "_start_lookup", observed_start)
    try:
        plan = compile_general_plan(repository=repository, profile=profile, service_list_rules=())
        assert admitted == [names[0]]
        assert plan.global_blocked_addresses == ()
        assert plan.global_allowed_addresses == ()
        assert plan.general_profile.allowed_addresses == ()
        warnings = [record for record in caplog.records if record.name == website.LOGGER.name]
        assert len(warnings) == 1
        assert all(name in warnings[0].message for name in names)
    finally:
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with website._resolution_cache_lock:
                pending = any(name in website._pending for name in names)
                for name in names:
                    website._recent_timeouts.pop(name, None)
                website._reported.pop(tuple(sorted(names)), None)
            if not pending:
                break
            time.sleep(0.01)
        assert not pending, "general-plan test lookups did not finish"
