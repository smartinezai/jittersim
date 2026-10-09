import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jittersim.cli import main
from jittersim.config.storage import ConfigRepository


class CliTests(unittest.TestCase):
    def test_default_command_is_simulation_only(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            result = main(["--uplink", "eth0", "--test-interface", "wlan0"])

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("No commands are being executed", rendered)
        self.assertIn("nft", rendered)
        self.assertIn("3000ms", rendered)
        self.assertIn("wlan0", rendered)

    def test_compares_measurements(self) -> None:
        run = {
            "format": "jittersim-measurement",
            "version": 1,
            "label": "normal",
            "target": "1.1.1.1",
            "summary": {"sent": 10, "loss_percent": 0.0, "rtt_avg_ms": 12.5,
                        "rtt_p95_ms": 14.0, "jitter_ms": 0.5, "reordered": 0},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "normal.json"
            path.write_text(json.dumps(run))
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["compare", str(path)])

        self.assertEqual(result, 0)
        self.assertIn("| normal | 1.1.1.1 | 10 | 0.0% | 12.50 ms |", output.getvalue())

    def test_cli_values_are_validated_before_rendering(self) -> None:
        with self.assertRaises(ValueError):
            main(["--loss-percent", "101"])

    def test_dns_failure_command_saves_a_chance_without_applying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "settings.db"
            ConfigRepository(database).close()
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "access", "dns-failure", "set", "25",
                        "--database", str(database),
                    ]
                )

            self.assertEqual(result, 0)
            self.assertIn("Saved DNS failure chance: 25%", output.getvalue())
            self.assertIn("Apply the saved setting", output.getvalue())
            with ConfigRepository(database) as repository:
                self.assertEqual(
                    repository.access_settings().access_options.dns_failure_percent, 25
                )

    def test_persistence_command_saves_the_mode_and_updates_the_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "settings.db"
            ConfigRepository(database).close()
            with patch("jittersim.cli.HelperClient") as helper_client:
                helper_client.return_value.set_gateway_persistence.return_value = {
                    "gateway_state": "available",
                    "outage_stop_latched": False,
                }
                output = io.StringIO()
                with redirect_stdout(output):
                    result = main(
                        [
                            "persistence", "set", "on",
                            "--database", str(database),
                        ]
                    )

            self.assertEqual(result, 0)
            helper_client.return_value.set_gateway_persistence.assert_called_once_with(
                enabled=True
            )
            self.assertIn("Saved persistence mode: on", output.getvalue())
            with ConfigRepository(database) as repository:
                self.assertTrue(repository.get_persist_profiles_after_gateway_outage())

    def test_update_check_explains_it_reads_releases_staged_on_the_pi(self) -> None:
        with patch("jittersim.cli.HelperClient") as helper_client:
            helper_client.return_value.release_status.return_value = {
                "installed": "v1.0",
                "available": None,
                "message": "No newer approved release is staged.",
            }
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["update", "check"])

        self.assertEqual(result, 0)
        self.assertIn("Installed: v1.0", output.getvalue())
        self.assertIn("Add --online to ask GitHub", output.getvalue())
        self.assertIn("sudo jittersim update", output.getvalue())


if __name__ == "__main__":
    unittest.main()
