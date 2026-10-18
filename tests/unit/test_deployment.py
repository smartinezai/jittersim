import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jittersim.networking import wifi_follow

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

    def test_test_wifi_cannot_forward_to_link_local_or_carrier_grade_nat(self) -> None:
        # Some ISP modems and their management pages sit in these ranges.
        firewall = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-control-firewall.nft"
        ).read_text()
        forward = firewall[firewall.index("chain forward"):]
        private = forward[:forward.index('comment "deny test Wi-Fi access to private upstream networks"')]
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                        "169.254.0.0/16", "100.64.0.0/10"):
            self.assertIn(network, private)

    def test_nothing_with_a_test_wifi_address_or_for_another_lan_host_leaves_the_uplink(self) -> None:
        # The behaviour is checked by capture in tests/kernel/test_router_wire_guard.py.
        firewall = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-control-firewall.nft"
        ).read_text()
        postrouting = firewall[firewall.index("chain postrouting"):]
        self.assertIn("type filter hook postrouting priority 300;", postrouting)
        self.assertIn('oifname "@UPLINK_INTERFACE@" ip saddr 192.168.50.0/24 '
                      "fib saddr . oif type != local counter drop", postrouting)
        # One transaction replaces the table, so a reload never leaves a gap.
        self.assertIn("delete table inet jittersim_control",
                      firewall.split("table inet jittersim_control {")[0])
        forward = firewall[firewall.index("chain forward"):firewall.index("chain postrouting")]
        self.assertIn('iifname "@UPLINK_INTERFACE@" oifname "@UPLINK_INTERFACE@" counter drop',
                      forward)

    def test_the_firewall_unit_sets_arp_and_redirects_and_starts_before_networkmanager(self) -> None:
        unit = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-control-firewall.service"
        ).read_text()
        for setting in ("conf/all/arp_ignore", "conf/all/arp_announce", "conf/*/send_redirects"):
            self.assertIn(setting, unit)
        before = [line for line in unit.splitlines() if line.startswith("Before=")]
        self.assertIn("NetworkManager.service", before[0].split("=", 1)[1].split())

    def test_pi_service_uses_tls_and_installer_protects_the_private_key(self) -> None:
        service = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-dashboard.service"
        ).read_text()
        installer = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()

        self.assertIn("--tls-cert /etc/jittersim/dashboard.crt", service)
        self.assertIn("--tls-key /etc/jittersim/dashboard.key", service)
        # The helper sets root:jittersim 0640 on the key (tests/unit/test_local_ca.py).
        self.assertIn("-m jittersim.localca ensure", installer)
        self.assertIn("--group jittersim", installer)

    def test_the_helper_starts_after_boot_restoration_has_finished(self) -> None:
        # O5: the helper's startup decision is applied last and Panic is only
        # served once access-boot can no longer restore rules behind it.
        def ordered_after(unit):
            text = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / unit).read_text()
            return {name for line in text.splitlines() if line.startswith("After=")
                    for name in line[len("After="):].split()}

        self.assertIn("jittersim-access-boot.service", ordered_after("jittersim-helper.service"))
        self.assertNotIn("jittersim-helper.service", ordered_after("jittersim-access-boot.service"))

    def test_a_hung_boot_restoration_cannot_keep_the_helper_and_panic_from_starting(self) -> None:
        # The helper is ordered after the one-shot access-boot, whose start timeout is
        # unlimited by default. A bounded timeout lets systemd stop it and start the
        # helper, which then applies its own startup decision. After= is only ordering
        # (no Requires=), so a failed or timed-out access-boot does not block the helper.
        unit = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-access-boot.service").read_text()
        timeouts = [line.split("=", 1)[1] for line in unit.splitlines() if line.startswith("TimeoutStartSec=")]
        self.assertEqual(len(timeouts), 1)
        self.assertTrue(timeouts[0].isdigit() and 0 < int(timeouts[0]) <= 120, timeouts)
        helper = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-helper.service").read_text()
        self.assertNotIn("Requires=jittersim-access-boot", helper)
        self.assertNotIn("BindsTo=jittersim-access-boot", helper)

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
    def test_the_certificate_comes_from_a_kept_local_authority(self) -> None:
        installer = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()

        # Names, addresses and reissue rules live in a tested helper, not in shell.
        self.assertIn('dashboard_host="$(hostname -s).local"', installer)
        self.assertIn("-m jittersim.localca ensure", installer)
        self.assertIn('--host-name "$dashboard_host"', installer)
        self.assertIn('--ethernet-ip "$dashboard_ip"', installer)
        self.assertNotIn("openssl req -x509", installer)
        self.assertNotIn("rsa:3072", installer)
        # Modes and owners are set by the tested helper: only root reads the
        # authority key, and the dashboard group reads the rest.
        self.assertIn("--group jittersim", installer)
        # Nothing in the installer deletes the authority.
        self.assertNotIn('rm -rf "$CONFIG_DIR', installer)
        self.assertNotIn('rm -f "$CONFIG_DIR/ca', installer)

    def test_the_dashboard_serves_the_neutral_certificate_to_the_test_wifi(self) -> None:
        service = (
            REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-dashboard.service"
        ).read_text()

        self.assertIn("--tls-cert-wifi /etc/jittersim/dashboard-wifi.crt", service)
        self.assertIn("--trust-ca /etc/jittersim/ca/ca.crt", service)


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
        self.assertIn("create-ap-profile.py", self.installer)
        self.assertIn("ipv4.method shared", self.installer)
        # The preflight refuses to take over a Wi-Fi connection in use, such as
        # one carrying this SSH session, before anything changes.
        preflight = self.installer.index("preflight.py")
        self.assertLess(preflight, self.installer.index("apt-get update"))
        self.assertLess(preflight, self.installer.index('create --interface "$JITTERSIM_TEST_INTERFACE"'))

    def test_the_test_wifi_has_a_neutral_name_and_isolates_its_clients(self) -> None:
        self.assertIn("JITTERSIM_AP_SSID=TestNet", self.installer)
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
        self.assertIn("RemainAfterExit=yes", self.unit)
        # The one safety check that stays: no test Wi-Fi without the management firewall.
        # It still fails the unit when the table is missing (no leading "-"). The listing
        # itself goes to /dev/null: see test_boot_noise.py for what it does at run time.
        pre = [line for line in self.unit.splitlines() if line.startswith("ExecStartPre=")
               and "nft list table inet jittersim_control" in line]
        self.assertEqual(len(pre), 1)
        self.assertIn("/usr/sbin/nft list table inet jittersim_control >/dev/null", pre[0])
        self.assertFalse(pre[0].startswith("ExecStartPre=-"))

    def test_the_start_retries_a_bounded_number_of_times_and_never_takes_the_wifi_down(self):
        # NetworkManager counts as started before wlan0 may be ready. Autoconnect is off
        # so a single failed `nmcli connection up` would leave the Wi-Fi down until reboot.
        start = [line for line in self.unit.splitlines() if line.startswith("ExecStart=")]
        self.assertEqual(len(start), 1)
        command = start[0]
        self.assertIn("for try in 1 2 3 4 5 6 7 8 9 10; do", command)
        self.assertIn("sleep 3", command)
        # An active connection counts as success and is never brought up again.
        self.assertLess(
            command.index("GENERAL.STATE connection show jittersim-ap"),
            command.index("connection up jittersim-ap"),
        )
        self.assertIn("= activated ] && return 0", command)
        self.assertIn("/usr/bin/nmcli --wait 10 connection up jittersim-ap && return 0", command)
        self.assertIn("did not start after 10 tries", command)
        self.assertTrue(command.rstrip("'").endswith("exit 1"))
        # Every step at its own limit and added up (the comment in the unit lists them):
        # nft check, boot step (lock wait, three 3 s reads, password check, look, rename and
        # rename back), uplink check, two rounds of ten tries (router copy, then the previous
        # Wi-Fi) with the fallback between them, and the lock wait of boot-done.
        boot_step = 45 + 3 + 3 + 15 + wifi_follow.SCAN_BUDGET + 9 + 9
        worst_case = 1 + boot_step + 5 + 2 * 10 * (10 + 3) + (45 + 9) + 45
        self.assertEqual(worst_case, 469)
        self.assertIn("TimeoutStartSec=480", self.unit)
        self.assertLess(worst_case, 480)

    def test_boot_returns_to_the_router_copy_and_never_blocks_the_start(self):
        follow = "/opt/jittersim/.venv/bin/python -I -m jittersim.networking.wifi_follow"
        # A failing step is ignored ("-"), so the Wi-Fi always starts with some profile.
        # After it, the read-only uplink check (only its exit status 3 fails the unit).
        self.assertEqual(self.directives(self.unit, "ExecStartPre")[1:], [
            ["-" + follow.split()[0], *follow.split()[1:], "boot"],
            ["/bin/sh", "-c", "'%s" % follow.split()[0], *follow.split()[1:], "uplink-check;",
             "[", '"$$?"', "-ne", "3", "]'"]])
        self.assertEqual(self.directives(self.unit, "ExecStartPost"), [
            ["-" + follow.split()[0], *follow.split()[1:], "boot-done"]])
        self.assertIn("EnvironmentFile=-/etc/jittersim/runtime.env", self.unit)
        command = [line for line in self.unit.splitlines() if line.startswith("ExecStart=")][0]
        # The previous Wi-Fi is tried only after the router copy failed ten times.
        self.assertIn(follow + ' boot-fallback; [ "$$?" -ne 2 ] && start_wifi && exit 0', command)
        self.assertLess(command.index("start_wifi && exit 0"), command.index("boot-fallback"))

    def test_a_router_copy_that_will_not_start_falls_back_to_the_previous_wifi(self):
        command = [line for line in self.unit.splitlines() if line.startswith("ExecStart=")][0]
        script = command.split("-c '", 1)[1].rsplit("'", 1)[0].replace("$$", "$")
        # 0: the old name is back. 1: the fallback failed but a start is still tried.
        # 2: this boot renamed nothing, so there is no second round.
        for fallback_exit, expected in ((0, 0), (1, 0), (2, 1)):
            with self.subTest(fallback_exit=fallback_exit), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "calls"
                fake = Path(directory) / "nmcli"
                # Every start fails until the fallback ran.
                fake.write_text(
                    "#!/bin/sh\necho \"nmcli $*\" >> %s\n"
                    "case \"$*\" in *GENERAL.STATE*) echo activating;; "
                    "*'connection up'*) grep -q fallback %s;; esac\n" % (log, log)
                )
                python = Path(directory) / "python"
                python.write_text("#!/bin/sh\necho \"fallback $*\" >> %s\nexit %d\n" % (log, fallback_exit))
                for path in (fake, python):
                    path.chmod(0o755)
                result = subprocess.run(
                    ["sh", "-c", script.replace("/usr/bin/nmcli", str(fake))
                     .replace("/opt/jittersim/.venv/bin/python", str(python))
                     .replace("sleep 3", "sleep 0")],
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                calls = log.read_text()
                self.assertEqual(calls.count("connection up"), 11 if expected == 0 else 10)
                self.assertEqual(calls.count("boot-fallback"), 1)
                self.assertNotIn("down", calls)

    def test_the_retry_loop_runs_and_stops_at_the_first_success(self):
        command = [line for line in self.unit.splitlines() if line.startswith("ExecStart=")][0]
        script = command.split("-c '", 1)[1].rsplit("'", 1)[0].replace("$$", "$")
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "nmcli"
            log = Path(directory) / "calls"
            # The third `connection up` succeeds.
            fake.write_text(
                "#!/bin/sh\necho \"$*\" >> %s\n"
                "case \"$*\" in *GENERAL.STATE*) echo activating;; "
                "*'connection up'*) [ $(grep -c 'connection up' %s) -ge 3 ];; esac\n" % (log, log)
            )
            fake.chmod(0o755)
            result = subprocess.run(
                ["sh", "-c", script.replace("/usr/bin/nmcli", str(fake)).replace("sleep 3", "sleep 0")],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().count("connection up"), 3)
            self.assertNotIn("down", log.read_text())

    def test_the_installer_enables_the_unit_and_keeps_boot_activation_with_it(self):
        self.assertIn(
            "systemctl enable jittersim-access-boot.service jittersim-ap.service", self.installer
        )
        # NetworkManager must not raise the access point before the access rules.
        modify = self.installer[self.installer.index("nmcli connection modify jittersim-ap \\") :]
        modify = modify[: modify.index("ap_settings_after")]
        self.assertIn("connection.autoconnect no", modify)
        self.assertNotIn("connection.autoconnect yes", self.installer)
        self.assertIn("create-ap-profile.py", self.installer)

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


class StaleAccessStateTests(unittest.TestCase):
    """An upgrade never restores main-era access state and never aborts on a restore.

    The owner's Pi once ran main's access recovery. A release without access rules
    (the Pi line before the merge) never wrote /var/lib/jittersim-access, so state
    found there on such an upgrade is stale and nothing in the database knows it.
    """

    INSTALLER = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()

    def function(self, name):
        match = re.search(r"^" + name + r"\(\) \{\n.*?^\}\n", self.INSTALLER, re.DOTALL | re.MULTILINE)
        self.assertIsNotNone(match, name)
        return match.group(0)

    def run_shell(self, script):
        return subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=20)

    def archive(self, root, previous):
        body = self.function("archive_stale_access_state").replace(
            "/var/lib/jittersim-access", str(root / "jittersim-access")
        )
        # Root owns the copy on the Pi. The test only records the call.
        script = "set -eu\nchown() { echo chown \"$@\"; }\nprevious_access_release=%s\n%s\narchive_stale_access_state\n" % (previous, body)
        return self.run_shell(script)

    def test_state_from_a_release_without_access_rules_is_moved_aside_for_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "jittersim-access").mkdir()
            (root / "jittersim-access" / "policy.json").write_text("{}")

            result = self.archive(root, "no")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "jittersim-access").exists())
            backups = list(root.glob("jittersim-access-before-*"))
            self.assertEqual(len(backups), 1)
            self.assertTrue((backups[0] / "policy.json").exists())
            self.assertEqual(oct(backups[0].stat().st_mode & 0o777), "0o700")
            self.assertIn("chown -R root:root", result.stdout)
            self.assertIn("not restored", result.stderr)

    def test_state_of_a_release_with_access_rules_and_an_empty_folder_stay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "jittersim-access").mkdir()
            self.assertEqual(self.archive(root, "no").returncode, 0)
            (root / "jittersim-access" / "policy.json").write_text("{}")
            self.assertEqual(self.archive(root, "yes").returncode, 0)
            self.assertTrue((root / "jittersim-access" / "policy.json").exists())
            self.assertEqual(list(root.glob("jittersim-access-before-*")), [])

    def test_a_failed_restore_warns_and_the_installer_carries_on(self):
        body = self.function("restore_access_rules")
        for code, warned in ((1, True), (0, False)):
            with self.subTest(code=code):
                result = self.run_shell(
                    "set -eu\nsystemctl() { return %d; }\n%s\nrestore_access_rules\necho carried on\n"
                    % (code, body)
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("carried on", result.stdout)
                self.assertEqual("sudo jittersim normal-access" in result.stderr, warned)

    def test_the_installer_reads_the_old_release_first_and_restores_only_through_the_guard(self):
        text = self.INSTALLER
        check = text.index('[ -x "$INSTALL_DIR/.venv/bin/jittersim-access-restore" ]')
        self.assertLess(check, text.index("replace-source.py\" \"$SOURCE_DIR\" \"$INSTALL_DIR\""))
        self.assertLess(check, text.index('"$INSTALL_DIR/.venv/bin/pip" install'))
        archive = text.index("\narchive_stale_access_state\n")
        self.assertLess(archive, text.index("install -d -m 0750 -o root -g jittersim /var/lib/jittersim-access"))
        self.assertLess(archive, text.index("\nrestore_access_rules\n"))
        # Every restart of the restore runs inside the warning so set -e never aborts.
        bare = [
            line for line in text.splitlines()
            if "systemctl restart jittersim-access-boot.service" in line and "if !" not in line
        ]
        self.assertEqual(bare, [])


def test_the_filtered_dns_unit_lets_unbound_list_the_network_interfaces():
    # Unbound lists interfaces through a netlink socket. Without it the service
    # stops with "getifaddrs: Address family not supported by protocol" on the Pi.
    unit = (REPOSITORY_ROOT / "deploy" / "raspberry-pi" / "jittersim-dns.service").read_text()
    families = [
        line.split("=", 1)[1].split()
        for line in unit.splitlines()
        if line.startswith("RestrictAddressFamilies=")
    ]
    assert families, "the unit must restrict address families"
    assert "AF_NETLINK" in families[0]
    assert "AF_INET" in families[0] and "AF_UNIX" in families[0]
    assert "AF_PACKET" not in families[0]
