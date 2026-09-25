import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim.config.storage import ConfigRepository
from jittersim.networking.reset import main


class ResetCommandTests(unittest.TestCase):
    def test_ssh_reset_records_state_only_after_helper_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jittersim.db"
            repository = ConfigRepository(database)
            repository.update_runtime_state(
                observed_state="applied",
                last_action="apply",
                last_result="success",
                last_error=None,
            )
            repository.close()
            with patch("jittersim.networking.reset.subprocess.run") as check, patch(
                "jittersim.networking.reset.HelperClient"
            ) as helper:
                check.return_value = subprocess.CompletedProcess([], 3)
                self.assertEqual(main(["--database", str(database)]), 0)
                helper.return_value.reset.assert_called_once_with()
                check.return_value = subprocess.CompletedProcess([], 0)
                self.assertEqual(main(["--database", str(database)]), 1)
                helper.return_value.reset.assert_called_once_with()
            reopened = ConfigRepository(database)
            try:
                self.assertEqual(reopened.get_runtime_state().last_action, "reset")
            finally:
                reopened.close()
