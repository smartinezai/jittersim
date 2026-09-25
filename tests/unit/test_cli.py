import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jittersim.cli import main


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


if __name__ == "__main__":
    unittest.main()
