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
    def test_the_certificate_is_named_after_the_pi(self) -> None:
        installer = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()

        # The browser shows the certificate's name to anyone who opens the address.
        self.assertIn('dashboard_host="$(hostname -s).local"', installer)
        self.assertIn('-subj "/CN=$dashboard_host"', installer)
        # It also covers the name the Avahi publisher announces.
        self.assertIn(
            "subjectAltName=DNS:$dashboard_host,DNS:jittersim.local,IP:$dashboard_ip", installer
        )
        # A certificate for another name or address is made again.
        self.assertIn('grep -Fq "DNS:$dashboard_host"', installer)
        self.assertIn('grep -Fq "DNS:jittersim.local"', installer)
        self.assertIn('grep -Fq "IP Address:$dashboard_ip"', installer)


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

    def test_installer_puts_the_emergency_command_on_the_path(self) -> None:
        self.assertIn(
            'ln -sf "$INSTALL_DIR/.venv/bin/jittersim" /usr/local/bin/jittersim',
            self.installer,
        )

    def test_installer_checks_the_pi_before_creating_the_test_wifi(self) -> None:
        # The helper manages a NetworkManager connection with this name.
        self.assertIn("con-name jittersim-ap", self.installer)
        self.assertIn("ipv4.method shared", self.installer)
        # The preflight refuses to take over a Wi-Fi connection in use, such as
        # one carrying this SSH session, before anything changes.
        preflight = self.installer.index("preflight.py")
        self.assertLess(preflight, self.installer.index("apt-get update"))
        self.assertLess(preflight, self.installer.index("nmcli connection add"))

    def test_the_test_wifi_has_a_neutral_name_and_isolates_its_clients(self) -> None:
        self.assertIn("JITTERSIM_AP_SSID=JitterSim", self.installer)
        self.assertNotIn("JitterSim-Test", self.installer)
        # On unless ap.env turns it off, so an update keeps the owner's choice.
        self.assertIn('ap_isolation="${JITTERSIM_AP_ISOLATION:-yes}"', self.installer)
        self.assertIn('802-11-wireless.ap-isolation "$ap_isolation"', self.installer)
        # A changed setting is noticed like any other AP setting, and the
        # installer says how to restart the running Wi-Fi to use it.
        self.assertEqual(self.installer.count("802-11-wireless.ap-isolation"), 3)
        self.assertIn("sudo nmcli connection up jittersim-ap", self.installer)

    def test_installer_refreshes_packages_and_reloads_the_firewall(self) -> None:
        self.assertLess(
            self.installer.index("apt-get update"), self.installer.index("apt-get install")
        )
        self.assertIn("systemctl restart jittersim-control-firewall.service", self.installer)


if __name__ == "__main__":
    unittest.main()
