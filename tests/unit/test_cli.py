import io
import unittest
from contextlib import redirect_stdout

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

    def test_cli_values_are_validated_before_rendering(self) -> None:
        with self.assertRaises(ValueError):
            main(["--loss-percent", "101"])


if __name__ == "__main__":
    unittest.main()
