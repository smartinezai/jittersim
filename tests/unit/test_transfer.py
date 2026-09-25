import json
import unittest

from jittersim.config.models import (
    DataLimit,
    Device,
    DeviceGroup,
    Impairment,
    Profile,
    SavedSettings,
    Schedule,
    ServiceAssignment,
    ServiceQuota,
    UnaffectedDevice,
)
from jittersim.config.transfer import check_settings, settings_to_payload


def lab_settings(**changes) -> SavedSettings:
    values = {
        "profiles": (
            Profile(id="normal", name="Normal"),
            Profile(id="slow", name="Slow", upload=Impairment(delay_ms=300)),
        ),
        "devices": (
            Device("alice", "Alice", "192.168.50.10", "normal"),
            Device("bob", "Bob", "192.168.50.11", "slow"),
        ),
        "groups": (DeviceGroup(id="family", name="Family", device_ids=("alice", "bob")),),
        "schedules": (
            Schedule(
                id="work",
                device_id="alice",
                profile_id="slow",
                days_of_week=(0, 1),
                start_minute=540,
                end_minute=1_020,
            ),
        ),
        "data_limits": (
            DataLimit(
                device_id="alice",
                profile_id="slow",
                limit_bytes=1_000,
                used_bytes=700,
                counter_bytes=50,
                last_reset_at=123,
            ),
        ),
    }
    values.update(changes)
    return SavedSettings(**values)


def assignment(assignment_id: str, **changes) -> ServiceAssignment:
    values = {
        "id": assignment_id,
        "device_id": "alice",
        "name": assignment_id.title(),
        "domains": (f"{assignment_id}.example",),
        "profile_id": "slow",
    }
    values.update(changes)
    return ServiceAssignment(**values)


