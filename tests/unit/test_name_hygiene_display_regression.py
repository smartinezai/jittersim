"""Device display filtering must leave saved and imported labels intact."""

import pytest

from jittersim.api.server import _new_client_name, _profile_from_payload, _settings_from_payload
from jittersim.config.device_names import (
    UNKNOWN_DEVICE_NAME,
    has_hidden_characters,
    resolve_device_name,
    saved_device_title,
)
from jittersim.config.models import Device, SavedSettings
from jittersim.config.storage import ConfigRepository
from jittersim.config.transfer import settings_to_payload


MISSING_CONTROLS = tuple(chr(code) for code in range(0x80, 0xA0)) + ("\u061c",)


@pytest.mark.parametrize("control", MISSING_CONTROLS)
def test_hidden_control_cannot_win_any_device_name_source(control):
    hostile = "Kitchen" + control + "tablet"
    assert has_hidden_characters(hostile)
    assert resolve_device_name(saved_label=hostile, saved_name="Saved") == "Saved"
    assert resolve_device_name(saved_name=hostile, hostname="Hostname") == "Hostname"
    assert resolve_device_name(hostname=hostile) == UNKNOWN_DEVICE_NAME
    assert saved_device_title(
        saved_label=hostile, saved_name=hostile, ipv4_address="192.168.50.10"
    ) == "Unknown device (192.168.50.10)"
    assert _new_client_name(hostile, "Hostname", "192.168.50.10") == "Hostname"
    assert _new_client_name(None, hostile, "192.168.50.10") == "Client 192.168.50.10"


@pytest.mark.parametrize(
    "name", ["Café 📱", "Zoë 👨‍👩‍👧", "مكتب", "می\u200cروم", "日本語の名前"]
)
def test_ordinary_unicode_names_keep_their_existing_display(name):
    assert not has_hidden_characters(name)
    assert resolve_device_name(saved_label=name, saved_name="Other") == name
    assert _new_client_name(None, name, "192.168.50.10") == name


@pytest.mark.parametrize("control", ("\x00", "\n", "\x9b", "\u061c", "\u202e"))
@pytest.mark.parametrize("version", (1, 2, 3))
def test_api_storage_and_legacy_import_preserve_hidden_name_values(control, version):
    name = "Kitchen" + control + "tablet"
    # Use the API's ordinary payload constructor without starting an HTTP server.
    profile = _profile_from_payload({"id": "slow", "name": name})
    device = Device(id="tablet", name=name, ipv4_address="192.168.50.10", profile_id="slow")
    repository = ConfigRepository(":memory:")
    try:
        repository.save_profile(profile)
        repository.save_device(device)
        assert repository.get_profile("slow").name == name
        assert repository.get_device("tablet").name == name
        exported = settings_to_payload(
            SavedSettings(profiles=(profile,), devices=(device,)), exported_at=0
        )
        exported["version"] = version
        imported = _settings_from_payload(exported)
        assert imported.profiles[0].name == name
        assert imported.devices[0].name == name
    finally:
        repository.close()
