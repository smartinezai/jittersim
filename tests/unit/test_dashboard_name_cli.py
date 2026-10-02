"""`jittersim dashboard-name` shows and changes the one dashboard address."""

import io
import json
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jittersim import cli, dashboard_name_cli


class Runner:
    def __init__(self, fail=lambda command: False):
        self.commands = []
        self.fail = fail

    def __call__(self, command, **options):
        self.commands.append(list(command))
        code = 1 if self.fail(command) else 0
        return subprocess.CompletedProcess(command, code, stdout="", stderr="")


class DashboardNameCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name)
        self.status = self.config / "status.json"
        self.runner = Runner()

    def run_cli(self, *arguments, root=True, fail=None, answers=None):
        if fail is not None:
            self.runner = Runner(fail)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = dashboard_name_cli.main(
                list(arguments), run=self.runner, euid=lambda: 0 if root else 1000,
                config_dir=self.config, status_path=self.status,
                ask=(lambda prompt: (answers or ["yes"]).pop(0)))
        return code, out.getvalue(), err.getvalue()

    def saved(self) -> str:
        return (self.config / "trust.env").read_text()

    def test_show_prints_the_address_and_whether_it_is_announced(self) -> None:
        self.status.write_text(json.dumps({
            "state": "published", "name": "netlab.local", "updated": int(time.time()),
            "message": "netlab.local opens the dashboard on Ethernet (192.168.0.23) and on the "
                       "test Wi-Fi (192.168.50.1).",
            "links": [{"link": "ethernet", "interface": "eth0", "address": "192.168.0.23",
                       "state": "published"}], "extra": []}))

        code, out, _ = self.run_cli("show", root=False)

        self.assertEqual(code, 0)
        self.assertIn("https://netlab.local:8080", out)
        self.assertIn("192.168.0.23", out)

    def test_no_arguments_means_show(self) -> None:
        code, out, _ = self.run_cli(root=False)
        self.assertEqual(code, 0)
        self.assertIn("https://netlab.local:8080", out)

    def test_show_reports_a_conflict_with_its_fix(self) -> None:
        self.status.write_text(json.dumps({
            "state": "conflict", "name": "netlab.local", "updated": int(time.time()),
            "message": "Another device on the Ethernet (router network) already uses netlab.local.",
            "fix": "Choose another name: sudo jittersim dashboard-name set <other>.local",
            "links": [], "extra": []}))

        _, out, _ = self.run_cli("show", root=False)

        self.assertIn("already uses netlab.local", out)
        self.assertIn("dashboard-name set", out)

    def test_set_validates_before_touching_anything(self) -> None:
        for bad in ("jittersim.local", "JitterSim.local", "x.example.com", "two.parts.local",
                    "pi.home.arpa", "", "bad-.local"):
            with self.subTest(bad=bad):
                code, _, err = self.run_cli("set", bad, "--yes")
                self.assertEqual(code, 2)
                self.assertIn("not changed", err.lower())
        self.assertEqual(self.runner.commands, [])
        self.assertFalse((self.config / "trust.env").exists())

    def test_set_needs_root(self) -> None:
        code, _, err = self.run_cli("set", "bench.local", "--yes", root=False)

        self.assertEqual(code, 1)
        self.assertIn("sudo", err)
        self.assertEqual(self.runner.commands, [])

    def test_set_reissues_the_certificate_then_saves_then_restarts(self) -> None:
        code, out, err = self.run_cli("set", "Bench.Local", "--yes")

        self.assertEqual(code, 0, err)
        self.assertIn("JITTERSIM_DASHBOARD_NAME=bench.local", self.saved())
        words = [" ".join(command[1:]) if command[0].endswith("python") or "python" in command[0]
                 else " ".join(command) for command in self.runner.commands]
        certificate = next(i for i, text in enumerate(words) if "jittersim.localca ensure" in text)
        self.assertIn("--neutral-name bench.local", words[certificate])
        record = next(i for i, text in enumerate(words) if "jittersim.localname install" in text)
        publisher = words.index("systemctl restart jittersim-mdns.service")
        dashboard = words.index("systemctl restart jittersim-dashboard.service")
        self.assertLess(certificate, record)
        self.assertLess(record, publisher)
        self.assertLess(publisher, dashboard)
        self.assertIn("https://bench.local:8080", out)
        self.assertIn("sign in once", out.lower())

    def test_a_failed_certificate_leaves_the_name_unchanged(self) -> None:
        (self.config / "trust.env").write_text("JITTERSIM_DASHBOARD_NAME=netlab.local\n")

        code, _, err = self.run_cli(
            "set", "bench.local", "--yes", fail=lambda command: "jittersim.localca" in " ".join(command))

        self.assertEqual(code, 1)
        self.assertEqual(self.saved(), "JITTERSIM_DASHBOARD_NAME=netlab.local\n")
        self.assertIn("not changed", err.lower())
        self.assertFalse([c for c in self.runner.commands if "systemctl" in c[0]])

    def test_a_failed_restart_is_reported_but_the_name_stays_saved(self) -> None:
        code, _, err = self.run_cli(
            "set", "bench.local", "--yes",
            fail=lambda command: command[-1] == "jittersim-mdns.service")

        self.assertEqual(code, 1)
        self.assertIn("JITTERSIM_DASHBOARD_NAME=bench.local", self.saved())
        self.assertIn("jittersim-mdns", err)
        # The dashboard still restarts so it serves the new certificate.
        self.assertIn(["systemctl", "restart", "jittersim-dashboard.service"], self.runner.commands)

    def test_setting_the_current_name_changes_nothing(self) -> None:
        (self.config / "trust.env").write_text("JITTERSIM_DASHBOARD_NAME=bench.local\n")

        code, out, _ = self.run_cli("set", "bench.local", "--yes")

        self.assertEqual(code, 0)
        self.assertIn("already", out)
        self.assertEqual(self.runner.commands, [])

    def test_without_yes_the_change_is_confirmed_and_can_be_declined(self) -> None:
        code, out, _ = self.run_cli("set", "bench.local", answers=["no"])
        self.assertEqual(code, 0)
        self.assertIn("not changed", out.lower())
        self.assertEqual(self.runner.commands, [])
        self.assertFalse((self.config / "trust.env").exists())

        code, _, _ = self.run_cli("set", "bench.local", answers=["yes"])
        self.assertEqual(code, 0)
        self.assertIn("bench.local", self.saved())

    def test_diagnostics_print_the_address_and_the_announcement(self) -> None:
        self.status.write_text(json.dumps({
            "state": "conflict", "name": "netlab.local", "updated": int(time.time()),
            "message": "Another device on the Ethernet (router network) already uses netlab.local.",
            "fix": "Choose another name: sudo jittersim dashboard-name set <other>.local",
            "links": [], "extra": []}))
        out = io.StringIO()

        with redirect_stdout(out):
            cli._print_dashboard_name(self.config, self.status)

        text = out.getvalue()
        self.assertIn("Dashboard address: https://netlab.local:8080", text)
        self.assertIn("announcement: conflict", text)
        self.assertIn("already uses netlab.local", text)
        self.assertIn("dashboard-name set", text)

    def test_the_name_service_log_can_be_read_with_the_logs_command(self) -> None:
        self.assertEqual(cli.LOG_SERVICES["name"], "jittersim-mdns.service")

    def test_the_main_command_dispatches_to_it(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["dashboard-name", "--config-dir", str(self.config),
                             "--status", str(self.status), "show"])
        self.assertEqual(code, 0)
        self.assertIn("https://netlab.local:8080", out.getvalue())


if __name__ == "__main__":
    unittest.main()
