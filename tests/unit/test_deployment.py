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
        # It also covers the name the Avahi publisher announces and the test Wi-Fi
        # gateway that browsers on the test Wi-Fi open.
        self.assertIn(
            "subjectAltName=DNS:$dashboard_host,DNS:jittersim.local,IP:$dashboard_ip,"
            "IP:192.168.50.1",
            installer,
        )
        # A certificate for another name or address is made again.
        self.assertIn('grep -Fq "DNS:$dashboard_host"', installer)
        self.assertIn('grep -Fq "DNS:jittersim.local"', installer)
        self.assertIn('grep -Fq "IP Address:$dashboard_ip"', installer)
        self.assertIn('grep -Fq "IP Address:192.168.50.1"', installer)


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


class AccessPointStartTests(unittest.TestCase):
    """The test Wi-Fi starts at boot and an install or update never restarts it."""

    PI = REPOSITORY_ROOT / "deploy" / "raspberry-pi"

    def setUp(self) -> None:
        self.installer = (self.PI / "install.sh").read_text()
        # Comments explain the rules and may name the directives they forbid.
        self.unit = "\n".join(
            line
            for line in (self.PI / "jittersim-ap.service").read_text().splitlines()
            if not line.startswith("#")
        )

    def directives(self, text, name):
        return [
            line.split("=", 1)[1].split()
            for line in text.splitlines()
            if line.startswith(name + "=")
        ]

    def test_the_unit_only_orders_itself_after_the_firewall_and_the_access_restore(self):
        wants = [word for line in self.directives(self.unit, "Wants") for word in line]
        after = [word for line in self.directives(self.unit, "After") for word in line]
        for needed in ("jittersim-access-boot.service", "jittersim-control-firewall.service"):
            self.assertIn(needed, wants)
            self.assertIn(needed, after)
        self.assertIn("NetworkManager.service", after)
        access_boot = (self.PI / "jittersim-access-boot.service").read_text()
        self.assertEqual(self.directives(access_boot, "Before"), [["jittersim-ap.service"]])

    def test_nothing_makes_a_restart_of_another_unit_restart_or_stop_the_wifi(self):
        # Requires= and BindsTo= pass a restart on to the unit that needs them and
        # a stop action would then take the connection down for every client.
        for name in ("Requires", "BindsTo", "PartOf", "Requisite", "Upholds"):
            self.assertEqual(self.directives(self.unit, name), [], name)
        self.assertNotIn("ExecStop", self.unit)
        self.assertNotIn("connection down", self.unit)
        for path in sorted(self.PI.glob("*.service")):
            text = path.read_text()
            for name in ("Requires", "BindsTo", "PartOf", "Requisite", "Upholds"):
                for line in self.directives(text, name):
                    self.assertNotIn("jittersim-ap.service", line, f"{path.name} {name}")
        self.assertIn("ExecStart=/usr/bin/nmcli connection up jittersim-ap", self.unit)
        self.assertIn("RemainAfterExit=yes", self.unit)
        # The one safety check that stays: no test Wi-Fi without the management firewall.
        self.assertIn(
            "ExecStartPre=/usr/sbin/nft list table inet jittersim_control", self.unit
        )

    def test_the_installer_enables_the_unit_and_keeps_boot_activation_with_it(self):
        self.assertIn(
            "systemctl enable jittersim-access-boot.service jittersim-ap.service", self.installer
        )
        # NetworkManager must not raise the access point before the access rules.
        modify = self.installer[self.installer.index("nmcli connection modify jittersim-ap \\") :]
        modify = modify[: modify.index("ap_settings_after")]
        self.assertIn("connection.autoconnect no", modify)
        self.assertNotIn("connection.autoconnect yes", self.installer)
        self.assertIn("autoconnect no", self.installer.split("nmcli connection add", 1)[1][:200])

    def test_an_installer_run_never_restarts_stops_or_drops_the_running_wifi(self):
        for forbidden in (
            "systemctl restart jittersim-ap",
            "systemctl stop jittersim-ap",
            "systemctl try-restart jittersim-ap",
            "nmcli connection down",
            "nmcli connection reload",
        ):
            self.assertNotIn(forbidden, self.installer)
        # The unit is started only when no access point is active yet.
        start = self.installer.index("systemctl start jittersim-ap.service")
        branch = self.installer[: start].rindex("if [ \"$ap_active_connection\" = jittersim-ap ]")
        self.assertIn("else", self.installer[branch:start])
        self.assertNotIn("fi", self.installer[branch:start].split("else", 1)[1].split("\n", 1)[1])


if __name__ == "__main__":
    unittest.main()
