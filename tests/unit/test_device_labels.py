import unittest

from jittersim.config.device_labels import (
    DeviceLabelError,
    guess_device_type,
    normalize_device_display_name,
    normalize_device_mac,
    normalize_device_type,
)


class DeviceLabelTests(unittest.TestCase):
    def test_normalizes_mac_and_display_name(self) -> None:
        self.assertEqual(normalize_device_mac(" DC:1E:D5:3E:97:4C "), "dc:1e:d5:3e:97:4c")
        self.assertEqual(normalize_device_display_name("  Family laptop  "), "Family laptop")
        self.assertEqual(normalize_device_type("tv_console"), "tv_console")

    def test_rejects_invalid_mac_names_and_types(self) -> None:
        for mac_address in ("not-a-mac", "01:11:22:33:44:55"):
            with self.subTest(mac_address=mac_address):
                with self.assertRaises(DeviceLabelError):
                    normalize_device_mac(mac_address)
        for display_name in ("x" * 65, "bad\nname"):
            with self.subTest(display_name=display_name):
                with self.assertRaises(DeviceLabelError):
                    normalize_device_display_name(display_name)
        with self.assertRaises(DeviceLabelError):
            normalize_device_type("router")

    def test_suggests_only_from_clear_hostname_clues(self) -> None:
        suggestions = {
            "Samantha-iPhone": "phone",
            "Android-Tablet": "tablet",
            "Alice-MacBook-Pro": "laptop",
            "Office-Workstation": "desktop",
            "Living-Room-Roku": "tv_console",
            "Family-Device": "unknown",
            "Samsung-Galaxy": "unknown",
            None: "unknown",
        }

        for hostname, expected in suggestions.items():
            with self.subTest(hostname=hostname):
                self.assertEqual(guess_device_type(hostname), expected)


if __name__ == "__main__":
    unittest.main()
