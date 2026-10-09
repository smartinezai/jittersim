"""Reproduce the Pi-only cutover in a namespace lab and measure each failure mode.

Run with tests/kernel/run-outage-lab.sh (one privileged container, about 15 minutes).
Every scenario records numbers in RESULTS, written as JSON to
$JITTERSIM_LAB_RESULTS (default /tmp/outage-lab-results.json) and printed as it runs.

"Browsing" everywhere means: the client resolves www.example.org with the resolver
its DHCP lease gave it AND fetches the page from the fake internet behind the router
(Lab.browse). Timing assertions are deliberately loose: the numbers are the result,
the assertions only pin the qualitative behaviour.
"""

import json
import os
import re
import signal
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from .outage_lab import (
    CLIENT,
    ENABLED,
    HOUSE,
    INET,
    OTHER_CLIENT,
    PI,
    PI_ETH0_MAC,
    ROUTER,
    WEB_ADDRESS,
    Lab,
    audit_uplink_frames,
    classify_frames,
    in_namespace,
    nstat,
    run_in,
)

if ENABLED:
    from ipaddress import IPv4Address

    from jittersim.config.models import (
        Device,
        Impairment,
        MacPolicy,
        Profile,
        SiteBlock,
    )
    from jittersim.engine.access import AccessClient, AccessPlan
    from jittersim.engine.codec import plan_to_payload
    from jittersim.engine.compiler import compile_plan
    from jittersim.engine.plan import GeneralProfileRule, ServiceListRule
    from jittersim.networking.access import AccessManager
    from jittersim.networking.applier import NetworkApplier
    from jittersim.networking.commands import Command
    from jittersim.networking.helper import HelperService
    from tests.kernel.lab import CLIENT_MAC, OTHER_CLIENT_MAC, TOPOLOGY, NamespaceExecutor
    from tests.kernel.test_access import DnsServiceExecutor
    from tests.wifi_support import WifiExecutor

RESULTS = {}


def record(scenario, **values):
    RESULTS.setdefault(scenario, {}).update(values)
    print(f"\n[outage-lab] {scenario}: {json.dumps(values, default=str, sort_keys=True)}",
          flush=True)