class SettingsTransferTests(unittest.TestCase):
    def test_export_holds_settings_but_not_usage(self) -> None:
        payload = settings_to_payload(lab_settings(), exported_at=1_000)

        self.assertEqual(payload["format"], "jittersim-settings")
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["exported_at"], 1_000)
        self.assertEqual([profile["id"] for profile in payload["profiles"]], ["normal", "slow"])
        self.assertEqual(payload["groups"][0]["device_ids"], ["alice", "bob"])
        self.assertEqual(
            payload["data_limits"][0],
            {
                "device_id": "alice",
                "profile_id": "slow",
                "limit_bytes": 1_000,
                "reset_mode": "manual",
                "reset_interval_seconds": 0,
                "reset_times": [],
                "enabled": True,
            },
        )
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_export_holds_browser_service_profiles(self) -> None:
        video = ServiceAssignment(
            id="video",
            device_id="alice",
            name="Video",
            domains=("youtube.com", "googlevideo.com"),
            profile_id="slow",
        )

        payload = settings_to_payload(lab_settings(service_assignments=(video,)), exported_at=1)

        self.assertEqual(
            payload["service_assignments"],
            [
                {
                    "id": "video",
                    "device_id": "alice",
                    "name": "Video",
                    "domains": ["youtube.com", "googlevideo.com"],
                    "profile_id": "slow",
                    "enabled": True,
                }
            ],
        )

    def test_accepts_consistent_settings(self) -> None:
        check_settings(lab_settings(), max_traffic_classes=15, max_service_domains=200)

    def test_rejects_settings_that_do_not_fit_together(self) -> None:
        normal = Profile(id="normal", name="Normal")
        cases = {
            "device bob uses the missing profile ghost": lab_settings(
                devices=(Device("bob", "Bob", "192.168.50.11", "ghost"),),
                groups=(),
                schedules=(),
                data_limits=(),
            ),
            "profile normal appears twice": lab_settings(
                profiles=(normal, normal, Profile(id="slow", name="Slow"))
            ),
            "device alice is in more than one group": lab_settings(
                groups=(
                    DeviceGroup(id="one", name="One", device_ids=("alice",)),
                    DeviceGroup(id="two", name="Two", device_ids=("alice",)),
                )
            ),
            "schedule work uses the missing device carol": lab_settings(
                schedules=(
                    Schedule(
                        id="work",
                        device_id="carol",
                        profile_id="slow",
                        days_of_week=(0,),
                        start_minute=0,
                        end_minute=60,
                    ),
                )
            ),
            "service limit video uses the missing profile ghost": lab_settings(
                service_quotas=(
                    ServiceQuota(
                        id="video",
                        device_id="alice",
                        name="Video",
                        domains=("netflix.com",),
                        profile_id="ghost",
                        limit_bytes=1,
                    ),
                )
            ),
            "browser service profile video uses the missing device carol": lab_settings(
                service_assignments=(assignment("video", device_id="carol"),)
            ),
            "browser service profile video uses the missing profile ghost": lab_settings(
                service_assignments=(assignment("video", profile_id="ghost"),)
            ),
            "browser service profile video appears twice": lab_settings(
                service_assignments=(assignment("video"), assignment("video"))
            ),
            "browser service profiles overlap on device alice": lab_settings(
                service_assignments=(
                    assignment("video", domains=("youtube.com",)),
                    assignment("music", domains=("music.youtube.com",)),
                )
            ),
            "at most 3 domains": lab_settings(
                service_assignments=(
                    assignment("video", domains=("youtube.com", "googlevideo.com")),
                    assignment("music", domains=("spotify.com", "scdn.co")),
                )
            ),
            "at most 2 devices and service rules": lab_settings(
                service_assignments=(assignment("video", enabled=True),)
            ),
            "at most 2 devices and service rules ": lab_settings(
                service_quotas=(
                    ServiceQuota(
                        id="video",
                        device_id="alice",
                        name="Video",
                        domains=("netflix.com",),
                        profile_id="slow",
                        limit_bytes=1,
                    ),
                )
            ),
        }
        for message, settings in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message.strip()):
                    check_settings(settings, max_traffic_classes=2, max_service_domains=3)

    def test_disabled_browser_service_profiles_use_no_capacity(self) -> None:
        check_settings(
            lab_settings(
                service_assignments=(
                    assignment("video", domains=("youtube.com",), enabled=False),
                    assignment("music", domains=("music.youtube.com",), enabled=False),
                )
            ),
            max_traffic_classes=2,
            max_service_domains=1,
        )


    def test_export_carries_the_everyone_else_settings(self) -> None:
        payload = settings_to_payload(
            SavedSettings(
                profiles=(Profile(id="slow", name="Slow"),),
                everyone_else_profile_id="slow",
                unaffected_devices=(UnaffectedDevice("aa:aa:aa:aa:aa:aa", "Laptop"),),
            ),
            exported_at=0,
        )

        self.assertEqual(
            payload["everyone_else"],
            {
                "profile_id": "slow",
                "unaffected_devices": [{"mac_address": "aa:aa:aa:aa:aa:aa", "name": "Laptop"}],
            },
        )

    def test_check_refuses_everyone_else_settings_that_do_not_fit(self) -> None:
        slow = Profile(id="slow", name="Slow")
        mac = "aa:aa:aa:aa:aa:aa"
        cases = {
            "everyone else uses the missing profile gone": SavedSettings(
                everyone_else_profile_id="gone"
            ),
            "unaffected device aa:aa:aa:aa:aa:aa appears twice": SavedSettings(
                unaffected_devices=(UnaffectedDevice(mac), UnaffectedDevice(mac))
            ),
            "aa:aa:aa:aa:aa:aa is unaffected and belongs to device alice": SavedSettings(
                profiles=(slow,),
                devices=(Device("alice", "Alice", "192.168.50.10", "slow", mac),),
                unaffected_devices=(UnaffectedDevice(mac),),
            ),
            "the unaffected list holds at most 256 devices": SavedSettings(
                unaffected_devices=tuple(
                    UnaffectedDevice(f"02:00:00:00:{index // 256:02x}:{index % 256:02x}")
                    for index in range(257)
                )
            ),
            "JitterSim can shape at most 15": SavedSettings(
                profiles=(slow,),
                devices=tuple(
                    Device(f"d{index}", f"D{index}", f"192.168.50.{10 + index}", "slow")
                    for index in range(15)
                ),
                everyone_else_profile_id="slow",
            ),
        }
        for message, settings in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    check_settings(settings, max_traffic_classes=15, max_service_domains=200)


if __name__ == "__main__":
    unittest.main()
