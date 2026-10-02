"""Partial access-rule saves must not remove restrictions from other sections."""

import json
from unittest.mock import patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.models import MacPolicy, WebsitePolicy
from jittersim.config.storage import ConfigRepository, StorageError


@pytest.fixture
def server():
    repository = ConfigRepository(":memory:")
    repository.save_mac_policy(MacPolicy(mode="denylist", denylist=("02:00:00:00:00:01",)))
    repository.save_website_policy(
        WebsitePolicy(
            mode="allow_only",
            allowed_domains=("example.com",),
            blocked_domains=("ads.example.com",),
        )
    )
    instance = object.__new__(ControlServer)
    instance.repository = repository
    # write_response_for reads these on every write; a bare server has no scheduler or helper.
    instance.scheduler = None
    instance.helper_client = None
    try:
        yield instance
    finally:
        repository.close()


def saved(server):
    repository = server.repository
    return (
        repository.get_mac_policy(),
        repository.get_website_policy(),
        repository.get_runtime_state().configuration_version,
    )


def post(server, payload):
    status, _, body = server.write_response_for("/api/policies", payload)
    return status, json.loads(body)


def test_website_only_save_preserves_the_device_block_list(server):
    before = saved(server)
    status, response = post(
        server, {"websites": {"mode": "unrestricted", "blocked_domains": ["tracker.example.com"]}}
    )
    after = saved(server)
    assert status == 200
    assert after[0] == before[0]
    assert after[1] == WebsitePolicy(blocked_domains=("tracker.example.com",))
    assert after[2] == before[2] + 1
    assert response["mac"]["denylist"] == ["02:00:00:00:00:01"]


def test_device_only_save_preserves_website_restrictions(server):
    before = saved(server)
    status, response = post(
        server, {"mac": {"mode": "allowlist", "allowlist": ["02:00:00:00:00:02"]}}
    )
    after = saved(server)
    assert status == 200
    assert after[0] == MacPolicy(mode="allowlist", allowlist=("02:00:00:00:00:02",))
    assert after[1] == before[1]
    assert after[2] == before[2] + 1
    assert response["websites"]["mode"] == "allow_only"
    assert response["websites"]["blocked_domains"] == ["ads.example.com"]


def test_no_sections_changes_no_rules_or_version(server):
    before = saved(server)
    status, _ = post(server, {})
    assert status == 200
    assert saved(server) == before


@pytest.mark.parametrize(
    "section,index,default", [("mac", 0, MacPolicy()), ("websites", 1, WebsitePolicy())]
)
def test_explicit_empty_section_resets_only_that_section(server, section, index, default):
    before = saved(server)
    status, _ = post(server, {section: {}})
    after = saved(server)
    assert status == 200
    assert after[index] == default
    assert after[1 - index] == before[1 - index]
    assert after[2] == before[2] + 1


def test_full_save_still_replaces_both_sections(server):
    status, response = post(
        server,
        {
            "mac": {"mode": "disabled"},
            "websites": {"mode": "unrestricted", "blocked_domains": ["tracker.example.com"]},
        },
    )
    assert status == 200
    assert server.repository.get_mac_policy() == MacPolicy()
    assert server.repository.get_website_policy() == WebsitePolicy(
        blocked_domains=("tracker.example.com",)
    )
    assert response["mac"]["denylist"] == []


@pytest.mark.parametrize(
    "section,bad",
    [
        ("mac", None),
        ("mac", []),
        ("mac", {"mode": "unknown"}),
        ("websites", None),
        ("websites", []),
        ("websites", {"blocked_domains": ["invalid host name"]}),
    ],
)
def test_invalid_section_rejects_the_whole_save(server, section, bad):
    before = saved(server)
    payload = {"mac": {}, "websites": {}, section: bad}
    status, _ = post(server, payload)
    assert status == 400
    assert saved(server) == before


def test_failed_second_write_rolls_back_the_first_write(server):
    before = saved(server)
    with patch.object(server.repository, "save_website_policy", side_effect=StorageError("failed")):
        with pytest.raises(StorageError):
            post(server, {"mac": {}, "websites": {}})
    assert saved(server) == before


# The combined save also checks the version the browser loaded and can carry site rules.
# These tests hold both behaviours together with the section-preserving one above.


def test_stale_version_rejects_a_partial_save_and_changes_nothing(server):
    before = saved(server)
    status, response = post(
        server, {"version": before[2] - 1, "websites": {"blocked_domains": ["tracker.example.com"]}}
    )
    assert status == 409
    assert "changed" in response["error"]
    assert saved(server) == before


def test_stale_version_is_checked_even_when_no_section_is_sent(server):
    before = saved(server)
    status, _ = post(server, {"version": before[2] + 5})
    assert status == 409
    assert saved(server) == before


def test_current_version_allows_a_partial_save_that_keeps_the_other_section(server):
    before = saved(server)
    status, _ = post(
        server, {"version": before[2], "websites": {"blocked_domains": ["tracker.example.com"]}}
    )
    after = saved(server)
    assert status == 200
    assert after[0] == before[0]
    assert after[1] == WebsitePolicy(blocked_domains=("tracker.example.com",))
    assert after[2] == before[2] + 1


def test_site_rules_are_saved_with_the_one_supplied_section(server):
    before = saved(server)
    rule = {"id": "video", "name": "Video", "domains": ["video.example.com"]}
    status, _ = post(
        server,
        {
            "version": before[2],
            "mac": {"mode": "allowlist", "allowlist": ["02:00:00:00:00:02"]},
            "site_blocks": [rule],
        },
    )
    after = saved(server)
    assert status == 200
    assert after[0] == MacPolicy(mode="allowlist", allowlist=("02:00:00:00:00:02",))
    assert after[1] == before[1]
    assert [block.id for block in server.repository.list_site_blocks()] == ["video"]


def test_a_failed_site_rule_write_restores_the_supplied_section(server):
    before = saved(server)
    with patch.object(server.repository, "_write_site_blocks", side_effect=StorageError("failed")):
        with pytest.raises(StorageError):
            post(
                server,
                {
                    "mac": {"mode": "allowlist", "allowlist": ["02:00:00:00:00:02"]},
                    "site_blocks": [{"id": "video", "name": "Video", "domains": ["video.example.com"]}],
                },
            )
    assert saved(server) == before
    assert server.repository.list_site_blocks() == ()
