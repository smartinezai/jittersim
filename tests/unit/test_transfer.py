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


if __name__ == "__main__":
    unittest.main()
