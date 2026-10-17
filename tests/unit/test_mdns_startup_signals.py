"""The name publisher must survive a network event that arrives while it starts.

On 2026-10-10 the Pi booted, NetworkManager reached "connected", and the
dispatcher hook sent SIGHUP to jittersim-mdns.service in the same second the
unit started. The publisher had not installed its SIGHUP handler yet (it did
that after reading the name and running openssl), so the default action ended
the process. systemd counts a SIGHUP death as a clean exit, so
Restart=on-failure never started it again and the dashboard name stayed
silent for six days.
"""

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from jittersim import mdnsname

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PI = ROOT / "deploy" / "raspberry-pi"

# Runs the real command line with a slow setup step and a counting publisher.
DRIVER = textwrap.dedent("""\
    import sys, time
    from pathlib import Path
    from jittersim import mdnsname

    steps, marker = Path(sys.argv[1]), Path(sys.argv[2])

    def slow_names(name, trust_env=None):
        marker.write_text("setup started\\n")
        time.sleep(1.5)
        return ()

    class Counting:
        def __init__(self, **options):
            pass

        def step(self):
            with steps.open("a") as handle:
                handle.write("%f\\n" % time.monotonic())

        def close(self):
            pass

    mdnsname._extra_names = slow_names
    mdnsname.Publisher = Counting
    sys.exit(mdnsname.main(["publish", "--runtime-env", sys.argv[3],
                            "--trust-env", sys.argv[4], "--status", sys.argv[5]]))
""")


class HandlersComeFirstTests(unittest.TestCase):
    """No slow or failing work may run while SIGHUP still kills the process."""

    def run_publish(self, setup_event=None):
        events, handlers = [], {}

        def record_signal(number, handler):
            events.append(("signal", number))
            handlers[number] = handler

        class FakePublisher:
            def __init__(self, **options):
                events.append(("publisher", None))
                self.steps = 0

            def step(self):
                self.steps += 1
                events.append(("step", None))

            def close(self):
                events.append(("close", None))

        def extra_names(name, trust_env=None):
            events.append(("names", None))
            if setup_event:
                setup_event(handlers)
            return ()

        arguments = mdnsname.argparse.Namespace(
            runtime_env=Path("/nonexistent/runtime.env"),
            trust_env=Path("/nonexistent/trust.env"), status=Path("/nonexistent/status.json"))
        with mock.patch.object(mdnsname.signal, "signal", side_effect=record_signal), \
                mock.patch.object(mdnsname, "Publisher", FakePublisher), \
                mock.patch.object(mdnsname, "_extra_names", extra_names), \
                mock.patch.object(mdnsname, "read_local_name",
                                  lambda path=None: events.append(("read_name", None))
                                  or "netlab.local"), \
                mock.patch.object(mdnsname, "run_forever") as run_forever:
            mdnsname._publish_command(arguments)
        return events, handlers, run_forever

    def test_every_handler_is_installed_before_any_setup_work(self) -> None:
        events, _handlers, _run = self.run_publish()

        kinds = [kind for kind, _number in events]
        self.assertEqual(
            {number for kind, number in events if kind == "signal"},
            {signal.SIGHUP, signal.SIGTERM, signal.SIGINT})
        first_work = min(kinds.index(kind) for kind in ("read_name", "names", "publisher"))
        self.assertEqual(kinds[:first_work].count("signal"), 3, events)

    def test_a_hangup_during_setup_is_remembered_and_answered_once_the_loop_runs(self) -> None:
        def hang_up(handlers):
            handlers[signal.SIGHUP](signal.SIGHUP, None)

        _events, _handlers, run_forever = self.run_publish(hang_up)

        woken = run_forever.call_args.kwargs["woken"]
        self.assertTrue(woken())
        self.assertFalse(woken())

    def test_without_a_hangup_the_loop_starts_quietly(self) -> None:
        _events, _handlers, run_forever = self.run_publish()

        self.assertFalse(run_forever.call_args.kwargs["woken"]())
        self.assertFalse(run_forever.call_args.kwargs["stopped"]())

    def test_a_stop_during_setup_is_not_lost(self) -> None:
        def terminate(handlers):
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        _events, _handlers, run_forever = self.run_publish(terminate)

        self.assertTrue(run_forever.call_args.kwargs["stopped"]())


