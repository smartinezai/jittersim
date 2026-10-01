from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Profile
from jittersim.config.protection import protected_addresses, protected_macs
from jittersim.config.storage import ConfigRepository

PROTECTED = "02:00:00:00:00:01"
OTHER = "02:00:00:00:00:02"


def make_repository(tmp_path):
    repository = ConfigRepository(tmp_path / "config.sqlite3")
    repository.save_profile(Profile(id="slow", name="Slow"))
    return repository


def test_protected_macs_are_sorted(tmp_path):
    repository = make_repository(tmp_path)
    repository.save_unaffected_device(UnaffectedDevice(OTHER, "b", 1))
    repository.save_unaffected_device(UnaffectedDevice(PROTECTED, "a", 1))
    assert protected_macs(repository) == (PROTECTED, OTHER)


def test_addresses_come_from_stations_and_saved_devices(tmp_path):
    repository = make_repository(tmp_path)
    repository.save_unaffected_device(UnaffectedDevice(PROTECTED, "a", 1))
    repository.save_device(
        Device(
            id="saved",
            name="Saved",
            ipv4_address="192.168.50.20",
            profile_id="slow",
            mac_address=PROTECTED,
        )
    )
    repository.save_device(
        Device(
            id="other",
            name="Other",
            ipv4_address="192.168.50.21",
            profile_id="slow",
            mac_address=OTHER,
        )
    )
    stations = [
        {"mac_address": PROTECTED, "ipv4_address": "192.168.50.30"},
        {"mac_address": OTHER, "ipv4_address": "192.168.50.31"},
        {"mac_address": PROTECTED, "ipv4_address": None},
        {"ipv4_address": "192.168.50.32"},
    ]
    assert protected_addresses(repository, stations) == {"192.168.50.20", "192.168.50.30"}


def test_nothing_is_protected_by_default(tmp_path):
    repository = make_repository(tmp_path)
    assert protected_macs(repository) == ()
    stations = [{"mac_address": OTHER, "ipv4_address": "192.168.50.31"}]
    assert protected_addresses(repository, stations) == set()