def tearDownModule():
    if not ENABLED or not RESULTS:
        return
    path = Path(os.environ.get("JITTERSIM_LAB_RESULTS", "/tmp/outage-lab-results.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text())
        except ValueError:
            previous = {}
    previous.update(RESULTS)
    path.write_text(json.dumps(previous, indent=2, sort_keys=True, default=str) + "\n")
    print(f"\n[outage-lab] results written to {path}", flush=True)


def dhcp_events(path, limit=24):
    """The DHCP message lines of a dnsmasq or dhclient log, oldest first."""
    try:
        text = Path(path).read_text(errors="replace")
    except FileNotFoundError:
        return []
    pattern = re.compile(r"DHCP(?:DISCOVER|OFFER|REQUEST|ACK|NAK|DECLINE|RELEASE|INFORM)\S*.*")
    lines = []
    for line in text.splitlines():
        match = pattern.search(line)
        if match and "requested options" not in line:
            stamp = re.match(r"\w{3}\s+\d+ (\d\d:\d\d:\d\d)", line)
            lines.append(((stamp.group(1) + " ") if stamp else "") + match.group(0).strip())
    return lines[-limit:]


if ENABLED:
    class LabDnsExecutor(DnsServiceExecutor):
        """systemd stand-in for jittersim-dns (real unbound) with a start-up delay.

        A Pi 3B+ needs noticeably longer than this container to start Unbound, so a
        scenario can add a delay before the service reports active.
        """

        def __init__(self, directory):
            super().__init__(directory)
            self.restart_delay = 0.0
            self.restarts = 0

        def run(self, command):
            if command.executable == "systemctl" and command.arguments[0] == "restart":
                self.restarts += 1
                if self.restart_delay:
                    time.sleep(self.restart_delay)
            return super().run(command)

        def systemd_restart(self):
            """What systemd's Restart=on-failure does after a crash."""
            return self.run(Command(executable="systemctl",
                                    arguments=("restart", "jittersim-dns.service"),
                                    purpose="lab: systemd restarts the filtered resolver"))

    class LabWifiExecutor(NamespaceExecutor):
        """NetworkManager stand-in: nmcli state is faked, its effects on wlan0 are real.

        `nmcli connection down` removes the AP address, NM's dnsmasq and NM's shared
        nft table; `up` puts them back (Lab.start/stop_pi_access_point). Everything
        else runs for real in the Pi namespace.
        """

        def __init__(self, lab, events):
            super().__init__(PI)
            self.lab = lab
            self.events = events
            self.wifi = WifiExecutor()
            self.wifi.settings["802-11-wireless.ssid"] = "JitterSim-Recovery"

        def run(self, command):
            if command.executable not in ("nmcli", "wpa_cli"):
                return super().run(command)
            arguments = command.arguments
            operation = None
            if command.executable == "nmcli" and "connection" in arguments:
                position = arguments.index("connection")
                operation = arguments[position + 1] if position + 1 < len(arguments) else None
            if operation == "down":
                self.lab.stop_pi_access_point()
            elif operation == "up":
                run("ip", "-n", self.lab_air(), "link", "set", "ap", "up")
                self.lab.start_pi_access_point()
            if operation in ("down", "up", "modify", "clone", "delete", "edit"):
                self.events.append({"t": round(time.monotonic(), 2), "nmcli": operation,
                                    "pi": pi_snapshot()})
            return self.wifi.run(command)

        @staticmethod
        def lab_air():
            from .outage_lab import AIR
            return AIR


def run(*argv, check=True):
    return subprocess.run(argv, capture_output=True, text=True, check=check)


def pi_snapshot():
    """What the Pi's routing and firewall look like right now (compact)."""
    routes = run("ip", "-n", PI, "-4", "route", check=False).stdout.strip().splitlines()
    wlan = run("ip", "-n", PI, "-4", "-o", "addr", "show", "dev", "wlan0", check=False).stdout
    tables = run_in(PI, "nft", "list", "tables", check=False).stdout.split("\n")
    return {
        "routes": [line.split(" proto")[0] for line in routes],
        "wlan0_address": re.findall(r"inet (\S+)", wlan),
        "nft_tables": sorted(line.replace("table ", "") for line in tables if line),
    }


@unittest.skipUnless(ENABLED, "needs the privileged outage-lab container (run-outage-lab.sh)")
class OutageLabCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="outage-lab-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o755)

    def make_lab(self, **options):
        self.lab = Lab(self.directory / "lab", **options)
        self.addCleanup(self.keep_logs)
        self.addCleanup(self.lab.stop)
        self.lab.start()
        return self.lab

    def keep_logs(self):
        target = os.environ.get("JITTERSIM_LAB_LOGS")
        if not target:
            return
        destination = Path(target) / self.id().rsplit(".", 1)[-1]
        destination.mkdir(parents=True, exist_ok=True)
        for folder in (self.directory / "lab", self.directory / "access"):
            for item in folder.glob("*"):
                if item.is_file() and item.suffix in (".log", ".leases", ".pcap", ".json"):
                    (destination / item.name).write_bytes(item.read_bytes())

    def join_pi(self, *namespaces, fresh=True):
        for namespace in namespaces:
            self.lab.associate(namespace, "pi")
            lease = self.lab.dhcp(namespace, fresh=fresh)
            self.assertTrue(lease["address"] and lease["address"].startswith("192.168.50."),
                            (namespace, lease))

    def access_manager(self, *, hostapd=None):
        directory = self.directory / "access"
        directory.mkdir(exist_ok=True)
        directory.chmod(0o755)
        self.executor = LabDnsExecutor(directory)
        self.addCleanup(self.executor.log.close)
        self.addCleanup(self.executor.stop)
        self.manager = AccessManager(
            executor=self.executor, topology=TOPOLOGY, directory=directory,
            runtime_path=directory / "runtime.json",
            hostapd_path=hostapd or (self.directory / "no-hostapd"),
        )
        return self.manager

    def helper(self, *, executor=None, **options):
        return HelperService(
            NetworkApplier(executor=executor or self.executor, topology=TOPOLOGY),
            active_plan_path=self.directory / "access" / "active-plan.json",
            access_manager=self.manager,
            **options,
        )

    def child_plan(self, address, *, extra_blocks=()):
        """Household rules: one device may not resolve video.example.org."""
        blocks = (SiteBlock("video", "Video", ("video.example.org",), device_id="child"),)
        return AccessPlan(
            blocks=blocks + tuple(extra_blocks),
            clients=(AccessClient("child", address, CLIENT_MAC, ("video",)),),
        )

    def dig(self, namespace, name):
        output = run_in(namespace, "dig", "+time=1", "+tries=1", name, check=False).stdout
        status = re.search(r"status: (\w+)", output)
        return status.group(1) if status else "no-answer"


class CleanPiOnlyTests(OutageLabCase):
    def test_1_clean_pi_only_mode_browses_and_router_faults_are_visible(self):
        lab = self.make_lab()
        lab.load_control_firewall()
        lab.associate(CLIENT, "pi")
        joined = time.monotonic()
        lease = lab.dhcp(CLIENT, fresh=True)
        seconds = lab.wait_browse(CLIENT, 15, since=joined)
        record("1_clean_pi_only", lease=lease, gateway=lab.default_gateway(CLIENT),
               resolver=lab.resolver(CLIENT), seconds_from_join_to_browse=seconds,
               pi_uplink=lab.pi_eth0_address,
               pi_dhcp=dhcp_events(lab.directory / "pi-dnsmasq.log", 6))
        self.assertTrue(lease["address"].startswith("192.168.50."), lease)
        self.assertEqual(lab.resolver(CLIENT), "192.168.50.1")
        self.assertIsNotNone(seconds, "a fresh client could not browse through the Pi")
        # Fault switch: the router loses its internet side (cable fault). The Pi path
        # cannot tell this apart from a Pi fault, and recovery is immediate once
        # the router forwards again.
        lab.router_fault('oifname "wan0" drop')
        broken = lab.browse(CLIENT)
        lab.router_fault()
        recovered = lab.wait_browse(CLIENT, 10)
        record("1_clean_pi_only", router_wan_fault_browse=broken,
               seconds_to_browse_after_router_fault_cleared=recovered)
        self.assertFalse(broken["ok"])
        self.assertIsNotNone(recovered)


class ImpairedUplinkTests(OutageLabCase):
    """H5: what an impaired upload profile puts on the router's cable.

    The `daily climb manual` profile (upload 6 % corruption, 7 % duplication,
    reordering; download 2 % / 3 %) was active during both red-PHONE failures.
    JitterSim shapes uploads with netem on eth0's egress, after NAT and after the
    Ethernet header is built, so netem's bit flips can land in the Ethernet and IP
    headers the router parses. This compares that layout, per scope, with
    impairing uploads where they enter the Pi (an ifb on wlan0's ingress).
    """

    SINK = (
        "import socket\n"
        "server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "server.bind(('198.51.100.80', 9000)); server.listen(8)\n"
        "while True:\n"
        "    connection, _ = server.accept(); connection.settimeout(30)\n"
        "    try:\n"
        "        while connection.recv(65536): pass\n"
        "    except OSError: pass\n"
        "    connection.close()\n"
    )
    UPLOAD = (
        "import os, socket\n"
        "s = socket.create_connection(('198.51.100.80', 9000), timeout=20)\n"
        "s.sendall(os.urandom(1 << 20)); s.shutdown(socket.SHUT_WR); s.close()\n"
    )

    def climb(self):
        return Profile(
            "climb", "Daily climb manual",
            upload=Impairment(delay_ms=20, reorder_percent=10, duplicate_percent=7,
                              corrupt_percent=6),
            download=Impairment(duplicate_percent=3, corrupt_percent=2),
        )

    def traffic(self):
        """The same mix every time: Pi-local frames and forwarded client traffic."""
        lab, report = self.lab, {}
        run("ip", "-n", PI, "neigh", "flush", "dev", "eth0")
        lab.dhcp(PI, "eth0")  # the Pi's DHCP client asks again (INIT-REBOOT)
        started = time.monotonic()
        download = run_in(CLIENT, "curl", "-s", "-o", "/dev/null", "--max-time", "40", "-w",
                          "%{http_code} %{size_download}", f"http://{WEB_ADDRESS}/big.bin",
                          check=False)
        report["download"] = {"result": download.stdout.strip(),
                              "seconds": round(time.monotonic() - started, 2)}
        started = time.monotonic()
        upload = run_in(CLIENT, "timeout", "40", "python", "-c", self.UPLOAD, check=False)
        report["upload_1MiB"] = {"ok": upload.returncode == 0,
                                 "seconds": round(time.monotonic() - started, 2)}
        answered = 0
        for index in range(30):
            answered += "198.51.100.80" in run_in(
                CLIENT, "dig", "+time=1", "+tries=1", "+short", "@198.51.100.53",
                f"f{index}.flood.example.org", check=False).stdout
            run_in(CLIENT, "dig", "+time=1", "+tries=1", "+short",
                   f"l{index}-{time.time_ns()}.flood.example.org", check=False)
        report["external_dns_answers_of_30"] = answered
        ping = run_in(CLIENT, "ping", "-n", "-q", "-c", "50", "-i", "0.05", WEB_ADDRESS,
                      check=False).stdout
        loss = re.search(r"(\d+)% packet loss", ping)
        report["ping_loss_percent"] = int(loss.group(1)) if loss else None
        return report

    def measure(self, name, layouts):
        lab = self.lab
        router_mac = re.search(r"link/ether (\S+)", run(
            "ip", "-n", ROUTER, "link", "show", "br-lan").stdout).group(1)
        before = nstat(ROUTER)
        with lab.capture(ROUTER, "r-pi", f"h5-{name}", inbound_only=True, snaplen=0) as capture:
            traffic = self.traffic()
        after = nstat(ROUTER)
        audit = audit_uplink_frames(capture["path"], pi_mac=PI_ETH0_MAC, router_mac=router_mac,
                                    pi_address=lab.pi_eth0_address)
        counters = {key: after.get(key, 0) - before.get(key, 0) for key in (
            "IpInHdrErrors", "IpInAddrErrors", "IpExtInCsumErrors", "IpInDiscards",
            "IpInUnknownProtos", "UdpInCsumErrors", "UdpInErrors", "TcpInCsumErrors",
            "IpInReceives") if after.get(key, 0) - before.get(key, 0)}
        learned = [line for line in run("bridge", "-n", ROUTER, "fdb", "show", "br", "br-lan",
                                        check=False).stdout.splitlines()
                   if "dev r-pi" in line and "permanent" not in line]
        layouts[name] = {"traffic": traffic, "frames": audit, "router_kernel_counters": counters,
                         "router_bridge_macs_learned_on_pi_port": len(learned),
                         "capture_seconds": capture["seconds"]}
        record("0_h5_impaired_uplink", **{name: layouts[name]})
        for line in learned:
            run("bridge", "-n", ROUTER, "fdb", "del", line.split()[0], "dev", "r-pi", "master",
                check=False)
        return layouts[name]

    def ifb_upload(self, profile, *, drop_invalid=False):
        """Upload impairment where frames enter the Pi: wlan0 ingress -> ifb -> netem."""
        commands = [
            ("ip", "link", "add", "ifb-up", "type", "ifb"),
            ("ip", "link", "set", "ifb-up", "up"),
            ("tc", "qdisc", "add", "dev", "wlan0", "handle", "ffff:", "ingress"),
            ("tc", "filter", "add", "dev", "wlan0", "parent", "ffff:", "protocol", "all",
             "flower", "src_mac", CLIENT_MAC, "action", "mirred", "egress", "redirect",
             "dev", "ifb-up"),
            ("tc", "qdisc", "add", "dev", "ifb-up", "root", "netem", "delay",
             f"{profile.upload.delay_ms}ms", "reorder", f"{profile.upload.reorder_percent}%",
             "duplicate", f"{profile.upload.duplicate_percent}%",
             "corrupt", f"{profile.upload.corrupt_percent}%"),
        ]
        for command in commands:
            run_in(PI, *command)
        if drop_invalid:
            run_in(PI, "nft", "-f", "-", stdin=(
                "table inet lab_invalid {\n chain forward {\n"
                "  type filter hook forward priority -300; policy accept;\n"
                "  ct state invalid counter drop\n }\n}\n"))

    def remove_ifb(self):
        run_in(PI, "tc", "qdisc", "del", "dev", "wlan0", "handle", "ffff:", "ingress",
               check=False)
        run_in(PI, "ip", "link", "del", "ifb-up", check=False)
        run_in(PI, "nft", "delete", "table", "inet", "lab_invalid", check=False)

    def test_0_h5_impaired_uploads_on_the_router_cable(self):
        lab = self.make_lab()
        lab.load_control_firewall()
        lab.spawn(INET, ["python", "-c", self.SINK], log="inet-sink.log")
        self.join_pi(CLIENT, OTHER_CLIENT)
        address = lab.address(CLIENT)
        executor = NamespaceExecutor(PI)
        applier = NetworkApplier(executor=executor, topology=TOPOLOGY)
        (self.directory / "access").mkdir(exist_ok=True)
        helper = HelperService(applier, active_plan_path=self.directory / "access" / "plan.json")
        climb = self.climb()
        layouts = {}
        self.measure("baseline_no_profile", layouts)
        scopes = {
            "current_eth0_egress_device_scope": compile_plan(
                devices=(Device("client", "Client", address, "climb", CLIENT_MAC),),
                profiles={"climb": climb}),
            "current_eth0_egress_general_scope": compile_plan(
                devices=(), profiles={"climb": climb},
                general_profile=GeneralProfileRule("climb")),
            "current_eth0_egress_service_scope": compile_plan(
                devices=(), profiles={"climb": climb},
                service_list_rules=(ServiceListRule("web", ("www.example.org",), 0,
                                                    climb.upload, climb.download),)),
            "current_eth0_egress_device_no_corruption": compile_plan(
                devices=(Device("client", "Client", address, "climb", CLIENT_MAC),),
                profiles={"climb": replace(climb, upload=replace(climb.upload,
                                                                   corrupt_percent=0))}),
        }
        for name, plan in scopes.items():
            response = helper.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
            self.assertTrue(response["ok"], (name, response))
            if plan.service_list_rules:
                applier.update_service_addresses(
                    plan=plan, additions={"web": [IPv4Address(WEB_ADDRESS)]}, removals={})
            layouts[name] = {"tc_eth0": run_in(PI, "tc", "qdisc", "show", "dev", "eth0").stdout}
            self.measure(name, layouts)
            self.assertTrue(helper.handle_payload({"action": "reset"})["ok"])
        self.ifb_upload(climb)
        self.measure("proposed_wlan0_ingress_ifb", layouts)
        self.remove_ifb()
        self.ifb_upload(climb, drop_invalid=True)
        self.measure("proposed_wlan0_ingress_ifb_plus_ct_invalid_drop", layouts)
        layouts["proposed_wlan0_ingress_ifb_plus_ct_invalid_drop"]["invalid_dropped"] = run_in(
            PI, "nft", "list", "table", "inet", "lab_invalid").stdout.split("counter")[-1][:40]
        self.remove_ifb()
        record("0_h5_impaired_uplink", **{key: value for key, value in layouts.items()})
        path = Path(os.environ.get("JITTERSIM_LAB_RESULTS", "/tmp/x")).parent / "h5.json"
        path.write_text(json.dumps(layouts, indent=2, default=str))
        device = layouts["current_eth0_egress_device_scope"]["frames"]["problems"]
        self.assertTrue(device, "the current layout puts damaged frames on the router cable")
        local = {problem: kinds for problem, kinds in device.items()
                 if any(kind.startswith("local-") for kind in kinds)}
        self.assertEqual(local, {}, "the Pi's own frames must never be impaired")


class StaleLeaseTests(OutageLabCase):
    """Scenario 2: a client roams between the router's radio and the Pi's (same SSID)."""

    def roam_with_reboot(self, source, target, scenario):
        """Join `source`, then roam to `target` and restart DHCP with the saved lease.

        Restarting dhclient with its lease file sends INIT-REBOOT (a DHCPREQUEST for
        the old address with no server identifier), which is what phones and
        laptops do when they rejoin the "same" network name.
        """
        lab = self.lab
        lab.associate(CLIENT, source)
        first = lab.dhcp(CLIENT, fresh=True)
        self.assertIsNotNone(lab.wait_browse(CLIENT, 10), "no browsing before the roam")
        lab.stop_dhcp(CLIENT)
        lab.associate(CLIENT, target)
        roamed = time.monotonic()
        lab.start_dhcp_daemon(CLIENT)
        seconds = lab.wait_browse(CLIENT, 60, since=roamed)
        record(scenario, first_address=first["address"], address_after=lab.address(CLIENT),
               gateway_after=lab.default_gateway(CLIENT), seconds_from_roam_to_browse=seconds,
               dhclient=dhcp_events(lab.directory / f"dhclient-{CLIENT}-c0.log", 12),
               server=dhcp_events(lab.directory / ("pi-dnsmasq.log" if target == "pi"
                                                   else "router-dnsmasq.log"), 8))
        return seconds

    def test_2a_router_to_pi_as_installed_before_the_fix(self):
        # NetworkManager's shared dnsmasq without dhcp-authoritative ignores the
        # INIT-REBOOT request for the router-era address; the client times out.
        self.make_lab(pi_authoritative=False)
        seconds = self.roam_with_reboot("router", "pi", "2a_router_to_pi_nm_default")
        self.assertIsNotNone(seconds)
        self.assertGreater(seconds, 8, "the client should wait out its INIT-REBOOT timeout")

    def test_2b_router_to_pi_with_the_installed_dhcp_authoritative(self):
        self.make_lab()
        seconds = self.roam_with_reboot("router", "pi", "2b_router_to_pi_authoritative")
        self.assertIsNotNone(seconds)
        self.assertLess(seconds, 5, "an authoritative server answers INIT-REBOOT at once")

    def test_2c_pi_to_router_rollback_router_authoritative(self):
        self.make_lab(router_authoritative=True)
        seconds = self.roam_with_reboot("pi", "router", "2c_pi_to_router_router_authoritative")
        self.assertIsNotNone(seconds)

    def test_2d_pi_to_router_rollback_router_not_authoritative(self):
        self.make_lab(router_authoritative=False)
        seconds = self.roam_with_reboot("pi", "router", "2d_pi_to_router_router_silent")
        self.assertIsNotNone(seconds)

    def test_2e_client_that_never_restarts_dhcp_waits_for_its_lease_timers(self):
        # A device that keeps its router lease and does not ask again on rejoin
        # (no INIT-REBOOT) can only recover at rebinding (T2) or expiry. The router
        # lease is 120 s here (dnsmasq's minimum); a real router's lease is hours.
        lab = self.make_lab(router_lease=120)
        lab.associate(CLIENT, "router")
        first = lab.dhcp(CLIENT, fresh=True)
        self.assertIsNotNone(lab.wait_browse(CLIENT, 10))
        lab.associate(CLIENT, "pi")
        roamed = time.monotonic()
        seconds = lab.wait_browse(CLIENT, 170, since=roamed, interval=1.0)
        record("2e_no_reboot_client_router_lease_120s", first_address=first["address"],
               address_after=lab.address(CLIENT), seconds_from_roam_to_browse=seconds,
               dhclient=dhcp_events(lab.directory / f"dhclient-{CLIENT}-c0.log", 12),
               pi_server=dhcp_events(lab.directory / "pi-dnsmasq.log", 8))
        self.assertIsNotNone(seconds)
        self.assertGreater(seconds, 50, "the stale lease should hold until rebinding")


class DnsRulesTests(OutageLabCase):
    """Scenario 3: household DNS rules ON and the filtered resolver fails."""

    def test_3_filtered_dns_failures(self):
        lab = self.make_lab()
        lab.load_control_firewall()
        self.join_pi(CLIENT, OTHER_CLIENT)
        manager = self.access_manager()
        plan = self.child_plan(lab.address(CLIENT))
        manager.apply(plan)
        self.assertEqual(self.dig(CLIENT, "video.example.org"), "NXDOMAIN")
        self.assertEqual(self.dig(OTHER_CLIENT, "video.example.org"), "NOERROR")
        self.assertTrue(lab.browse(OTHER_CLIENT)["ok"])

        # 3a: Unbound crashes and systemd restarts it after RestartSec=2.
        with lab.browse_monitor(OTHER_CLIENT) as crash:
            time.sleep(1)
            self.executor.process.kill()
            self.executor.process.wait()
            time.sleep(2)
            self.executor.systemd_restart()
            lab.wait_browse(OTHER_CLIENT, 15)
            time.sleep(1)
        record("3a_unbound_crash_restart_after_2s", other_client_without_rules=crash)

        # 3b: a rule change whose resolver restart fails once: the old rules come back.
        self.executor.fail_restarts = 1
        with lab.browse_monitor(OTHER_CLIENT) as once:
            time.sleep(1)
            started = time.monotonic()
            try:
                manager.apply(self.child_plan(lab.address(CLIENT), extra_blocks=(
                    SiteBlock("news", "News", ("news.example.org",)),)))
                outcome = "applied"
            except Exception as error:  # noqa: BLE001 - the outcome is the measurement
                outcome = type(error).__name__
            took = round(time.monotonic() - started, 2)
            lab.wait_browse(OTHER_CLIENT, 15)
            time.sleep(1)
        record("3b_rule_change_restart_fails_once", outcome=outcome, transaction_seconds=took,
               manager_error=manager.error, other_client_without_rules=once)

        # 3c: every restart fails: the transition guard stays and closes the test Wi-Fi.
        self.executor.fail_restarts = 100
        try:
            manager.apply(self.child_plan(lab.address(CLIENT), extra_blocks=(
                SiteBlock("news", "News", ("news.example.org",)),)))
        except Exception:  # noqa: BLE001
            pass
        guard_left = "jittersim_access_transition" in run_in(PI, "nft", "list", "tables").stdout
        closed = lab.browse(OTHER_CLIENT)
        error_before_panic = manager.error
        helper = self.helper()
        started = time.monotonic()
        panic = helper.handle_payload({"action": "normal-access"})
        panic_seconds = round(time.monotonic() - started, 2)
        reopened = lab.wait_browse(OTHER_CLIENT, 15)
        record("3c_rule_change_restart_always_fails", guard_left=guard_left,
               other_client_browse_while_guarded=closed, manager_error=error_before_panic,
               panic=panic, panic_seconds=panic_seconds,
               seconds_from_panic_to_browse=reopened)
        self.assertTrue(guard_left)
        self.assertFalse(closed["ok"])
        self.assertTrue(panic["ok"], panic)
        self.assertIsNotNone(reopened)

        # 3d: rules on again, then Unbound dies and cannot start (a crash loop that
        # systemd gives up on). Every Wi-Fi client loses DNS, not just the child.
        self.executor.fail_restarts = 0
        manager.apply(plan)
        self.assertTrue(lab.browse(OTHER_CLIENT)["ok"])
        self.executor.process.kill()
        self.executor.process.wait()
        time.sleep(3)
        dead = {namespace: lab.browse(namespace) for namespace in (CLIENT, OTHER_CLIENT)}
        manager.check_health()
        health = manager.status()["error"]
        started = time.monotonic()
        panic = self.helper().handle_payload({"action": "normal-access"})
        reopened = lab.wait_browse(OTHER_CLIENT, 15, since=started)
        record("3d_unbound_dead_no_restart", browse_while_dead=dead, health_error=health,
               panic=panic, seconds_from_panic_to_browse=reopened)
        self.assertFalse(dead[OTHER_CLIENT]["ok"],
                         "a dead filtered resolver takes DNS from every Wi-Fi client")
        self.assertIsNotNone(reopened)


    @unittest.expectedFailure
    def test_3e_a_dead_filtered_resolver_leaves_devices_without_rules_resolving(self):
        # access.py render_access_firewall redirects port 53 of EVERY test Wi-Fi
        # client to the filtered resolver once any DNS rule exists (gated file).
        # When Unbound is down and cannot start, devices without any rule lose DNS
        # too until Panic. Strict: passes once unruled clients bypass the redirect
        # or fall back to NetworkManager's resolver.
        lab = self.make_lab()
        lab.load_control_firewall()
        self.join_pi(CLIENT, OTHER_CLIENT)
        manager = self.access_manager()
        manager.apply(self.child_plan(lab.address(CLIENT)))
        self.executor.process.kill()
        self.executor.process.wait()
        time.sleep(2)
        self.assertTrue(lab.browse(OTHER_CLIENT)["ok"])


class PanicSequenceTests(OutageLabCase):
    """Scenario 4: Panic, Reset, Start and Panic again with a wedged hostapd control socket."""

    def start_hostapd(self):
        control = self.directory / "hostapd"
        config = self.directory / "hostapd.conf"
        config.write_text(f"interface=wlan0\ndriver=none\nssid=LabAP\nctrl_interface={control}\n")
        daemon = subprocess.Popen(["ip", "netns", "exec", PI, "hostapd", str(config)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_hostapd, daemon)
        for _ in range(100):
            if (control / "wlan0").is_socket():
                return control, daemon
            time.sleep(0.05)
        self.fail("hostapd did not start in controller-only mode")

    @staticmethod
    def stop_hostapd(daemon):
        if daemon.poll() is None:
            os.kill(daemon.pid, signal.SIGCONT)
            daemon.terminate()
            daemon.wait(timeout=5)

    def household(self):
        """The owner's setup: household MAC rules on, an experiment on one device."""
        lab = self.make_lab()
        lab.load_control_firewall()
        self.join_pi(CLIENT, OTHER_CLIENT)
        control, daemon = self.start_hostapd()
        manager = self.access_manager(hostapd=control)
        access = AccessPlan(mac=MacPolicy("denylist", (), ("02:00:00:00:00:99",)))
        manager.apply(access)
        helper = self.helper()
        plan = compile_plan(
            devices=(Device("other", "Other", lab.address(OTHER_CLIENT), "slow",
                            OTHER_CLIENT_MAC),),
            profiles={"slow": Profile("slow", "Slow", upload=Impairment(delay_ms=30))},
            access=access,
        )
        started = helper.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
        self.assertTrue(started["ok"], started)
        self.assertTrue(lab.browse(CLIENT)["ok"])
        return lab, helper, plan, daemon

    def test_4a_panic_reset_start_with_wedged_hostapd(self):
        lab, helper, plan, daemon = self.household()
        os.kill(daemon.pid, signal.SIGSTOP)
        wedged = time.monotonic()
        steps = []

        def step(name, payload):
            started = time.monotonic()
            response = helper.handle_payload(payload)
            steps.append({
                "step": name, "seconds": round(time.monotonic() - started, 2),
                "ok": response.get("ok"), "error": response.get("error"),
                "access_disabled": response.get("access_disabled"),
                "guard_table": "jittersim_access_transition" in run_in(
                    PI, "nft", "list", "tables").stdout,
                "client_browses": lab.browse(CLIENT)["ok"],
            })
            return response

        step("panic", {"action": "normal-access"})
        revision = helper.handle_payload({"action": "control-revision"})["control_revision"]
        step("start", {"action": "apply", "plan": plan_to_payload(plan),
                       "explicit_apply": True, "expected_control_revision": revision})
        step("reset", {"action": "reset"})
        step("panic-retry", {"action": "normal-access"})
        os.kill(daemon.pid, signal.SIGCONT)
        resumed = time.monotonic()
        step("panic-after-hostapd-recovers", {"action": "normal-access"})
        reopened = lab.wait_browse(CLIENT, 15)
        record("4a_panic_with_wedged_hostapd", steps=steps,
               closed_seconds_until_hostapd_recovered=round(resumed - wedged, 2),
               seconds_to_browse_after_final_panic=reopened,
               access_error=self.manager.error)
        self.assertFalse(steps[0]["ok"])
        self.assertTrue(steps[-1]["ok"], steps[-1])
        self.assertIsNotNone(reopened)

    @unittest.expectedFailure
    def test_4b_panic_keeps_the_test_wifi_open_when_hostapd_is_wedged(self):
        # Gap audit finding 2 (access.py:556-579, gated file): Panic's household
        # release installs the transition guard, fails in hostapd admission, fails
        # again in the rollback and leaves the guard. Expected: Panic always opens
        # forwarding. Strict: this test must start passing once that is fixed.
        lab, helper, _, daemon = self.household()
        os.kill(daemon.pid, signal.SIGSTOP)
        helper.handle_payload({"action": "normal-access"})
        self.assertTrue(lab.browse(CLIENT)["ok"])


class AddressRefreshTests(OutageLabCase):
    """Scenario 5: the background address refresh runs a full access transaction."""

    def test_5_refresh_while_another_client_streams(self):
        lab = self.make_lab()
        lab.load_control_firewall()
        self.join_pi(CLIENT, OTHER_CLIENT)
        manager = self.access_manager()
        actual = lab.address(CLIENT)
        stale = "192.168.50.10" if actual != "192.168.50.10" else "192.168.50.11"
        # The saved rules still hold the child's previous address (it got a new lease).
        manager.apply(self.child_plan(stale))
        cut_off = lab.browse(CLIENT)
        results = {"ruled_device_with_new_lease_browse": cut_off}
        for delay in (0.0, 1.5):
            if delay:
                manager.apply(self.child_plan(stale))
            self.executor.restart_delay = delay
            with lab.stream(OTHER_CLIENT) as stream:
                time.sleep(1)
                started = time.monotonic()
                manager.refresh_addresses([{"mac_address": CLIENT_MAC, "ipv4_address": actual}])
                took = round(time.monotonic() - started, 2)
                time.sleep(1)
            child = lab.wait_browse(CLIENT, 10)
            results[f"restart_delay_{delay}s"] = {
                "transaction_seconds": took, "unrelated_stream": stream,
                "child_seconds_to_browse_after_refresh": child,
            }
        record("5_address_refresh_during_stream", **results)
        self.assertFalse(cut_off["ok"], "a ruled device on a new address is cut off")
        for delay in (0.0, 1.5):
            self.assertGreaterEqual(
                results[f"restart_delay_{delay}s"]["unrelated_stream"]["max_gap_seconds"], delay,
                "the transition guard pauses forwarding for every Wi-Fi client")


    @unittest.expectedFailure
    def test_5b_an_address_refresh_does_not_pause_unrelated_clients(self):
        # Gap audit finding 3 (access.py:602-623 calls apply(), which installs the
        # transition guard at access.py:556 for the whole resolver restart; gated
        # file). Strict: passes once a refresh updates only the moved device.
        lab = self.make_lab()
        lab.load_control_firewall()
        self.join_pi(CLIENT, OTHER_CLIENT)
        manager = self.access_manager()
        actual = lab.address(CLIENT)
        manager.apply(self.child_plan("192.168.50.10" if actual != "192.168.50.10"
                                      else "192.168.50.11"))
        self.executor.restart_delay = 1.5
        with lab.stream(OTHER_CLIENT) as stream:
            time.sleep(1)
            manager.refresh_addresses([{"mac_address": CLIENT_MAC, "ipv4_address": actual}])
            time.sleep(1)
        self.assertLess(stream["max_gap_seconds"], 0.5)


class ConntrackPressureTests(OutageLabCase):
    """Scenario 6: connection-tracking entries per page and a full table."""

    FLOOD = r"""
import socket, sys
count = int(sys.argv[1])
sockets = []
for port in range(20000, 20000 + count):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b"x", ("198.51.100.99", port))
    sockets.append(s)
"""

    def lookups(self, namespace, prefix, count):
        for index in range(count):
            run_in(namespace, "dig", "+time=1", "+tries=1", "+short",
                   f"{prefix}{index}.flood.example.org", check=False)

    def test_6_conntrack_entries_and_a_full_table(self):
        lab = self.make_lab()
        lab.load_control_firewall()
        self.join_pi(CLIENT, OTHER_CLIENT)
        run_in(PI, "conntrack", "-F", check=False)
        base = lab.conntrack_count()
        for _ in range(10):
            lab.browse(CLIENT)
        pages = lab.conntrack_count() - base
        run_in(PI, "conntrack", "-F", check=False)
        base = lab.conntrack_count()
        self.lookups(CLIENT, "plain", 20)
        plain = lab.conntrack_count() - base
        manager = self.access_manager()
        manager.apply(self.child_plan(lab.address(CLIENT)))
        run_in(PI, "conntrack", "-F", check=False)
        base = lab.conntrack_count()
        self.lookups(CLIENT, "filtered", 20)
        filtered = lab.conntrack_count() - base
        slab = run("sh", "-c", "grep -E '^nf_conntrack ' /proc/slabinfo",
                   check=False).stdout.split()
        memory = {}
        for name, pattern in (("unbound", "unbound -d"), ("pi_dnsmasq", "dnsmasq-wlan0")):
            pids = run("pgrep", "-f", pattern, check=False).stdout.split()
            if pids:
                status = Path(f"/proc/{pids[0]}/status").read_text()
                memory[name + "_rss_kib"] = int(re.search(r"VmRSS:\s+(\d+)", status).group(1))
        record("6_conntrack_and_memory",
               entries_for_10_page_loads=pages, entries_for_20_uncached_lookups_plain=plain,
               entries_for_20_uncached_lookups_filtered=filtered,
               conntrack_object_bytes=int(slab[3]) if len(slab) > 3 else None,
               lab_nf_conntrack_max=int(Path("/proc/sys/net/netfilter/nf_conntrack_max")
                                        .read_text()),
               udp_unreplied_timeout_seconds=int(Path(
                   "/proc/sys/net/netfilter/nf_conntrack_udp_timeout").read_text()),
               process_memory=memory)

        # A full table, emulated: nf_conntrack_max is global and read-only inside a
        # network namespace, so the lab caps flows entering from the test Wi-Fi with
        # nftables `ct count` instead. Kernel behaviour at the real limit is the same
        # in kind: new flows are dropped, existing ones continue.
        cap = 300
        table = f"""table inet lab_cap {{
    set flows {{ type ifname; size 16; flags dynamic; }}
    chain cap {{
        type filter hook prerouting priority -150; policy accept;
        ct state new add @flows {{ iifname ct count over {cap} }} counter drop
    }}
}}
"""
        loaded = run_in(PI, "nft", "-f", "-", stdin=table, check=False)
        if loaded.returncode:
            record("6_conntrack_and_memory", full_table="nft ct count unsupported: "
                   + loaded.stderr.strip())
            return
        self.assertTrue(lab.browse(CLIENT)["ok"])
        run_in(OTHER_CLIENT, "python", "-c", self.FLOOD, str(cap + 50))
        flooded = time.monotonic()
        during = lab.browse(CLIENT)
        recovered = lab.wait_browse(CLIENT, 60, since=flooded, interval=1.0)
        record("6_conntrack_and_memory", full_table={
            "cap_on_wifi_flows": cap, "flood_flows_from_other_client": cap + 50,
            "client_browse_during_flood": during,
            "seconds_until_client_browses_again": recovered,
        })
        self.assertFalse(during["ok"])
        self.assertIsNotNone(recovered)


class RouterFootprintTests(OutageLabCase):
    """Scenario 7: every frame the Pi puts on the router LAN, by phase."""

    AVAHI = """[server]
host-name=jittersim-lab-pi
use-ipv4=yes
use-ipv6=yes
enable-dbus=no
[publish]
disable-user-service-publishing=yes
publish-workstation={publish}
publish-addresses={publish}
publish-hinfo={publish}
publish-domain={publish}
[reflector]
enable-reflector=no
"""

    def avahi(self, publish):
        path = self.directory / f"avahi-{publish}.conf"
        path.write_text(self.AVAHI.format(publish=publish))
        Path("/run/avahi-daemon").mkdir(parents=True, exist_ok=True)
        for leftover in Path("/run/avahi-daemon").glob("*"):
            leftover.unlink()
        return self.lab.spawn(PI, ["avahi-daemon", "-f", str(path), "--no-drop-root",
                                   "--no-chroot", "--no-rlimits"], log=f"avahi-{publish}.log")

    def phase(self, name, action, phases):
        lab = self.lab
        with lab.capture(ROUTER, "r-pi", name, inbound_only=True) as capture:
            extra = action() or {}
        summary = classify_frames(capture["path"], PI_ETH0_MAC, lab.pi_eth0_address)
        seconds = capture["seconds"]
        summary["capture_seconds"] = seconds
        summary["broadcast_multicast_per_second"] = round(sum(
            count for kind, count in summary["kinds"].items()
            if not kind.startswith(("ipv4-unicast", "arp-reply"))) / max(seconds, 0.1), 2)
        summary.update(extra)
        phases[name] = summary
        return summary

    def test_7_what_the_router_sees_from_the_pi(self):
        lab = self.make_lab()
        phases = {}

        def uplink():
            lab.dhcp(PI, "eth0", fresh=True)
            lab.pi_eth0_address = lab.address(PI, "eth0")
            restricted = self.avahi("no")
            time.sleep(20)
            restricted.terminate()
            restricted.wait(timeout=5)

        def stock_avahi():
            stock = self.avahi("yes")
            time.sleep(20)
            stock.terminate()
            stock.wait(timeout=5)

        def cutover():
            lab.load_control_firewall()
            self.join_pi(CLIENT, OTHER_CLIENT)
            for _ in range(5):
                lab.browse(CLIENT)
                lab.browse(OTHER_CLIENT)
            # A household device asks for a lease: only the router may answer.
            lab.dhcp(HOUSE, fresh=True)
            dns_on_uplink = run_in(HOUSE, "dig", "+time=1", "+tries=1",
                                   f"@{lab.pi_eth0_address}", "www.example.org",
                                   check=False).stdout
            return {"pi_dns_answers_on_eth0": "ANSWER SECTION" in dns_on_uplink,
                    "house_lease": lab.address(HOUSE),
                    "pi_dnsmasq_on_eth0": [line for line in (
                        lab.directory / "pi-dnsmasq.log").read_text().splitlines()
                        if "eth0" in line][-3:]}

        self.access_manager()

        def shaping():
            helper = self.helper()
            plan = compile_plan(
                devices=(Device("client", "Client", lab.address(CLIENT), "slow", CLIENT_MAC),),
                profiles={"slow": Profile("slow", "Slow",
                                          upload=Impairment(delay_ms=100),
                                          download=Impairment(delay_ms=100))},
            )
            applied = helper.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
            rtt = {}
            for namespace in (CLIENT, OTHER_CLIENT, HOUSE):
                output = run_in(namespace, "ping", "-n", "-c", "10", "-i", "0.2", WEB_ADDRESS,
                                check=False).stdout
                average = re.search(r"= [\d.]+/([\d.]+)/", output)
                loss = re.search(r"(\d+)% packet loss", output)
                rtt[namespace.rsplit("-", 1)[-1]] = {
                    "avg_ms": float(average.group(1)) if average else None,
                    "loss_percent": int(loss.group(1)) if loss else None}
            counters = run_in(PI, "nft", "list", "table", "ip", "nm-shared-wlan0",
                              check=False).stdout
            self.shaping_rtt = rtt
            return {"apply_ok": applied.get("ok"), "rtt": rtt,
                    "nm_shared_rejects": re.findall(r"reject", counters).__len__()}

        def panic():
            response = self.helper().handle_payload({"action": "normal-access"})
            return {"panic_ok": response.get("ok")}

        def ap_restart():
            # The 2026-10-04 rollback timer ran nmcli to re-activate the AP.
            lab.restart_pi_access_point()
            for namespace in (CLIENT, OTHER_CLIENT):
                lab.stop_dhcp(namespace)
                lab.start_dhcp_daemon(namespace)
            return {"client_seconds_to_browse": lab.wait_browse(CLIENT, 30)}

        def stale_client():
            # OTHER keeps a router-era address and gateway on the Pi's radio.
            lab.stop_dhcp(OTHER_CLIENT)
            run("ip", "-n", OTHER_CLIENT, "addr", "flush", "dev", "c1")
            run("ip", "-n", OTHER_CLIENT, "addr", "add", "192.168.0.77/24", "dev", "c1")
            run("ip", "-n", OTHER_CLIENT, "route", "replace", "default", "via", "192.168.0.1")
            for target in (WEB_ADDRESS, "192.168.0.1", lab.pi_eth0_address):
                run_in(OTHER_CLIENT, "ping", "-n", "-c", "3", "-i", "0.2", "-W", "1", target,
                       check=False)
            neighbours = run("ip", "-n", OTHER_CLIENT, "neigh", check=False).stdout
            return {"stale_client_neighbours": neighbours.strip().splitlines(),
                    "stale_client_reaches_pi_eth0_address": "lladdr" in neighbours
                    and lab.pi_eth0_address in neighbours}

        for name, action in (("uplink_dhcp_and_restricted_avahi_20s", uplink),
                             ("stock_avahi_for_comparison_20s", stock_avahi),
                             ("cutover_clients_join_and_browse", cutover),
                             ("shaping_applied", shaping), ("panic", panic),
                             ("ap_restart_like_rollback_timer", ap_restart),
                             ("stale_router_address_client_on_pi_radio", stale_client)):
            self.phase(name, action, phases)
        record("7_router_view_of_the_pi", **phases)
        for name, summary in phases.items():
            kinds = summary["kinds"]
            self.assertFalse([kind for kind in kinds if kind.isupper() or "SENDER" in kind
                              or "FOREIGN" in kind or "SERVER" in kind
                              or "ADVERTISEMENT" in kind], (name, kinds))
            self.assertEqual(summary["foreign_source_macs"], {}, name)
        self.assertFalse(phases["cutover_clients_join_and_browse"]["pi_dns_answers_on_eth0"])
        rtt = self.shaping_rtt
        client, other = rtt[CLIENT.rsplit("-", 1)[-1]], rtt[OTHER_CLIENT.rsplit("-", 1)[-1]]
        self.assertGreater(client["avg_ms"], 150)
        self.assertLess(other["avg_ms"], 50, "shaping one device must not slow another")
        self.assertEqual(other["loss_percent"], 0)


class RouterCopyAndRollbackTests(OutageLabCase):
    """Scenario 8: copy the router's Wi-Fi to the Pi, then the rollback timer path."""

    FIRMWARE = r"""
import json, sys, time
from http.server import ThreadingHTTPServer
import tests.unit.test_router as fixture
log = open(sys.argv[1], "a", buffering=1)
original = ThreadingHTTPServer.__init__
def bind_router(self, address, handler, *rest, **options):
    original(self, ("192.168.0.1", 80), handler, *rest, **options)
ThreadingHTTPServer.__init__ = bind_router
original_get = fixture.FirmwareHandler.do_GET
original_post = fixture.FirmwareHandler.do_POST
def logged_get(self):
    log.write(json.dumps([round(time.time(), 2), "GET", self.path.split("?")[0]]) + "\n")
    original_get(self)
def logged_post(self):
    log.write(json.dumps([round(time.time(), 2), "POST", self.path]) + "\n")
    original_post(self)
fixture.FirmwareHandler.do_GET = logged_get
fixture.FirmwareHandler.do_POST = logged_post
server = fixture.FirmwareServer()
server.serve_forever()
"""

    def test_8_copy_router_wifi_then_rollback_timer(self):
        lab = self.make_lab()
        lab.load_control_firewall()
        calls = lab.directory / "router-api.json"
        lab.spawn(ROUTER, ["python", "-c", self.FIRMWARE, str(calls)], log="router-api.log")
        for _ in range(50):
            if "192.168.0.1:80 " in run_in(ROUTER, "ss", "-lnt", check=False).stdout:
                break
            time.sleep(0.1)
        # A household device stays on the router radio while the Pi copies the name.
        lab.associate(CLIENT, "router")
        lab.dhcp(CLIENT, fresh=True)
        self.access_manager()
        events = []
        executor = LabWifiExecutor(lab, events)
        helper = self.helper(executor=executor)
        before = pi_snapshot()
        with lab.browse_monitor(CLIENT) as household:
            with in_namespace(PI):
                preview = helper.handle_payload({"action": "router-preview-ap",
                                                 "admin_password": "router-test-password"})
                synced = helper.handle_payload({
                    "action": "router-sync-ap", "admin_password": "router-test-password",
                    "expected_fingerprint": preview.get("fingerprint"), "confirm_legacy": True})
        after = pi_snapshot()
        lines = calls.read_text().splitlines() if calls.exists() else []
        api = [json.loads(line) for line in lines]
        logins = sum(1 for _, method, path in api if path.endswith("ajaxSet_Password.php"))
        logouts = sum(1 for _, method, path in api if path.endswith("logout.php"))
        record("8_copy_router_wifi", preview_ok=preview.get("ok"), sync=synced,
               router_requests=[f"{method} {path}" for _, method, path in api],
               logins=logins, logouts=logouts, nm_steps=events, pi_before=before,
               pi_after=after, household_client_on_router_during_copy=household,
               ssid_now=executor.wifi.settings["802-11-wireless.ssid"])
        self.assertTrue(synced.get("ok"), synced)
        self.assertEqual(logins, logouts, "every router session must be closed")
        self.assertEqual(before["routes"], after["routes"])
        self.assertEqual(household["failed_probes"], 0)

        # Cutover: router Wi-Fi off, the client roams to the Pi's copied name.
        lab.stop_dhcp(CLIENT)
        lab.associate(CLIENT, "pi")
        lab.start_dhcp_daemon(CLIENT)
        on_pi = lab.wait_browse(CLIENT, 60)
        # The rollback timer: the AP is renamed and re-activated, so copied-name
        # clients lose it. With router Wi-Fi back on they roam home holding a Pi lease.
        started = time.monotonic()
        lab.restart_pi_access_point()
        lab.stop_dhcp(CLIENT)
        lab.associate(CLIENT, "router")
        lab.start_dhcp_daemon(CLIENT)
        home = lab.wait_browse(CLIENT, 60, since=started)
        record("8_copy_router_wifi", seconds_to_browse_after_cutover=on_pi,
               rollback_seconds_to_browse_on_router=home,
               rollback_dhcp=dhcp_events(lab.directory / "router-dnsmasq.log", 6))
        self.assertIsNotNone(on_pi)
        self.assertIsNotNone(home)


if __name__ == "__main__":
    unittest.main()