class SighupWhileStartingTests(unittest.TestCase):
    """The real process, with a slow setup, hit by the network hook's signal."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.steps, self.marker = root / "steps.log", root / "marker"
        driver = root / "driver.py"
        driver.write_text(DRIVER)
        environment = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
        self.process = subprocess.Popen(
            [sys.executable, str(driver), str(self.steps), str(self.marker),
             str(root / "runtime.env"), str(root / "trust.env"), str(root / "status.json")],
            env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop)

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        if self.process.stderr:
            self.process.stderr.close()

    def wait_for(self, condition, seconds: float, what: str) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            self.assertIsNone(self.process.poll(),
                              "the publisher ended while waiting for %s (exit %s)"
                              % (what, self.process.returncode))
            time.sleep(0.02)
        self.fail("timed out waiting for %s" % what)

    def step_times(self) -> list:
        try:
            return [float(line) for line in self.steps.read_text().split()]
        except FileNotFoundError:
            return []

    def test_a_hangup_during_slow_setup_does_not_end_the_publisher_and_is_answered(self) -> None:
        self.wait_for(self.marker.exists, 30, "the setup to start")

        self.process.send_signal(signal.SIGHUP)

        self.wait_for(lambda: len(self.step_times()) >= 1, 30, "the first pass")
        self.assertIsNone(self.process.poll(), "SIGHUP during setup ended the publisher")
        # The hangup arrived before the loop. It is answered at once, not after
        # the five seconds of the regular check.
        self.wait_for(lambda: len(self.step_times()) >= 2, 3, "the pass the hangup asked for")
        first, second = self.step_times()[:2]
        self.assertLess(second - first, 2.0)

    def test_a_hangup_while_running_checks_again_at_once(self) -> None:
        self.wait_for(lambda: len(self.step_times()) >= 1, 30, "the first pass")
        time.sleep(0.8)
        before = len(self.step_times())

        self.process.send_signal(signal.SIGHUP)

        self.wait_for(lambda: len(self.step_times()) > before, 3, "the pass the hangup asked for")
        self.assertIsNone(self.process.poll())

    def test_a_stop_during_slow_setup_ends_the_publisher_cleanly(self) -> None:
        self.wait_for(self.marker.exists, 30, "the setup to start")

        self.process.send_signal(signal.SIGTERM)

        self.assertEqual(self.process.wait(timeout=15), 0)


class ServiceRestartTests(unittest.TestCase):
    """Whatever still ends the publisher, systemd starts it again."""

    def setUp(self) -> None:
        self.unit = (PI / "jittersim-mdns.service").read_text()
        self.settings = {}
        for line in self.unit.splitlines():
            key, separator, value = line.partition("=")
            if separator and not line.startswith("#"):
                self.settings[key.strip()] = value.strip()

    def test_the_publisher_is_restarted_after_any_exit_including_a_hangup(self) -> None:
        # systemd treats SIGHUP, SIGINT, SIGTERM and SIGPIPE as clean exits, so
        # Restart=on-failure would leave a publisher that died of SIGHUP dead.
        self.assertEqual(self.settings.get("Restart"), "always")

    def test_the_restart_delay_is_short_but_not_a_busy_loop(self) -> None:
        delay = float(self.settings["RestartSec"].rstrip("s"))
        self.assertGreaterEqual(delay, 2)
        self.assertLessEqual(delay, 30)

    def test_a_publisher_that_keeps_failing_still_stops_retrying(self) -> None:
        self.assertIn("StartLimitIntervalSec", self.settings)
        self.assertIn("StartLimitBurst", self.settings)
        self.assertNotEqual(self.settings["StartLimitIntervalSec"], "0")


if __name__ == "__main__":
    unittest.main()
