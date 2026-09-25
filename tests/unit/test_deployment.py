import shutil
import subprocess
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


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.installer_path = REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh"
        self.installer = self.installer_path.read_text()

    def test_installer_is_valid_shell(self) -> None:
        shell = shutil.which("sh")
        if shell is None:
            self.skipTest("a POSIX shell is required to check the installer")
        result = subprocess.run(
            [shell, "-n", str(self.installer_path)], capture_output=True, text=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_code_run_by_the_root_helper_stays_owned_by_root(self) -> None:
        # jittersim-helper runs as root from this directory.
        self.assertIn('chown -R root:root "$INSTALL_DIR"', self.installer)
        self.assertIn('chmod -R go-w "$INSTALL_DIR"', self.installer)
        self.assertNotIn('chown -R jittersim:jittersim "$INSTALL_DIR"', self.installer)

    def test_installer_refreshes_packages_and_reloads_the_firewall(self) -> None:
        self.assertLess(
            self.installer.index("apt-get update"), self.installer.index("apt-get install")
        )
        self.assertIn("systemctl restart jittersim-control-firewall.service", self.installer)


if __name__ == "__main__":
    unittest.main()
