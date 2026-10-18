"""Boot-time pre-steps of two units must not write errors or the ruleset to the journal.

On the Pi every boot logged `Error: Could not process rule: No such file or
directory` from the control firewall (it deleted a table that does not exist yet)
and about 27 lines of nft ruleset from jittersim-ap.service (it listed the table
only to check that it exists). Both are harmless, and both make a real problem
harder to see in the journal.
"""

import os
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

PI = Path(__file__).resolve().parents[2] / "deploy" / "raspberry-pi"

RULESET = "\n".join(
    ["table inet jittersim_control {", "\tchain input {"]
    + ["\t\tiifname \"wlan0\" tcp dport %d counter accept" % port for port in range(8080, 8100)]
    + ["\t}", "}"])

# A model of `nft` for the one table: it exists or it does not.
FAKE_NFT = """#!/bin/sh
state="$FAKE_NFT_STATE"
case "$1 $2" in
    "list table")
        if [ -e "$state" ]; then cat <<'RULES'
%s
RULES
        else echo "Error: Could not process rule: No such file or directory" >&2
             echo "list table inet jittersim_control" >&2; exit 1; fi ;;
    "add table") : > "$state" ;;
    "delete table")
        if [ -e "$state" ]; then rm -f "$state"
        else echo "Error: Could not process rule: No such file or directory" >&2
             echo "delete table inet jittersim_control" >&2; exit 1; fi ;;
    *) echo "unexpected nft call: $*" >&2; exit 2 ;;
esac
""" % RULESET


def pre_steps(unit_path: Path) -> list:
    """The ExecStartPre lines that call nft, as (ignore_failure, argument list)."""
    steps = []
    for line in unit_path.read_text().splitlines():
        if line.startswith("ExecStartPre=") and "/usr/sbin/nft" in line:
            command = line.split("=", 1)[1]
            ignore = command.startswith("-")
            steps.append((ignore, shlex.split(command.lstrip("-"))))
    return steps


class BootNoiseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.fake = root / "nft"
        self.fake.write_text(FAKE_NFT)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.state = root / "table-exists"
        self.env = {**os.environ, "FAKE_NFT_STATE": str(self.state)}

    def run_step(self, arguments: list) -> subprocess.CompletedProcess:
        # The units name nft by its absolute path. Point it at the model.
        command = [self.fake if part == "/usr/sbin/nft" else part for part in arguments]
        command = [str(part) for part in command]
        if command[:2] == ["/bin/sh", "-c"]:
            command[2] = command[2].replace("/usr/sbin/nft", str(self.fake))
        return subprocess.run(command, capture_output=True, text=True, env=self.env, timeout=10)

    def test_the_control_firewall_clears_an_old_table_without_an_error_either_way(self) -> None:
        # The rules file itself replaces the table in one transaction: `add table`
        # (a no-op when it exists) before `delete table`, so the delete always
        # finds a table and never logs "No such file or directory" at boot. There
        # is no separate nft step that would leave a moment without the rules.
        self.assertEqual(pre_steps(PI / "jittersim-control-firewall.service"), [])
        commands = [line.strip() for line in
                    (PI / "jittersim-control-firewall.nft").read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(commands[:3], ["add table inet jittersim_control",
                                        "delete table inet jittersim_control",
                                        "table inet jittersim_control {"])

    def test_the_wifi_unit_checks_the_table_without_listing_the_ruleset(self) -> None:
        steps = pre_steps(PI / "jittersim-ap.service")
        self.assertEqual(len(steps), 1)
        self.state.write_text("")

        done = self.run_step(steps[0][1])

        self.assertEqual(done.returncode, 0)
        output = (done.stdout + done.stderr).splitlines()
        self.assertLessEqual(len(output), 2, output)
        self.assertIn("jittersim_control", "\n".join(output))
        for word in ("chain", "dport", "accept", "iifname"):
            self.assertNotIn(word, done.stdout + done.stderr)

    def test_the_wifi_still_does_not_start_without_the_management_firewall(self) -> None:
        steps = pre_steps(PI / "jittersim-ap.service")
        self.assertFalse(steps[0][0], "a failing check must stop the unit, so no leading '-'")
        self.state.unlink(missing_ok=True)

        done = self.run_step(steps[0][1])

        self.assertNotEqual(done.returncode, 0)
        self.assertIn("No such file or directory", done.stderr)


if __name__ == "__main__":
    unittest.main()
