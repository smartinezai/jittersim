import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jittersim.config.models import Device, Impairment, Profile, SavedSettings
from jittersim.config.pending_devices import (
    MAX_PENDING_TIME,
    PendingDeviceChoice,
)
from jittersim.config.storage import (
    ConfigRepository,
    PendingDeviceConflictError,
    StorageError,
)


def choice(index=1, **kwargs):
    values = dict(mac_address=f"02:00:00:00:{index // 256:02x}:{index % 256:02x}", action="protect")
    values.update(kwargs)
    return PendingDeviceChoice(**values)


def active_rows(repository):
    """Compare every pre-existing table, including grants and runtime state."""
    connection = repository._connection
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name != 'pending_device_choices' ORDER BY name"
        )
    ]
    return {
        name: tuple(
            tuple(row)
            for row in connection.execute(f'SELECT * FROM "{name}"')
            if name != "sqlite_sequence" or row[0] != "pending_device_choices"
        )
        for name in names
    }


class PendingDeviceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "settings.db"
        self.repository = ConfigRepository(self.path)
        self.profile = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=100))
        self.repository.save_profile(self.profile)
        self.repository.save_device(
            Device(id="existing", name="Existing", ipv4_address="192.168.50.10", profile_id="slow")
        )

    def tearDown(self):
        self.repository.close()
        self.directory.cleanup()

    def test_round_trip_normalization_and_inert_save_edit_delete(self):
        before = active_rows(self.repository)
        with patch("jittersim.config.storage.time.time", return_value=1234):
            first = self.repository.save_pending_device_choice(
                choice(
                    mac_address=" AA-BB-CC-DD-EE-FF ",
                    display_name="  Tablet  ",
                    action="profile",
                    profile_id="slow",
                    source="imported-neighbour-cache",
                    observed_at=MAX_PENDING_TIME,
                    saved_at=1,
                )
            )
        self.assertEqual(first.mac_address, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(first.display_name, "Tablet")
        self.assertEqual(first.saved_at, 1234)
        self.assertEqual(self.repository.get_pending_device_choice("AA-BB-CC-DD-EE-FF"), first)
        second = self.repository.save_pending_device_choice(
            replace(first, action="protect", profile_id=None), expected_revision=first.revision
        )
        self.assertEqual(second.revision, first.revision + 1)
        self.repository.delete_pending_device_choice(
            second.mac_address, expected_revision=second.revision
        )
        self.assertEqual(self.repository.list_pending_device_choices(), ())
        self.assertEqual(active_rows(self.repository), before)

    def test_invalid_fields_rejected_without_changes(self):
        before = active_rows(self.repository)
        invalid = [
            dict(mac_address="00:00:00:00:00:00"),
            dict(mac_address="ff:ff:ff:ff:ff:ff"),
            dict(mac_address=42),
            dict(action="apply"),
            dict(action=[]),
            dict(action="profile"),
            dict(profile_id="slow"),
            dict(source="router"),
            dict(source=[]),
            dict(display_name="x" * 65),
            dict(display_name="a\nb"),
            dict(display_name=None),
            dict(observed_at=True),
            dict(observed_at=-1),
            dict(observed_at=1.2),
            dict(observed_at=MAX_PENDING_TIME + 1),
            dict(saved_at=True),
            dict(saved_at=-1),
            dict(saved_at=MAX_PENDING_TIME + 1),
            dict(revision=True),
            dict(revision=0),
            dict(revision=2**63),
            dict(action="profile", profile_id="x" * 65),
            dict(association_verified=True),
            dict(ipv4_address="192.168.50.20"),
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises((ValueError, TypeError)):
                choice(**fields)
        with self.assertRaises(StorageError):
            self.repository.save_pending_device_choice(
                choice(action="profile", profile_id="missing")
            )
        self.assertEqual(active_rows(self.repository), before)
        self.assertEqual(self.repository.list_pending_device_choices(), ())

    def test_revision_conflicts_do_not_overwrite_or_resurrect(self):
        first = self.repository.save_pending_device_choice(choice())
        with self.assertRaises(PendingDeviceConflictError):
            self.repository.save_pending_device_choice(choice())
        current = self.repository.save_pending_device_choice(
            replace(first, display_name="new"), expected_revision=first.revision
        )
        for operation in (
            lambda: self.repository.save_pending_device_choice(
                first, expected_revision=first.revision
            ),
            lambda: self.repository.delete_pending_device_choice(
                first.mac_address, expected_revision=first.revision
            ),
        ):
            with self.assertRaises(PendingDeviceConflictError):
                operation()
        self.repository.delete_pending_device_choice(
            current.mac_address, expected_revision=current.revision
        )
        with self.assertRaises(PendingDeviceConflictError):
            self.repository.save_pending_device_choice(first, expected_revision=current.revision)
        with self.assertRaises(PendingDeviceConflictError):
            self.repository.delete_pending_device_choice(
                first.mac_address, expected_revision=current.revision
            )
        for revision in (True, 0, -1, 1.5, 2**63):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.repository.save_pending_device_choice(first, expected_revision=revision)
            with self.assertRaises(ValueError):
                self.repository.delete_pending_device_choice(
                    first.mac_address, expected_revision=revision
                )

    def test_deleted_revision_cannot_target_recreated_choice_after_reopen(self):
        first = self.repository.save_pending_device_choice(choice(revision=9999))
        self.assertEqual(first.revision, 1)
        self.repository.delete_pending_device_choice(
            first.mac_address, expected_revision=first.revision
        )
        self.repository.close()
        self.repository = ConfigRepository(self.path)
        replacement = self.repository.save_pending_device_choice(
            replace(first, display_name="Replacement")
        )
        self.assertGreater(replacement.revision, first.revision)
        with self.assertRaises(PendingDeviceConflictError):
            self.repository.save_pending_device_choice(first, expected_revision=first.revision)
        with self.assertRaises(PendingDeviceConflictError):
            self.repository.delete_pending_device_choice(
                first.mac_address, expected_revision=first.revision
            )
        self.assertEqual(self.repository.get_pending_device_choice(first.mac_address), replacement)

    def race(self, operation):
        barrier = threading.Barrier(2)

        def run(index):
            with ConfigRepository(self.path) as repository:
                barrier.wait(timeout=5)
                try:
                    return operation(repository, index)
                except StorageError as error:
                    return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, (1, 2)))

    def test_transactional_capacity_across_connections(self):
        before = active_rows(self.repository)
        for index in range(255):
            self.repository.save_pending_device_choice(choice(index))
        results = self.race(
            lambda repository, index: repository.save_pending_device_choice(choice(255 + index))
        )
        self.assertEqual(sum(isinstance(result, StorageError) for result in results), 1)
        self.assertEqual(len(self.repository.list_pending_device_choices()), 256)
        with self.assertRaises(StorageError):
            self.repository.save_pending_device_choice(choice(999))
        current = self.repository.get_pending_device_choice(choice(0).mac_address)
        self.repository.save_pending_device_choice(
            replace(current, display_name="updated"), expected_revision=current.revision
        )
        self.assertEqual(active_rows(self.repository), before)

    def test_revision_compare_and_swap_across_connections(self):
        first = self.repository.save_pending_device_choice(choice())
        results = self.race(
            lambda repository, index: repository.save_pending_device_choice(
                replace(first, display_name=str(index)), expected_revision=first.revision
            )
        )
        self.assertEqual(
            sum(isinstance(result, PendingDeviceConflictError) for result in results), 1
        )
        self.assertEqual(self.repository.get_pending_device_choice(first.mac_address).revision, 2)

    def test_profile_references_and_delete_failure_are_atomic(self):
        saved = self.repository.save_pending_device_choice(
            choice(action="profile", profile_id="slow", display_name="Prepared tablet")
        )
        self.assertIn(
            "pending device Prepared tablet", self.repository.profile_references("slow", now=0)
        )
        self.repository.delete_device("existing")
        before = active_rows(self.repository)
        with self.assertRaises(StorageError):
            self.repository.delete_profile("slow")
        self.assertEqual(active_rows(self.repository), before)
        self.assertEqual(self.repository.list_pending_device_choices(), (saved,))
        self.assertFalse(self.repository.in_transaction)

    def test_import_keeps_profile_or_rolls_back_everything(self):
        saved = self.repository.save_pending_device_choice(
            choice(action="profile", profile_id="slow")
        )
        self.repository.replace_settings(
            SavedSettings(profiles=(replace(self.profile, name="Changed"),))
        )
        self.assertEqual(self.repository.get_profile("slow").name, "Changed")
        before = active_rows(self.repository)
        with self.assertRaises(StorageError) as refused:
            self.repository.replace_settings(SavedSettings())
        self.assertIn(
            f"Remove pending device choices for {saved.mac_address}", str(refused.exception)
        )
        self.assertIn("before importing settings that omit their profiles", str(refused.exception))
        self.assertEqual(active_rows(self.repository), before)
        self.assertEqual(self.repository.list_pending_device_choices(), (saved,))
        self.assertFalse(self.repository.in_transaction)
        self.assertEqual(
            self.repository._connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )

    def test_import_pending_reference_message_is_bounded_and_version_check_stays_first(self):
        for index in range(8):
            self.repository.save_pending_device_choice(
                choice(index, action="profile", profile_id="slow", display_name="<b>" + "x" * 61)
            )
        pending = self.repository.list_pending_device_choices()
        before = active_rows(self.repository)
        with self.assertRaisesRegex(StorageError, "^settings changed since the preview$"):
            self.repository.replace_settings(SavedSettings(), expected_version=-1)
        with self.assertRaises(StorageError) as refused:
            self.repository.replace_settings(SavedSettings())
        message = str(refused.exception)
        for saved in pending[:3]:
            self.assertIn(saved.mac_address, message)
        for saved in pending[3:]:
            self.assertNotIn(saved.mac_address, message)
        self.assertIn("(and 5 more)", message)
        self.assertNotIn("<b>", message)
        self.assertLess(len(message), 200)
        self.assertEqual(active_rows(self.repository), before)
        self.assertEqual(self.repository.list_pending_device_choices(), pending)
        self.assertFalse(self.repository.in_transaction)
        self.assertEqual(
            self.repository._connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )

    def test_injected_post_mutation_failure_rolls_back_pending_existing_and_version(self):
        before = active_rows(self.repository)
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with self.repository.transaction():
                self.repository.save_pending_device_choice(choice())
                self.repository.save_profile(replace(self.profile, name="Changed"))
                self.repository.delete_device("existing")
                raise RuntimeError("injected after writes")
        self.assertEqual(active_rows(self.repository), before)
        self.assertEqual(self.repository.list_pending_device_choices(), ())
        self.assertFalse(self.repository.in_transaction)

    def test_pending_edit_and_delete_roll_back_with_outer_failure(self):
        first = self.repository.save_pending_device_choice(choice())
        second = self.repository.save_pending_device_choice(choice(2))
        before = active_rows(self.repository)
        sequence = self.repository._connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'pending_device_choices'"
        ).fetchone()[0]
        with self.assertRaises(RuntimeError):
            with self.repository.transaction():
                self.repository.save_pending_device_choice(
                    replace(first, display_name="Changed"), expected_revision=first.revision
                )
                self.repository.delete_pending_device_choice(
                    second.mac_address, expected_revision=second.revision
                )
                raise RuntimeError("injected after pending edit and delete")
        self.assertEqual(self.repository.list_pending_device_choices(), (first, second))
        self.assertEqual(active_rows(self.repository), before)
        self.assertEqual(
            self.repository._connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'pending_device_choices'"
            ).fetchone()[0],
            sequence,
        )

    def test_save_commit_failure_rolls_back_mutation(self):
        before = active_rows(self.repository)
        # A deferred missing reference makes commit fail after the pending insert.
        self.repository._connection.executescript(
            "CREATE TABLE fail_at_commit (id TEXT REFERENCES profiles(id) "
            "DEFERRABLE INITIALLY DEFERRED); "
            "CREATE TRIGGER fail_pending AFTER INSERT ON pending_device_choices "
            "BEGIN INSERT INTO fail_at_commit VALUES ('missing'); END;"
        )
        with self.assertRaises(StorageError):
            self.repository.save_pending_device_choice(choice())
        self.assertEqual(self.repository.list_pending_device_choices(), ())
        self.assertEqual(
            self.repository._connection.execute("SELECT * FROM fail_at_commit").fetchall(), []
        )
        after = active_rows(self.repository)
        after.pop("fail_at_commit")
        self.assertEqual(after, before)
        self.assertFalse(self.repository.in_transaction)

    def test_reopen_twice_and_sqlite_backup_retain_choices(self):
        saved = self.repository.save_pending_device_choice(
            choice(action="profile", profile_id="slow")
        )
        before = active_rows(self.repository)
        for _ in range(2):
            self.repository.close()
            self.repository = ConfigRepository(self.path)
            self.assertEqual(self.repository.list_pending_device_choices(), (saved,))
            self.assertEqual(active_rows(self.repository), before)
        backup = Path(self.directory.name) / "backup.db"
        backup.write_bytes(self.repository.backup_bytes())
        with ConfigRepository(backup) as restored:
            self.assertEqual(restored.list_pending_device_choices(), (saved,))
            self.assertEqual(active_rows(restored), before)
            self.assertEqual(restored._connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        columns = [
            row[1]
            for row in self.repository._connection.execute(
                "PRAGMA table_info(pending_device_choices)"
            )
        ]
        self.assertNotIn("ipv4_address", columns)
        self.assertNotIn("association_verified", columns)
        self.assertNotIn("pending", repr(self.repository.saved_settings()))
