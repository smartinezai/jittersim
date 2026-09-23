from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class DeploymentFirewallTests(unittest.TestCase):
    def test_test_wifi_cannot_forward_to_private_upstream_networks(self) -> None:
        firewall = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-control-firewall.nft"
        ).read_text()

        self.assertIn('iifname "wlan0" oifname "eth0"', firewall)
        self.assertIn("10.0.0.0/8", firewall)
        self.assertIn("172.16.0.0/12", firewall)
        self.assertIn("192.168.0.0/16", firewall)
        self.assertIn('comment "deny test Wi-Fi access to private upstream networks"', firewall)
        self.assertIn("fc00::/7", firewall)
        self.assertIn("fe80::/10", firewall)


if __name__ == "__main__":
    unittest.main()
