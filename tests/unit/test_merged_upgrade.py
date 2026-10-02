"""Databases written by either line before the merge open and keep their settings.

The fixtures are dumps of the settings database each line wrote at its last
commit before the merge, with one of everything saved.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jittersim.config.storage import ConfigRepository

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def database_from(dump_name: str, directory: str) -> Path:
    path = Path(directory) / "settings.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript((FIXTURES / dump_name).read_text())
    finally:
        connection.close()
    return path


class MergedUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def open_twice(self, dump_name: str):
        """Open the database twice: the upgrade must be idempotent."""

        path = database_from(dump_name, self.directory.name)
        with ConfigRepository(path):
            pass
        return ConfigRepository(path)

    def test_a_main_database_keeps_everyone_else_and_scenarios(self) -> None:
        with self.open_twice("main-tip-settings.sql") as repository:
            self.assertEqual([profile.id for profile in repository.list_profiles()], ["slow"])
            self.assertEqual([device.id for device in repository.list_devices()], ["alice"])
            self.assertEqual([scenario.id for scenario in repository.list_scenarios()], ["c"])
            self.assertEqual(repository.get_everyone_else_profile_id(), "slow")
            (unaffected,) = repository.list_unaffected_devices()
            self.assertEqual(unaffected.mac_address, "02:00:00:00:00:02")
            # The old "name" column becomes the display name.
            self.assertEqual(unaffected.display_name, "Tablet")
            self.assertEqual(unaffected.name, "Tablet")
            # Tables the Pi line added exist and are empty.
            self.assertIsNone(repository.get_general_profile())
            self.assertEqual(repository.list_service_lists(), ())
            settings = repository.saved_settings()
            self.assertEqual(settings.everyone_else_profile_id, "slow")
            self.assertIsNone(settings.general_profile_id)

    def test_a_pi_line_database_keeps_the_general_profile_and_service_lists(self) -> None:
        with self.open_twice("pi-line-tip-settings.sql") as repository:
            self.assertEqual([profile.id for profile in repository.list_profiles()], ["slow"])
            self.assertEqual([device.id for device in repository.list_devices()], ["alice"])
            general = repository.get_general_profile()
            self.assertIsNotNone(general)
            self.assertEqual(general.id, "slow")
            self.assertEqual(
                [service_list.id for service_list in repository.list_service_lists()],
                ["streaming"],
            )
            (unaffected,) = repository.list_unaffected_devices()
            self.assertEqual((unaffected.mac_address, unaffected.display_name, unaffected.created_at),
                             ("02:00:00:00:00:02", "Tablet", 5))
            # Tables main added exist and are empty.
            self.assertIsNone(repository.get_everyone_else_profile_id())
            self.assertEqual(repository.list_scenarios(), ())
            self.assertEqual(repository.list_site_blocks(), ())
            settings = repository.saved_settings()
            self.assertEqual(settings.general_profile_id, "slow")
            self.assertEqual([item.id for item in settings.service_lists], ["streaming"])

    def test_saved_settings_from_either_line_survive_a_replace(self) -> None:
        for dump_name in ("main-tip-settings.sql", "pi-line-tip-settings.sql"):
            with self.subTest(dump=dump_name):
                directory = tempfile.TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                with ConfigRepository(database_from(dump_name, directory.name)) as repository:
                    before = repository.saved_settings()
                    repository.replace_settings(before)
                    self.assertEqual(repository.saved_settings(), before)


if __name__ == "__main__":
    unittest.main()
