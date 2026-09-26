"""Exercise retention against an existing database without touching active runs."""

import tempfile
import unittest
from pathlib import Path

from jittersim.config.models import Device, Profile, Progression, Scenario, ScenarioRun, ScenarioStep, Timer
from jittersim.config.storage import ConfigRepository


class RunHistoryTests(unittest.TestCase):
    def test_upgrade_and_later_writes_prune_in_bounded_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.db"
            with ConfigRepository(path) as repo:
                repo.save_profile(Profile("normal", "Normal"))
                for identifier in ("phone", "tablet"):
                    repo.save_device(Device(identifier, identifier, "192.168.50." + ("10" if identifier == "phone" else "11"), "normal"))
                repo.save_timer(Timer("active", "phone", "normal", 0, 100))
                repo.save_progression(Progression("active", "tablet", "normal", "normal", 0, 60))
                repo.save_scenario(Scenario("saved", "Saved", (ScenarioStep("normal", 10),)))
                repo.start_scenario_run(ScenarioRun("active", "saved", 0, everyone_else=True))
                # An old database can hold more history than one cleanup batch.
                for table in ("timers", "progressions", "scenario_runs"):
                    columns = [row["name"] for row in repo._connection.execute(f"PRAGMA table_info({table})")]
                    values = ["?" if name in ("id", "started_at") else "0" if name == "enabled" else name for name in columns]
                    # id precedes started_at in each table.
                    repo._connection.executemany(
                        f"INSERT INTO {table} ({', '.join(columns)}) SELECT {', '.join(values)} FROM {table} WHERE id = 'active'",
                        [(f"old-{number:04}", number) for number in range(1, 1606)],
                    )
                    repo._connection.execute(f"DROP INDEX {table}_history_idx")
                repo._connection.commit()
            with ConfigRepository(path) as repo:
                version = repo.get_runtime_state().configuration_version
                for table in ("timers", "progressions", "scenario_runs"):
                    self.assertEqual(repo._connection.execute(f"SELECT COUNT(*) FROM {table} WHERE enabled = 0").fetchone()[0], 605)
                    self.assertEqual(repo._connection.execute(f"SELECT id FROM {table} WHERE enabled = 1").fetchone()[0], "active")
                    indexes = {row["name"] for row in repo._connection.execute(f"PRAGMA index_list({table})")}
                    self.assertIn(f"{table}_history_idx", indexes)
                repo.save_scenario(Scenario("saved", "Renamed", (ScenarioStep("normal", 10),)), now=1)
                for table in ("timers", "progressions", "scenario_runs"):
                    rows = repo._connection.execute(f"SELECT started_at FROM {table} WHERE enabled = 0 ORDER BY started_at").fetchall()
                    self.assertEqual([row[0] for row in rows], list(range(1106, 1606)))
                    self.assertEqual(repo._connection.execute(f"SELECT id FROM {table} WHERE enabled = 1").fetchone()[0], "active")
                self.assertEqual(repo.get_scenario("saved").name, "Renamed")
                self.assertEqual(repo.get_runtime_state().configuration_version, version + 1)
