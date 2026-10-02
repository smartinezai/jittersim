import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jittersim import localname
from jittersim.config.models import AccessOptions, WebsitePolicy
from jittersim.engine.access import AccessPlan
from jittersim.networking.access import render_dns

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECORD = 'local-data: "pi.home.arpa. 60 IN A 192.168.50.1"'


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
                    self.assertIn('local-zone: "pi.home.arpa." static', section)
                self.assertIn(RECORD, views[0])

    def test_a_configured_name_is_used_and_never_names_the_product(self) -> None:
        config = render_dns(AccessPlan(), local_name="dash.home.arpa")

        self.assertIn('local-data: "dash.home.arpa. 60 IN A 192.168.50.1"', config)
        self.assertNotIn("pi.home.arpa", config)
        self.assertNotIn("jittersim.home", config)
        with self.assertRaises(ValueError):
            render_dns(AccessPlan(), local_name="jittersim.home.arpa")

    def test_shared_network_resolver_answers_the_name_when_rules_are_off(self) -> None:
        self.assertEqual(
            localname.render_shared_dnsmasq("pi.home.arpa"),
            "address=/pi.home.arpa/192.168.50.1\n")
        with self.assertRaises(ValueError):
            localname.render_shared_dnsmasq("example.com")

    def test_the_name_is_read_from_the_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trust.env"
            self.assertEqual(localname.read_local_name(path), "pi.home.arpa")
            path.write_text("OTHER=1\nJITTERSIM_LOCAL_NAME=Lab.Home.Arpa\n")
            self.assertEqual(localname.read_local_name(path), "lab.home.arpa")
            path.write_text("JITTERSIM_LOCAL_NAME=jittersim.home.arpa\n")
            self.assertEqual(localname.read_local_name(path), "pi.home.arpa")

    def test_install_writes_the_default_name_and_the_shared_resolver_record_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, dns = Path(directory) / "etc", Path(directory) / "dnsmasq"
            config.mkdir()

            name, changed = localname.install_config(config, dns)

            self.assertEqual((name, changed), ("pi.home.arpa", True))
            self.assertEqual((config / "trust.env").read_text(), "JITTERSIM_LOCAL_NAME=pi.home.arpa\n")
            record = dns / "jittersim-dashboard-name.conf"
            self.assertEqual(record.read_text(), "address=/pi.home.arpa/192.168.50.1\n")
            self.assertEqual(localname.install_config(config, dns), ("pi.home.arpa", False))

            (config / "trust.env").write_text("JITTERSIM_LOCAL_NAME=dash.home.arpa\n")
            self.assertEqual(localname.install_config(config, dns), ("dash.home.arpa", True))
            self.assertEqual(record.read_text(), "address=/dash.home.arpa/192.168.50.1\n")

            # An invalid choice falls back to the default instead of publishing it.
            (config / "trust.env").write_text("JITTERSIM_LOCAL_NAME=jittersim.home.arpa\n")
            self.assertEqual(localname.install_config(config, dns)[0], "pi.home.arpa")

    def test_installer_publishes_the_name_to_the_shared_resolver_only(self) -> None:
        installer = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()

        self.assertIn("-m jittersim.localname install", installer)
        # mDNS stays closed to the test Wi-Fi: no new open port for it.
        firewall = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-control-firewall.nft").read_text()
        self.assertIn("deny mDNS queries from test Wi-Fi", firewall)
        self.assertIn("deny mDNS replies from test Wi-Fi", firewall)


if __name__ == "__main__":
    unittest.main()
