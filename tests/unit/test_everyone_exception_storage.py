"""Devices that run their own profile while Everyone runs are saved runtime state.

They are never settings: the export does not carry them. They end with every
whole-scope action, and the database ends them for any writer that does not know
about them, such as an older dashboard after a rollback.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository


def new_repository(path=":memory:") -> ConfigRepository:
    repository = ConfigRepository(path)
    repository.save_profile(Profile(id="low", name="Low", upload=Impairment(delay_ms=50)))
    repository.save_profile(Profile(id="slow", name="Slow", upload=Impairment(delay_ms=3_000)))
    repository.save_device(Device("tv", "TV", "192.168.50.20", "low"))
    repository.save_device(Device("air", "Air", "192.168.50.21", "slow"))
    repository.save_general_profile("low")
    return repository


def running(repository, action="apply", state="applied", **keep):
    repository.update_runtime_state(
        observed_state=state, last_action=action, last_result="success", last_error=None, **keep)


class EveryoneExceptionStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = new_repository()
        running(self.repository)

    def tearDown(self) -> None:
        self.repository.close()

    def rows(self):
        return {row[0] for row in self.repository._connection.execute(
            "SELECT device_id FROM everyone_exceptions")}

    def test_an_exception_counts_while_everyone_runs(self) -> None:
        self.assertEqual(self.repository.get_everyone_exceptions(), frozenset())

        self.repository.add_everyone_exception("air")

        self.assertEqual(self.repository.get_everyone_exceptions(), frozenset({"air"}))
        self.assertEqual(self.rows(), {"air"})

    def test_it_counts_only_while_everyone_is_saved_and_the_network_is_not_reset(self) -> None:
        self.repository.add_everyone_exception("air")

        self.repository._connection.execute("UPDATE general_profile_setting SET profile_id = NULL")
        self.assertEqual(self.repository.get_everyone_exceptions(), frozenset())
        self.repository._connection.execute("UPDATE general_profile_setting SET profile_id = 'low'")
        self.assertEqual(self.repository.get_everyone_exceptions(), frozenset({"air"}))
        for action in ("reset", None):
            self.repository._connection.execute(
                "UPDATE runtime_state SET last_action = ?, exceptions_kept = 1", (action,))
            self.assertEqual(self.repository.get_everyone_exceptions(), frozenset(), action)
        self.repository._connection.execute(
            "UPDATE runtime_state SET last_action = 'schedule', exceptions_kept = 1")
        self.assertEqual(self.repository.get_everyone_exceptions(), frozenset({"air"}))

    def test_every_runtime_write_ends_exceptions_unless_it_keeps_them(self) -> None:
        self.repository.add_everyone_exception("air")

        running(self.repository, keep_everyone_exceptions=True)
        self.assertEqual(self.rows(), {"air"})
        running(self.repository, state="applying", keep_everyone_exceptions=True)
        self.assertEqual(self.rows(), {"air"})
        running(self.repository)
        self.assertEqual(self.rows(), set())

    def test_a_reset_write_cannot_keep_exceptions(self) -> None:
        self.repository.add_everyone_exception("air")

        with self.assertRaises(ValueError):
            running(self.repository, action="reset", state="reset", keep_everyone_exceptions=True)
        self.assertEqual(self.rows(), {"air"})
        running(self.repository, action="reset", state="reset")
        self.assertEqual(self.rows(), set())

    def test_the_keep_flag_never_outlives_the_write(self) -> None:
        self.repository.add_everyone_exception("air")
        running(self.repository, keep_everyone_exceptions=True)

        kept = self.repository._connection.execute(
            "SELECT exceptions_kept FROM runtime_state").fetchone()[0]
        self.assertEqual(kept, 0)
        # A writer that does not know about the flag still ends the exceptions.
        self.repository._connection.execute(
            "UPDATE runtime_state SET observed_state = 'applying', last_action = 'apply'")
        self.assertEqual(self.rows(), set())

    def test_saving_a_setting_keeps_them_and_so_does_a_scheduled_result(self) -> None:
        self.repository.add_everyone_exception("air")

        self.repository.save_profile(Profile(id="extra", name="Extra"))
        self.assertEqual(self.repository.get_runtime_state().observed_state, "unknown")
        self.assertEqual(self.rows(), {"air"})
        self.assertTrue(self.repository.record_schedule_result(success=True))
        self.assertTrue(self.repository.record_schedule_result(success=False))
        self.assertEqual(self.rows(), {"air"})
        self.assertEqual(self.repository.get_runtime_state().last_action, "schedule")

    def test_a_scheduled_result_after_a_reset_changes_nothing_and_keeps_nothing(self) -> None:
        running(self.repository, action="reset", state="reset")

        self.assertFalse(self.repository.record_schedule_result(success=True))

    def test_changing_everyone_ends_the_exceptions(self) -> None:
        self.repository.add_everyone_exception("air")

        self.repository.save_general_profile("slow")

        self.assertEqual(self.rows(), set())
        self.repository.add_everyone_exception("air")
        self.repository.save_general_profile(None)
        self.assertEqual(self.rows(), set())

    def test_deleting_the_device_removes_its_exception_and_nothing_else(self) -> None:
        self.repository.add_everyone_exception("air")
        self.repository.add_everyone_exception("tv")

        self.assertTrue(self.repository.delete_device("air"))

        self.assertEqual(self.repository.get_everyone_exceptions(), frozenset({"tv"}))
        self.assertEqual(self.repository.get_general_profile().id, "low")

    def test_a_refused_start_puts_the_earlier_exceptions_back(self) -> None:
        self.repository.add_everyone_exception("tv")
        before = self.repository.get_everyone_exceptions()
        self.repository.add_everyone_exception("air")

        self.repository.restore_everyone_exceptions(before)

        self.assertEqual(self.rows(), {"tv"})
        self.repository.restore_everyone_exceptions(frozenset({"gone", "tv"}))
        self.assertEqual(self.rows(), {"tv"}, "a device that no longer exists is not restored")

    def test_the_same_device_is_never_listed_twice(self) -> None:
        self.repository.add_everyone_exception("air")
        self.repository.add_everyone_exception("air")
        self.assertEqual(self.rows(), {"air"})

    def test_an_unknown_device_cannot_be_an_exception(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.add_everyone_exception("nobody")

    def test_a_settings_import_ends_them(self) -> None:
        self.repository.add_everyone_exception("air")
        self.repository.replace_settings(self.repository.saved_settings())
        self.assertEqual(self.rows(), set())
        self.assertEqual(self.repository.get_everyone_exceptions(), frozenset())


class EveryoneExceptionMigrationTests(unittest.TestCase):
    def test_an_older_database_gains_the_table_column_and_trigger_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "jittersim.db"
            repository = new_repository(path)
            running(repository)
            repository.close()
            connection = sqlite3.connect(path)
            connection.executescript("""
                DROP TRIGGER end_everyone_exceptions;
                DROP TABLE everyone_exceptions;
                ALTER TABLE runtime_state DROP COLUMN exceptions_kept;
            """)
            connection.commit()
            connection.close()

            upgraded = ConfigRepository(path)
            names = {row[0] for row in upgraded._connection.execute(
                "SELECT name FROM sqlite_master")}
            self.assertIn("everyone_exceptions", names)
            self.assertIn("end_everyone_exceptions", names)
            self.assertEqual(upgraded.get_everyone_exceptions(), frozenset())
            # The saved state is untouched.
            self.assertEqual(upgraded.get_runtime_state().last_action, "apply")
            self.assertEqual(upgraded.get_general_profile().id, "low")
            upgraded.add_everyone_exception("air")
            upgraded.close()
            # Opening it again changes nothing, and the exception survives the restart.
            again = ConfigRepository(path)
            self.assertEqual(again.get_everyone_exceptions(), frozenset({"air"}))
            again.close()


if __name__ == "__main__":
    unittest.main()
