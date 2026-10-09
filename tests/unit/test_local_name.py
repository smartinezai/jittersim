import tempfile
import unittest
from pathlib import Path

from jittersim import localname
from jittersim.config.models import AccessOptions, WebsitePolicy
from jittersim.engine.access import AccessPlan
from jittersim.networking.access import render_dns

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECORD = 'local-data: "netlab.local. 60 IN A 192.168.50.1"'
LEGACY_RECORD = 'local-data: "pi.home.arpa. 60 IN A 192.168.50.1"'


class DashboardNameValidationTests(unittest.TestCase):
    def test_one_neutral_label_followed_by_dot_local_is_accepted(self) -> None:
        for value, expected in (
            ("netlab.local", "netlab.local"),
            ("  NetLab.Local. ", "netlab.local"),
            ("lab-2.local", "lab-2.local"),
            ("a.local", "a.local"),
            ("x" * 63 + ".local", "x" * 63 + ".local"),
            ("1234.local", "1234.local"),
        ):
            with self.subTest(value=value):
                self.assertEqual(localname.validate_local_name(value), expected)

    def test_everything_else_is_refused_with_a_plain_reason(self) -> None:
        for value in (
            "",
            "netlab",
            "netlab.example.com",
            "netlab.home.arpa",
            "pi.home.arpa",
            "two.labels.local",
            ".local",
            "-lab.local",
            "lab-.local",
            "lab_x.local",
            "lab x.local",
            "lab\n.local",
            "x" * 64 + ".local",
            "räuter.local",
            "lab.local/path",
            "lab.local:8080",
            "192.168.0.5",
            "local",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    localname.validate_local_name(value)
                self.assertTrue(str(caught.exception))

    def test_the_product_name_is_never_allowed_in_any_spelling(self) -> None:
        for value in (
            "jittersim.local",
            "JitterSim.local",
            "my-jittersim.local",
            "jitter-sim.local",
            "jittersim2.local",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "neutral"):
                    localname.validate_local_name(value)

    def test_the_default_is_valid_and_neutral(self) -> None:
        self.assertEqual(localname.DEFAULT_LOCAL_NAME, "netlab.local")
        self.assertEqual(localname.validate_local_name(localname.DEFAULT_LOCAL_NAME), "netlab.local")
        self.assertNotIn("jittersim", localname.DEFAULT_LOCAL_NAME)


class LocalNameTests(unittest.TestCase):
    def test_filtered_resolver_answers_the_name_in_every_view(self) -> None:
        plans = {
            "off": AccessPlan(options=AccessOptions(enabled=False)),
            "blocking": AccessPlan(websites=WebsitePolicy("unrestricted", (), ("example.com",))),
            "allow only": AccessPlan(websites=WebsitePolicy("allow_only", ("example.com",), ())),
        }
        for label, plan in plans.items():
            with self.subTest(label):
                config = render_dns(plan)
                views = config.split("\nview:\n")
                self.assertGreaterEqual(len(views), 2)
                for section in views[1:]:
                    self.assertIn(RECORD, section)
                    self.assertIn('local-zone: "netlab.local." static', section)
                    # Bookmarks made while the name was pi.home.arpa keep working.
                    self.assertIn(LEGACY_RECORD, section)
                self.assertIn(RECORD, views[0])

    def test_a_configured_name_is_used_and_never_names_the_product(self) -> None:
        config = render_dns(AccessPlan(), local_name="dash.local")

        self.assertIn('local-data: "dash.local. 60 IN A 192.168.50.1"', config)
        self.assertNotIn("netlab.local", config)
        self.assertNotIn("jittersim.local", config)
        with self.assertRaises(ValueError):
            render_dns(AccessPlan(), local_name="jittersim.local")

    def test_shared_network_resolver_answers_the_name_when_rules_are_off(self) -> None:
        self.assertEqual(
            localname.render_shared_dnsmasq("netlab.local"),
            "address=/netlab.local/192.168.50.1\naddress=/pi.home.arpa/192.168.50.1\n")
        with self.assertRaises(ValueError):
            localname.render_shared_dnsmasq("example.com")

    def test_the_name_is_read_from_the_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trust.env"
            self.assertEqual(localname.read_local_name(path), "netlab.local")
            path.write_text("OTHER=1\nJITTERSIM_DASHBOARD_NAME=Lab.Local\n")
            self.assertEqual(localname.read_local_name(path), "lab.local")
            path.write_text("JITTERSIM_DASHBOARD_NAME='quoted.local'\n")
            self.assertEqual(localname.read_local_name(path), "quoted.local")
            # An invalid explicit choice must not silently change the address.
            path.write_text("JITTERSIM_DASHBOARD_NAME=jittersim.local\n")
            with self.assertRaises(ValueError):
                localname.read_local_name(path)
            path.write_text("JITTERSIM_DASHBOARD_NAME=pi.home.arpa\n")
            with self.assertRaises(ValueError):
                localname.read_local_name(path)

    def test_a_name_saved_under_the_earlier_key_is_still_read_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trust.env"
            path.write_text("JITTERSIM_LOCAL_NAME=old-key.local\n")
            self.assertEqual(localname.read_local_name(path), "old-key.local")
            path.write_text("JITTERSIM_LOCAL_NAME=pi.home.arpa\n")
            self.assertEqual(localname.read_local_name(path), "netlab.local")
            # The newer key wins when both exist.
            path.write_text("JITTERSIM_LOCAL_NAME=old-key.local\nJITTERSIM_DASHBOARD_NAME=new.local\n")
            self.assertEqual(localname.read_local_name(path), "new.local")

    def test_install_writes_the_default_name_and_the_shared_resolver_record_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, dns = Path(directory) / "etc", Path(directory) / "dnsmasq"
            config.mkdir()

            name, changed = localname.install_config(config, dns)

            self.assertEqual((name, changed), ("netlab.local", True))
            self.assertEqual(
                (config / "trust.env").read_text(), "JITTERSIM_DASHBOARD_NAME=netlab.local\n")
            record = dns / "jittersim-dashboard-name.conf"
            self.assertEqual(
                record.read_text(),
                "address=/netlab.local/192.168.50.1\naddress=/pi.home.arpa/192.168.50.1\n")
            self.assertEqual(localname.install_config(config, dns), ("netlab.local", False))

            (config / "trust.env").write_text("JITTERSIM_DASHBOARD_NAME=dash.local\n")
            self.assertEqual(localname.install_config(config, dns), ("dash.local", True))
            self.assertIn("address=/dash.local/192.168.50.1\n", record.read_text())

            # Preserve the last resolver record when an explicit choice is invalid.
            before = record.read_text()
            (config / "trust.env").write_text("JITTERSIM_DASHBOARD_NAME=jittersim.local\n")
            with self.assertRaises(ValueError):
                localname.install_config(config, dns)
            self.assertEqual(record.read_text(), before)

    def test_install_makes_the_test_wifi_dhcp_server_answer_foreign_leases_at_once(self) -> None:
        # A device that roams from the router's Wi-Fi to the Pi's copied name asks
        # for its old 192.168.0.x lease first (INIT-REBOOT). NetworkManager's
        # shared-mode dnsmasq is not authoritative, ignores that request and the
        # device waits out its own timeout (15 s for ISC dhclient in the outage
        # lab, tests/kernel/test_outage_lab.py scenario 2). An authoritative server
        # answers DHCPNAK at once. Only the Pi serves 192.168.50.0/24.
        with tempfile.TemporaryDirectory() as directory:
            config, dns = Path(directory) / "etc", Path(directory) / "dnsmasq"
            config.mkdir()
            _, changed = localname.install_config(config, dns)
            self.assertTrue(changed)
            dhcp = dns / localname.SHARED_DHCP_FILE
            self.assertEqual(dhcp.read_text(), "dhcp-authoritative\n")
            self.assertEqual(dhcp.stat().st_mode & 0o777, 0o644)
            self.assertEqual(localname.install_config(config, dns), ("netlab.local", False))
            # An existing installation that only had the name record still reports a
            # change, so the installer tells the owner to restart the test Wi-Fi.
            dhcp.unlink()
            self.assertEqual(localname.install_config(config, dns), ("netlab.local", True))

    def test_install_moves_a_name_saved_under_the_earlier_key_to_the_new_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, dns = Path(directory) / "etc", Path(directory) / "dnsmasq"
            config.mkdir()
            (config / "trust.env").write_text("KEEP=1\nJITTERSIM_LOCAL_NAME=pi.home.arpa\n")

            name, _ = localname.install_config(config, dns)

            self.assertEqual(name, "netlab.local")
            lines = (config / "trust.env").read_text().splitlines()
            self.assertIn("KEEP=1", lines)
            self.assertIn("JITTERSIM_DASHBOARD_NAME=netlab.local", lines)
            self.assertIn("JITTERSIM_LOCAL_NAME=pi.home.arpa", lines)
            self.assertIn("address=/pi.home.arpa/192.168.50.1", (dns / localname.SHARED_DNS_FILE).read_text())

    def test_set_name_validates_saves_and_keeps_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / "trust.env").write_text("KEEP=1\nJITTERSIM_DASHBOARD_NAME=netlab.local\n")

            self.assertEqual(localname.set_local_name("Lab-Net.Local", config), "lab-net.local")

            self.assertEqual(
                (config / "trust.env").read_text(),
                "KEEP=1\nJITTERSIM_DASHBOARD_NAME=lab-net.local\n")
            before = (config / "trust.env").read_text()
            for bad in ("jittersim.local", "x.example.com", ""):
                with self.assertRaises(ValueError):
                    localname.set_local_name(bad, config)
            self.assertEqual((config / "trust.env").read_text(), before)

    def test_set_name_creates_the_file_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            localname.set_local_name("fresh.local", config)
            self.assertEqual(
                (config / "trust.env").read_text(), "JITTERSIM_DASHBOARD_NAME=fresh.local\n")

    def test_installer_publishes_the_name_to_the_shared_resolver(self) -> None:
        installer = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()

        self.assertIn("-m jittersim.localname install", installer)


if __name__ == "__main__":
    unittest.main()
