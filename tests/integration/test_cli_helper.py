import io
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jittersim.cli import main, reset_main
from jittersim.config.models import MacPolicy, WebsitePolicy
from jittersim.config.storage import ConfigRepository
from jittersim.engine.access import build_access_plan
from jittersim.networking.access import AccessManager
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService, UnixHelperServer
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor


class CliHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp_dir.name) / "helper.sock"
        self.database = Path(self.temp_dir.name) / "jittersim.db"
        self.executor = FakeExecutor()
        access_directory = Path(self.temp_dir.name) / 'access'
        self.access = AccessManager(executor=AccessExecutor(access_directory),
                                    topology=Topology('eth0', 'wlan0'), directory=access_directory,
                                    runtime_path=Path(self.temp_dir.name) / 'access.json')
        service = HelperService(
            NetworkApplier(
                executor=self.executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ), access_manager=self.access,
        )
        self.helper_server = UnixHelperServer(self.socket_path, service)
        self.helper_thread = threading.Thread(target=self.helper_server.serve_forever)
        self.helper_thread.start()
        self.helper_running = True
        with ConfigRepository(self.database) as repository:
            repository.update_runtime_state(
                observed_state="applied",
                last_action="apply",
                last_result="success",
                last_error=None,
            )

    def tearDown(self) -> None:
        self._stop_helper()
        self.temp_dir.cleanup()

    def _stop_helper(self) -> None:
        if self.helper_running:
            self.helper_server.shutdown()
            self.helper_server.server_close()
            self.helper_thread.join(timeout=2)
            self.helper_running = False

    def _run(self, *arguments: str, database: Path = None):
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(
                [
                    *arguments,
                    "--database",
                    str(database or self.database),
                    "--helper-socket",
                    str(self.socket_path),
                ]
            )
        return code, output.getvalue(), errors.getvalue()

    def _state(self):
        with ConfigRepository(self.database) as repository:
            return repository.get_runtime_state()

    def test_reset_records_the_reset_then_clears_the_network(self) -> None:
        code, output, _ = self._run("reset")

        state = self._state()
        self.assertEqual(code, 0)
        self.assertIn("Network reset", output)
        self.assertIn(
            "remove the project-owned nftables table",
            [command.purpose for command in self.executor.commands],
        )
        self.assertEqual(
            (state.observed_state, state.last_action, state.last_result),
            ("reset", "reset", "success"),
        )

    def test_jittersim_reset_is_the_same_command(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = reset_main(
                ["--database", str(self.database), "--helper-socket", str(self.socket_path)]
            )

        self.assertEqual(code, 0)
        self.assertIn("Network reset", output.getvalue())
        self.assertEqual(self._state().last_action, "reset")

    def test_normal_access_disables_persistent_rules_while_reset_preserves_them(self):
        with ConfigRepository(self.database) as repository:
            repository.save_access_policies(MacPolicy(), WebsitePolicy(blocked_domains=('example.com',)))
            self.access.apply(build_access_plan(repository.access_settings()))
        self.assertEqual(self._run('reset')[0], 0)
        self.assertTrue(self.access.applied.options.enabled)
        code, output, errors = self._run('normal-access')
        self.assertEqual(code, 0, errors)
        self.assertIn('Normal access restored', output)
        self.assertFalse(self.access.applied.options.enabled)
        with ConfigRepository(self.database) as repository:
            self.assertFalse(repository.get_access_options().enabled)
            self.assertEqual(repository.get_website_policy().blocked_domains, ('example.com',))
            self.assertEqual(repository.get_runtime_state().last_action, 'reset')

    def test_reset_still_clears_the_network_without_the_database(self) -> None:
        missing = Path(self.temp_dir.name) / "missing.db"

        code, _, errors = self._run("reset", database=missing)

        self.assertEqual(code, 0)
        self.assertIn("could not record the reset", errors)
        self.assertFalse(missing.exists())
        self.assertTrue(self.executor.commands)

    def test_reset_reports_a_helper_that_does_not_answer(self) -> None:
        self._stop_helper()

        code, _, errors = self._run("reset")

        state = self._state()
        self.assertEqual(code, 1)
        self.assertIn("Reset failed", errors)
        self.assertEqual(
            (state.observed_state, state.last_action, state.last_result),
            ("error", "reset", "failure"),
        )

    def test_status_shows_the_recorded_state_and_whether_the_helper_answers(self) -> None:
        code, output, _ = self._run("status")
        self._stop_helper()
        stopped_code, stopped_output, _ = self._run("status")

        self.assertEqual(code, 0)
        self.assertIn("Last network action: apply (success)", output)
        self.assertIn("Helper: answering", output)
        self.assertEqual(stopped_code, 1)
        self.assertIn("Helper: not answering", stopped_output)


if __name__ == "__main__":
    unittest.main()
