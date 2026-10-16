"""An upgrade that clears the Traffic list records it and every place that shows the list says so."""

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from jittersim.api.server import DASHBOARD_HTML, ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import DomainObservation
from jittersim.config.storage import ConfigRepository

PROTECTED = "02:00:00:00:00:09"
NOW = 1_800_000_000


def observation(address, domain, mac=None):
    return DomainObservation(address, domain, 3, NOW - 100, NOW - 50, mac)


def old_database(directory, *, rows=3, protected=True):
    """A database from before the hardware address column. Its names have no address."""

    path = Path(directory) / "settings.db"
    repository = ConfigRepository(path)
    repository.save_domain_observations(
        [observation(f"192.168.50.{20 + index}", f"site{index}.example.com") for index in range(rows)],
        now=NOW,
    )
    if protected:
        repository.save_unaffected_device(UnaffectedDevice(mac_address=PROTECTED, display_name="Phone"))
    repository.close()
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        ALTER TABLE domain_observations RENAME TO domain_observations_old;
        CREATE TABLE domain_observations (
            ipv4_address TEXT NOT NULL, domain TEXT NOT NULL, query_count INTEGER NOT NULL,
            first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
            PRIMARY KEY (ipv4_address, domain)
        );
        INSERT INTO domain_observations
            SELECT ipv4_address, domain, query_count, first_seen, last_seen
            FROM domain_observations_old;
        DROP TABLE domain_observations_old;
        DROP TABLE domain_list_clearing;
        """
    )
    connection.commit()
    connection.close()
    return path


class DomainListClearingTests(unittest.TestCase):
    def test_an_upgrade_with_protected_devices_clears_the_list_and_records_it_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = old_database(directory)
            before = int(time.time())

            upgraded = ConfigRepository(path)
            try:
                self.assertEqual(upgraded.list_domain_observations(), ())
                cleared_at, rows_cleared = upgraded.get_domain_list_clearing()
                self.assertEqual(rows_cleared, 3)
                self.assertTrue(before <= cleared_at <= int(time.time()))
            finally:
                upgraded.close()

            # opening again changes nothing: the same time and the same count
            time.sleep(1.1)
            reopened = ConfigRepository(path)
            try:
                self.assertEqual(reopened.get_domain_list_clearing(), (cleared_at, rows_cleared))
            finally:
                reopened.close()

    def test_an_upgrade_without_protected_devices_keeps_the_names_and_records_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = old_database(directory, protected=False)

            upgraded = ConfigRepository(path)
            try:
                self.assertEqual(len(upgraded.list_domain_observations()), 3)
                self.assertIsNone(upgraded.get_domain_list_clearing())
            finally:
                upgraded.close()

    def test_an_upgrade_of_an_empty_list_records_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            upgraded = ConfigRepository(old_database(directory, rows=0))
            try:
                self.assertIsNone(upgraded.get_domain_list_clearing())
            finally:
                upgraded.close()

    def test_the_first_new_observation_removes_the_record_for_good(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = old_database(directory)
            upgraded = ConfigRepository(path)
            try:
                self.assertIsNotNone(upgraded.get_domain_list_clearing())
                upgraded.save_domain_observations([], now=NOW)
                self.assertIsNotNone(upgraded.get_domain_list_clearing())
                upgraded.save_domain_observations(
                    [observation("192.168.50.30", "new.example.com", "02:00:00:00:00:01")], now=NOW
                )
                self.assertIsNone(upgraded.get_domain_list_clearing())
            finally:
                upgraded.close()
            # a name without a hardware address survives the next start too: it is not cleared again
            reopened = ConfigRepository(path)
            try:
                reopened.save_domain_observations([observation("192.168.50.31", "later.example.com")], now=NOW)
            finally:
                reopened.close()
            again = ConfigRepository(path)
            try:
                self.assertIsNone(again.get_domain_list_clearing())
                self.assertEqual(len(again.list_domain_observations()), 2)
            finally:
                again.close()

    def test_a_crash_while_recording_neither_loses_nor_repeats_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = old_database(directory)
            with mock.patch.object(
                ConfigRepository, "_record_domain_list_cleared", side_effect=RuntimeError("crash")
            ):
                with self.assertRaises(RuntimeError):
                    ConfigRepository(path)

            connection = sqlite3.connect(path)
            names = connection.execute("SELECT COUNT(*) FROM domain_observations").fetchone()[0]
            connection.close()
            self.assertEqual(names, 3)  # the clearing rolled back with the record

            recovered = ConfigRepository(path)
            try:
                self.assertEqual(recovered.get_domain_list_clearing()[1], 3)
                self.assertEqual(recovered.list_domain_observations(), ())
            finally:
                recovered.close()

    def test_a_second_record_never_replaces_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            upgraded = ConfigRepository(old_database(directory))
            try:
                first = upgraded.get_domain_list_clearing()
                upgraded._record_domain_list_cleared(99)
                self.assertEqual(upgraded.get_domain_list_clearing(), first)
            finally:
                upgraded.close()

    def test_a_current_database_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ConfigRepository(Path(directory) / "settings.db")
            try:
                repository.save_unaffected_device(UnaffectedDevice(mac_address=PROTECTED))
                repository.save_domain_observations([observation("192.168.50.20", "a.example.com")], now=NOW)
            finally:
                repository.close()
            reopened = ConfigRepository(Path(directory) / "settings.db")
            try:
                self.assertEqual(len(reopened.list_domain_observations()), 1)
                self.assertIsNone(reopened.get_domain_list_clearing())
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()


ROOT = Path(__file__).resolve().parents[2]
NOTICE = ROOT / "deploy" / "raspberry-pi" / "upgrade-notice.py"


def installer_notice(path):
    result = subprocess.run(
        [sys.executable, str(NOTICE), str(path)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class InstallerNoticeTests(unittest.TestCase):
    def test_the_installer_says_how_many_names_an_upgrade_will_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            notice = installer_notice(old_database(directory))

            self.assertEqual(
                notice,
                "Notice: this upgrade clears the 3 DNS names listed under Traffic because protected"
                " devices exist. The list fills again as devices browse."
                " Names are kept for 30 days from their last sighting.",
            )
            # it only reads: the names are still there for the upgrade to clear
            connection = sqlite3.connect(Path(directory) / "settings.db")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM domain_observations").fetchone()[0], 3)
            connection.close()

    def test_the_installer_stays_quiet_when_nothing_is_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for name, options in (("no-protected", {"protected": False}), ("no-names", {"rows": 0})):
                (Path(directory) / name).mkdir()
                self.assertEqual(installer_notice(old_database(Path(directory) / name, **options)), "", name)
            self.assertEqual(installer_notice(Path(directory) / "missing.db"), "")
            # a database that already has the column is not cleared again
            repository = ConfigRepository(Path(directory) / "current.db")
            repository.save_unaffected_device(UnaffectedDevice(mac_address=PROTECTED))
            repository.save_domain_observations([observation("192.168.50.20", "a.example.com")], now=NOW)
            repository.close()
            self.assertEqual(installer_notice(Path(directory) / "current.db"), "")

    def test_the_installer_runs_the_notice_before_it_replaces_anything(self) -> None:
        script = (ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()
        call = 'upgrade-notice.py" "$DATA_DIR/jittersim.db" || true'
        assert call in script
        assert script.index(call) < script.index("replace-source.py") < script.index("systemctl restart")

    def test_the_readme_explains_the_clearing_in_its_upgrade_notes(self) -> None:
        # The long guide moved to deploy/raspberry-pi/README.md when the main README became a short page.
        readme = " ".join((ROOT / "deploy" / "raspberry-pi" / "README.md").read_text().split())
        for phrase in ("clears the DNS names listed under Traffic once when any device is protected",
                       "prints a notice with the number of names before it happens",
                       "Names are kept for 30 days from their last sighting"):
            assert phrase in readme, phrase


class ObservationsApiTests(unittest.TestCase):
    def serve(self, directory, **options):
        repository = ConfigRepository(old_database(directory, **options))
        self.addCleanup(repository.close)
        server = ControlServer(("127.0.0.1", 0), repository)
        self.addCleanup(server.server_close)
        return repository, server

    def observations(self, server):
        status, _, body = server.response_for("/api/observations")
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_the_api_returns_the_record_with_the_message_the_page_and_the_cli_show(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, server = self.serve(directory)
            noon = int(datetime(2026, 10, 1, 12).timestamp())
            repository._connection.execute("UPDATE domain_list_clearing SET cleared_at = ?", (noon,))
            repository._connection.commit()

            cleared = self.observations(server)["list_cleared"]

            self.assertEqual(cleared, {"cleared_at": noon, "rows_cleared": 3, "message": CLEARED})

    def test_the_record_goes_with_the_first_new_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, server = self.serve(directory)
            self.assertIsNotNone(self.observations(server)["list_cleared"])

            repository.save_domain_observations([observation("192.168.50.40", "new.example.com")], now=NOW)

            self.assertIsNone(self.observations(server)["list_cleared"])

    def test_a_database_that_was_not_cleared_has_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, server = self.serve(directory, protected=False)

            self.assertIsNone(self.observations(server)["list_cleared"])


# The empty state of Traffic discovery. A small fake page lets the real render code run in node.
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
OLD_EMPTY = "No DNS observations yet. Connect a device and run a scan."
CLEARED = (
    "The list was cleared on 1 October 2026 when JitterSim was upgraded because protected devices exist."
    " It fills again as devices browse. Names are kept for 30 days from their last sighting."
)


def empty_state(data):
    start = SCRIPT.index("function renderObservations(")
    function = SCRIPT[start : SCRIPT.index("devices.forEach((device) => {", start)] + "}"
    program = (
        """
