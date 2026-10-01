import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class DeploymentFirewallTests(unittest.TestCase):
    def test_test_wifi_cannot_forward_to_private_upstream_networks(self) -> None:
        firewall = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-control-firewall.nft"
        ).read_text()

        self.assertIn('iifname "@TEST_INTERFACE@" oifname "@UPLINK_INTERFACE@"', firewall)
        self.assertIn("10.0.0.0/8", firewall)
        self.assertIn("172.16.0.0/12", firewall)
        self.assertIn("192.168.0.0/16", firewall)
        self.assertIn('comment "deny test Wi-Fi access to private upstream networks"', firewall)
        self.assertIn("fc00::/7", firewall)
        self.assertIn("fe80::/10", firewall)

    def test_pi_service_uses_tls_and_installer_protects_the_private_key(self) -> None:
        service = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-dashboard.service"
        ).read_text()
        installer = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()

        self.assertIn("--tls-cert /etc/jittersim/dashboard.crt", service)
        self.assertIn("--tls-key /etc/jittersim/dashboard.key", service)
        self.assertIn('chown root:jittersim "$CONFIG_DIR/dashboard.key"', installer)
        self.assertIn('chmod 0640 "$CONFIG_DIR/dashboard.key"', installer)
        self.assertIn("subjectAltName=DNS:jittersim.local,IP:$dashboard_ip", installer)
        self.assertIn('openssl x509 -in "$CONFIG_DIR/dashboard.crt" -noout -text', installer)

    def test_dns_watcher_runs_unprivileged_with_only_packet_capture(self) -> None:
        service = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-service-watch.service"
        ).read_text()
        installer = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()

        self.assertIn("User=jittersim", service)
        self.assertIn("AmbientCapabilities=CAP_NET_RAW", service)
        self.assertIn("CapabilityBoundingSet=CAP_NET_RAW", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("RestrictAddressFamilies=AF_PACKET AF_UNIX", service)
        self.assertIn("--interface ${JITTERSIM_TEST_INTERFACE}", service)
        self.assertIn("After=NetworkManager.service jittersim-helper.service", service)
        self.assertIn("jittersim-service-watch.service", installer)
        self.assertIn("systemctl restart jittersim-service-watch.service", installer)
        self.assertIn("jittersim.networking.dns_watch", installer)

    def test_the_watcher_is_installed_as_a_command(self) -> None:
        project = (REPOSITORY_ROOT / "pyproject.toml").read_text()

        self.assertIn('jittersim-service-watch = "jittersim.networking.dns_watch:main"', project)


if __name__ == "__main__":
    unittest.main()