const fake = (tag) => ({tag, children: [], className: "", textContent: "",
  replaceChildren() { this.children = []; }, appendChild(node) { this.children.push(node); return node; }});
const nodes = {observations: fake("div"), "observation-count": fake("span")};
const element = (id) => nodes[id];
const document = {createElement: fake};
let observedDomainsByDevice = {}; let observedNames = new Set();
"""
        + function
        + "\nrenderObservations("
        + json.dumps(data)
        + ');\nconsole.log(JSON.stringify({texts: nodes.observations.children.map((node) => node.textContent),'
        " count: nodes['observation-count'].textContent}));"
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@NEEDS_NODE
def test_the_empty_list_says_it_was_cleared_by_the_upgrade():
    shown = empty_state({
        "available": True, "devices": [], "total_domains": 0,
        "list_cleared": {"cleared_at": 1, "rows_cleared": 5, "message": CLEARED},
    })
    assert shown["texts"] == [CLEARED]


@NEEDS_NODE
@pytest.mark.parametrize("extra", [{}, {"list_cleared": None}])
def test_an_empty_list_for_another_reason_keeps_the_old_text(extra):
    shown = empty_state({"available": True, "devices": [], "total_domains": 0, **extra})
    assert shown["texts"] == [OLD_EMPTY]


@NEEDS_NODE
def test_the_page_never_adds_its_own_wording_to_the_message():
    # The message comes from the API so the dashboard and the command line use the same words.
    assert "was cleared on" not in SCRIPT
