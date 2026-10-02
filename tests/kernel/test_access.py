"""Exercise domain rules using real Unbound and nftables in isolated networks."""

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from jittersim.config.models import AccessOptions, MacPolicy, SiteBlock, WebsitePolicy
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.networking.access import AccessManager, read_access, transition_firewall
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError
from jittersim.networking.helper import HelperService
from tests.kernel.lab import (
    CLIENT,
    CLIENT_ADDRESS,
    CLIENT_MAC,
    OTHER_CLIENT,
    OTHER_CLIENT_ADDRESSES,
    PI,
    ROUTER,
    TOPOLOGY,
    NamespaceExecutor,
    create_lab,
    remove_lab,
    run_in,
)

ROOT = Path(__file__).resolve().parents[2]

ENABLED = (
    sys.platform == "linux"
    and os.geteuid() == 0
    and os.environ.get("JITTERSIM_KERNEL_TESTS") == "1"
)


class DnsServiceExecutor(NamespaceExecutor):
    """Replace systemd only. DNS processes and all network operations are real."""

    def __init__(self, directory):
        super().__init__(PI)
        self.directory = directory
        self.process = None
        self.fail_restarts = 0
        self.log = (directory / "dns.log").open("w+")

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.process = None

    def run(self, command):
        if command.executable != "systemctl":
            return super().run(command)
        if command.arguments[0] == "stop":
            self.stop()
            return CommandResult(return_code=0)
        if command.arguments[0] == "restart":
            self.stop()
            if self.fail_restarts:
                self.fail_restarts -= 1
                raise CommandExecutionError("simulated service failure")
            path = self.directory / "unbound.conf"
            self.process = subprocess.Popen(
                ["ip", "netns", "exec", PI, "unbound", "-d", "-c", str(path)],
                stdout=self.log,
                stderr=self.log,
            )
        if not self.process or self.process.poll() is not None:
            raise CommandExecutionError("DNS service stopped")
        return CommandResult(return_code=0)


@unittest.skipUnless(
    ENABLED and shutil.which("unbound"), "requires the privileged Linux test container"
)
class AccessDatapathTests(unittest.TestCase):
    def setUp(self):
        create_lab()
        self.addCleanup(remove_lab)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        # Unbound reads configuration before dropping privileges.
        self.directory.chmod(0o755)
        upstream = self.directory / "upstream.conf"
        upstream.write_text("""server:
  interface: 192.168.50.1@53
  chroot: ""
  directory: "/"
  pidfile: ""
  do-ip6: no
  access-control: 0.0.0.0/0 allow
  local-zone: "example.com." static
  local-data: "video.example.com. 60 IN A 203.0.113.7"
  local-data: "cdn.video.example.com. 60 IN A 203.0.113.7"
  local-data: "notvideo.example.com. 60 IN A 203.0.113.7"
  local-data: "news.example.com. 60 IN A 203.0.113.7"
  local-data: "global.example.com. 60 IN A 203.0.113.8"
""")
        self.upstream = subprocess.Popen(
            ["ip", "netns", "exec", PI, "unbound", "-d", "-c", str(upstream)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.stop_upstream)
        self.executor = DnsServiceExecutor(self.directory)
        self.addCleanup(self.executor.log.close)
        self.addCleanup(self.executor.stop)
        self.manager = AccessManager(
            executor=self.executor,
            topology=TOPOLOGY,
            directory=self.directory,
            runtime_path=self.directory / "runtime.json",
        )
        self.plan = AccessPlan(
            blocks=(SiteBlock("video", "Video", ("video.example.com",), device_id="child"),),
            clients=(AccessClient("child", CLIENT_ADDRESS, CLIENT_MAC, ("video",)),),
        )

    def stop_upstream(self):
        self.upstream.terminate()
        self.upstream.wait(timeout=5)

    def query(self, name, namespace=CLIENT, *, tcp=False, resolver="8.8.8.8"):
        result = run_in(
            namespace,
            "dig",
            "@" + resolver,
            name,
            "+time=1",
            "+tries=1",
            *(("+tcp",) if tcp else ()),
            check=False,
        )
        return result.stdout

    def test_targets_subdomains_tcp_external_dns_shared_address_and_reset(self):
        self.manager.apply(self.plan)
        for tcp in (False, True):
            self.assertIn("status: NXDOMAIN", self.query("video.example.com", tcp=tcp))
            self.assertIn("status: NXDOMAIN", self.query("cdn.video.example.com", tcp=tcp))
            self.assertIn("203.0.113.7", self.query("notvideo.example.com", tcp=tcp))
            self.assertIn("203.0.113.7", self.query("news.example.com", tcp=tcp))
            self.assertIn("203.0.113.7", self.query("video.example.com", OTHER_CLIENT, tcp=tcp))
            # Reversing the order must not leak an allowed cached answer to the child.
            self.assertIn("status: NXDOMAIN", self.query("video.example.com", tcp=tcp))
        NetworkApplier(executor=self.executor, topology=TOPOLOGY).reset()
        self.assertIn("status: NXDOMAIN", self.query("video.example.com"))

    def test_dns_failure_percentage_rejects_only_test_wifi_requests(self):
        self.manager.apply(
            replace(self.plan, options=AccessOptions(dns_failure_percent=0))
        )
        for tcp in (False, True):
            self.assertIn("203.0.113.7", self.query("news.example.com", tcp=tcp))

        try:
            self.manager.apply(
                replace(self.plan, options=AccessOptions(dns_failure_percent=100))
            )
        except CommandExecutionError as error:
            self.fail(f"could not install DNS failure rules: {error.stderr}")
        for namespace in (CLIENT, OTHER_CLIENT):
            for tcp in (False, True):
                result = run_in(
                    namespace,
                    "dig",
                    "@8.8.8.8",
                    "news.example.com",
                    "+time=1",
                    "+tries=1",
                    *(('+tcp',) if tcp else ()),
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("203.0.113.7", result.stdout + result.stderr)

        management_query = run_in(
            PI,
            "dig",
            "@192.168.50.1",
            "news.example.com",
            "+time=1",
            "+tries=1",
            check=False,
        )
        self.assertEqual(management_query.returncode, 0, management_query.stderr)
        self.assertIn("203.0.113.7", management_query.stdout)

    def test_global_rules_allow_only_and_explicit_disable(self):
        plan = replace(
            self.plan,
            websites=WebsitePolicy("allow_only", ("example.com",), ("global.example.com",)),
        )
        self.manager.apply(plan)
        self.assertIn("status: NXDOMAIN", self.query("global.example.com", OTHER_CLIENT))
        self.assertIn("status: NXDOMAIN", self.query("outside.example", OTHER_CLIENT))
        self.assertIn("203.0.113.7", self.query("news.example.com", OTHER_CLIENT))
        self.manager.apply(replace(plan, options=AccessOptions(enabled=False)))
        self.assertIn("203.0.113.7", self.query("video.example.com", resolver="192.168.50.1"))

    def test_failed_apply_restores_policy_and_restart_recovers_it(self):
        self.manager.apply(self.plan)
        self.executor.fail_restarts = 1
        with self.assertRaises(CommandExecutionError):
            # A changed rule needs the DNS service. Dropping every rule would not.
            self.manager.apply(
                replace(
                    self.plan,
                    blocks=(SiteBlock("video", "Video", ("news.example.com",), device_id="child"),),
                )
            )
        self.assertEqual(read_access(self.directory / "policy.json"), self.plan)
        self.assertIn("status: NXDOMAIN", self.query("video.example.com"))
        self.executor.stop()
        run_in(PI, "nft", "delete", "table", "inet", "jittersim_access")
        self.manager.restore()
        self.assertIn("status: NXDOMAIN", self.query("video.example.com"))
        self.assertEqual(
            json.loads((self.directory / "runtime.json").read_text()),
            json.loads(json.dumps(self.plan.payload())),
        )

    def test_mac_block_and_allowlist_survive_reset(self):
        run_in(ROUTER, "ip", "route", "add", "192.168.50.0/24", "via", "192.168.0.2")

        def incoming(namespace=CLIENT, address=CLIENT_ADDRESS):
            # The receiver sends nothing back. This catches a download that would
            # survive a filter checking only traffic sent by the blocked client.
            receiver = subprocess.Popen(
                [
                    "ip", "netns", "exec", namespace, "python", "-u", "-c",
                    "import socket\n"
                    "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                    "s.bind(('0.0.0.0', 50777))\n"
                    "s.settimeout(1)\n"
                    "print('ready')\n"
                    "try:\n"
                    "    print(s.recv(32).decode())\n"
                    "except socket.timeout:\n"
                    "    print('blocked')\n",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertTrue(select.select([receiver.stdout], [], [], 3)[0])
                self.assertEqual(receiver.stdout.readline().strip(), "ready")
                run_in(
                    ROUTER, "python", "-c",
                    "import socket; "
                    f"socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'arrived', "
                    f"('{address}', 50777))",
                )
                output, error = receiver.communicate(timeout=3)
                self.assertEqual(receiver.returncode, 0, error)
                return output.strip()
            finally:
                if receiver.poll() is None:
                    receiver.kill()
                    receiver.communicate(timeout=3)

        self.assertEqual(incoming(), "arrived")
        self.manager.apply(replace(self.plan, mac=MacPolicy("denylist", (), (CLIENT_MAC,))))
        self.assertEqual(incoming(), "blocked")
        self.assertEqual(incoming(OTHER_CLIENT, OTHER_CLIENT_ADDRESSES[0]), "arrived")
        self.assertNotIn("status: ", self.query("news.example.com"))
        self.assertIn("203.0.113.7", self.query("news.example.com", OTHER_CLIENT))
        NetworkApplier(executor=self.executor, topology=TOPOLOGY).reset()
        self.assertEqual(incoming(), "blocked")
        self.assertNotIn("status: ", self.query("news.example.com"))
        self.manager.apply(replace(self.plan, mac=MacPolicy("allowlist", (CLIENT_MAC,), ())))
        self.assertEqual(incoming(), "arrived")
        self.assertEqual(incoming(OTHER_CLIENT, OTHER_CLIENT_ADDRESSES[0]), "blocked")
        self.assertIn("203.0.113.7", self.query("news.example.com"))
        self.assertNotIn("status: ", self.query("news.example.com", OTHER_CLIENT))
        self.manager.disable()
        self.assertEqual(incoming(OTHER_CLIENT, OTHER_CLIENT_ADDRESSES[0]), "arrived")

    def test_policy_follows_mac_address_and_failed_recovery_closes_network(self):
        self.manager.apply(self.plan)
        self.manager.refresh_addresses(
            [{"mac_address": CLIENT_MAC, "ipv4_address": "192.168.50.11"}]
        )
        result = run_in(
            CLIENT,
            "dig",
            "@8.8.8.8",
            "-b",
            "192.168.50.11",
            "video.example.com",
            "+time=1",
            "+tries=1",
        )
        self.assertIn("status: NXDOMAIN", result.stdout)
        self.executor.fail_restarts = 2
        with self.assertLogs(level="ERROR"), self.assertRaises(CommandExecutionError):
            self.manager.apply(
                replace(self.plan, websites=WebsitePolicy(blocked_domains=("news.example.com",)))
            )
        self.assertNotIn("status: ", self.query("news.example.com", OTHER_CLIENT))
        self.assertIn("closed", self.manager.status()["error"])
        self.manager.restore()
        self.assertIn("203.0.113.7", self.query("news.example.com", OTHER_CLIENT))

    def test_panic_clears_effects_and_reused_dns_routes_without_restarting_dns(self):
        from jittersim.config.models import Device, Impairment, Profile
        from jittersim.engine.codec import plan_to_payload
        from jittersim.engine.compiler import compile_plan
        from jittersim.networking.helper import HelperService

        helper = HelperService(
            NetworkApplier(executor=self.executor, topology=TOPOLOGY),
            active_plan_path=self.directory / "active-plan.json",
            access_manager=self.manager,
        )
        plan = compile_plan(
            devices=(Device("child", "Child", CLIENT_ADDRESS, "slow", CLIENT_MAC),),
            profiles={"slow": Profile("slow", "Slow", upload=Impairment(delay_ms=50))},
            access=self.plan,
        )
        self.assertTrue(
            helper.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})["ok"]
        )

        def same_dns_socket():
            return run_in(
                CLIENT,
                "dig",
                "@192.168.50.1",
                "-b",
                CLIENT_ADDRESS + "#50555",
                "video.example.com",
                "+time=1",
                "+tries=1",
            ).stdout

        self.assertIn("status: NXDOMAIN", same_dns_socket())
        self.executor.fail_restarts = 100
        self.assertTrue(helper.handle_payload({"action": "normal-access"})["ok"])
        self.assertIn("203.0.113.7", same_dns_socket())
        self.assertFalse((self.directory / "active-plan.json").exists())
        self.assertFalse(read_access(self.directory / "policy.json").options.enabled)
        self.assertNotIn("table inet jittersim\n", run_in(PI, "nft", "list", "tables").stdout)
        for interface in ("eth0", "wlan0"):
            self.assertNotIn("htb", run_in(PI, "tc", "qdisc", "show", "dev", interface).stdout)

    def udp_arrives(self, port, *, address="192.168.0.2"):
        """Send one datagram from the router side to a socket bound in the Pi namespace."""

        receiver = subprocess.Popen(
            [
                "ip", "netns", "exec", PI, "python", "-u", "-c",
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                f"s.bind(('0.0.0.0', {port}))\n"
                "s.settimeout(1)\n"
                "print('ready')\n"
                "try:\n"
                "    print(s.recv(32).decode())\n"
                "except socket.timeout:\n"
                "    print('blocked')\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertTrue(select.select([receiver.stdout], [], [], 3)[0])
            self.assertEqual(receiver.stdout.readline().strip(), "ready")
            run_in(
                ROUTER, "python", "-c",
                "import socket; "
                "socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'arrived', "
                f"('{address}', {port}))",
            )
            output, error = receiver.communicate(timeout=3)
            self.assertEqual(receiver.returncode, 0, error)
            return output.strip()
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.communicate(timeout=3)

    def test_name_queries_still_reach_avahi_after_restore_and_apply(self):
        # Avahi answers `<pi-name>.local` and `jittersim.local` on UDP 5353 from the
        # Ethernet side. The access table must leave that port alone while it keeps
        # the filtered resolver's own port private.
        self.assertEqual(self.udp_arrives(5353), "arrived")
        self.manager.restore()
        self.assertEqual(self.udp_arrives(5353), "arrived")
        self.manager.apply(self.plan)
        self.assertEqual(self.udp_arrives(5353), "arrived")
        self.assertEqual(self.udp_arrives(5335), "blocked")
        self.manager.apply(
            replace(
                self.plan,
                mac=MacPolicy("denylist", (), (CLIENT_MAC,)),
                options=AccessOptions(dns_failure_percent=50),
            )
        )
        self.assertEqual(self.udp_arrives(5353), "arrived")
        self.manager.restore()
        self.assertEqual(self.udp_arrives(5353), "arrived")
        self.assertEqual(self.udp_arrives(5335), "blocked")

    def load_control_firewall(self):
        text = (ROOT / "deploy/raspberry-pi/jittersim-control-firewall.nft").read_text()
        path = self.directory / "control.nft"
        path.write_text(
            text.replace("@TEST_INTERFACE@", TOPOLOGY.test_interface).replace(
                "@UPLINK_INTERFACE@", TOPOLOGY.uplink_interface
            )
        )
        run_in(PI, "nft", "-f", str(path))

    def helper(self):
        return HelperService(
            NetworkApplier(executor=self.executor, topology=TOPOLOGY),
            active_plan_path=self.directory / "active-plan.json",
            access_manager=self.manager,
        )

    def test_test_client_dns_works_with_the_control_firewall_and_a_global_block_list(self):
        self.load_control_firewall()
        self.manager.apply(
            AccessPlan(websites=WebsitePolicy(blocked_domains=("video.example.com",)))
        )
        # The control firewall drops mDNS on the test interface. Redirected DNS
        # must not look like mDNS to it, over UDP or TCP.
        for namespace in (CLIENT, OTHER_CLIENT):
            for tcp in (False, True):
                with self.subTest(namespace=namespace, tcp=tcp):
                    self.assertIn("203.0.113.7", self.query("news.example.com", namespace, tcp=tcp))
                    self.assertIn(
                        "status: NXDOMAIN", self.query("video.example.com", namespace, tcp=tcp)
                    )
        self.assertTrue(self.helper().handle_payload({"action": "normal-access"})["ok"])
        for tcp in (False, True):
            self.assertIn(
                "203.0.113.7",
                self.query("video.example.com", resolver="192.168.50.1", tcp=tcp),
            )
        # Plain DNS from the test network still works with the firewall loaded.
        tables = run_in(PI, "nft", "list", "tables").stdout
        self.assertIn("jittersim_control", tables)
        self.assertNotIn("jittersim_access_transition", tables)

    def sign_in_listeners(self):
        """Listen on the dashboard port and on another port in the Pi namespace."""

        listener = subprocess.Popen(
            [
                "ip", "netns", "exec", PI, "python", "-u", "-c",
                "import selectors, socket\n"
                "selector = selectors.DefaultSelector()\n"
                "for port in (8080, 8081):\n"
                "    s = socket.socket()\n"
                "    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                "    s.bind(('0.0.0.0', port))\n"
                "    s.listen(16)\n"
                "    selector.register(s, selectors.EVENT_READ)\n"
                "print('ready')\n"
                "while True:\n"
                "    for key, _ in selector.select():\n"
                "        connection, _ = key.fileobj.accept()\n"
                "        connection.sendall(b'welcome')\n"
                "        connection.close()\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(listener.communicate)
        self.addCleanup(listener.kill)
        self.assertTrue(select.select([listener.stdout], [], [], 3)[0])
        self.assertEqual(listener.stdout.readline().strip(), "ready")

    def reaches(self, namespace, port):
        # A fresh lookup of the Pi's hardware address on every try. A cached entry
        # would hide a block on ARP replies.
        for name in (PI, CLIENT, OTHER_CLIENT):
            run_in(name, "ip", "neigh", "flush", "all", check=False)
        result = run_in(
            namespace,
            "python",
            "-c",
            "import socket\n"
            f"s = socket.create_connection(('192.168.50.1', {port}), timeout=1)\n"
            "print(s.recv(16).decode())\n",
            check=False,
        )
        return result.stdout.strip() == "welcome"

    def test_mac_blocked_client_keeps_the_dashboard_sign_in_and_panic_releases_the_rest(self):
        self.sign_in_listeners()
        self.assertTrue(self.reaches(CLIENT, 8081))
        for mac in (
            MacPolicy("denylist", (), (CLIENT_MAC,)),
            MacPolicy("allowlist", ("02:00:00:00:00:99",), ()),
        ):
            with self.subTest(mode=mac.mode):
                self.manager.apply(replace(self.plan, mac=mac))
                # Sign-in is how a blocked person learns why and gets help. Both
                # directions of the connection need the exception.
                self.assertTrue(self.reaches(CLIENT, 8080))
                self.assertFalse(self.reaches(CLIENT, 8081))
        self.manager.apply(replace(self.plan, mac=MacPolicy("denylist", (), (CLIENT_MAC,))))
        self.assertTrue(self.reaches(OTHER_CLIENT, 8080))
        self.assertTrue(self.reaches(OTHER_CLIENT, 8081))
        # A server that merely uses port 8080 must not reach the blocked device.
        run_in(ROUTER, "ip", "route", "add", "192.168.50.0/24", "via", "192.168.0.2")
        self.assertEqual(self.udp_from_port_8080_arrives(), "blocked")
        self.assertTrue(self.helper().handle_payload({"action": "normal-access"})["ok"])
        self.assertTrue(self.reaches(CLIENT, 8080))
        self.assertTrue(self.reaches(CLIENT, 8081))
        self.assertEqual(self.udp_from_port_8080_arrives(), "arrived")

    def experiment_payload(self):
        from jittersim.config.models import Device, Impairment, Profile
        from jittersim.engine.codec import plan_to_payload
        from jittersim.engine.compiler import compile_plan

        return {
            "action": "apply",
            "plan": plan_to_payload(
                compile_plan(
                    devices=(Device("child", "Child", CLIENT_ADDRESS, "slow", CLIENT_MAC),),
                    profiles={"slow": Profile("slow", "Slow", upload=Impairment(delay_ms=50))},
                )
            ),
        }

    def test_dashboard_sign_in_survives_an_access_change_made_while_an_experiment_runs(self):
        self.sign_in_listeners()
        helper = self.helper()
        self.assertTrue(helper.handle_payload(self.experiment_payload())["ok"])
        # The access table is loaded after the experiment's download chain. Its
        # egress chain must still run first or the reply mark is already cleared.
        self.manager.apply(replace(self.plan, mac=MacPolicy("denylist", (), (CLIENT_MAC,))))
        self.assertTrue(self.reaches(CLIENT, 8080))
        self.assertFalse(self.reaches(CLIENT, 8081))
        self.assertTrue(helper.handle_payload({"action": "normal-access"})["ok"])
        self.assertTrue(self.reaches(CLIENT, 8081))

    def test_dashboard_sign_in_survives_an_experiment_started_after_the_access_rules(self):
        self.sign_in_listeners()
        self.manager.apply(replace(self.plan, mac=MacPolicy("denylist", (), (CLIENT_MAC,))))
        helper = self.helper()
        self.assertTrue(helper.handle_payload(self.experiment_payload())["ok"])
        self.assertTrue(self.reaches(CLIENT, 8080))
        self.assertFalse(self.reaches(CLIENT, 8081))
        self.assertTrue(helper.handle_payload({"action": "normal-access"})["ok"])
        self.assertTrue(self.reaches(CLIENT, 8081))

    def udp_from_port_8080_arrives(self):
        receiver = subprocess.Popen(
            [
                "ip", "netns", "exec", CLIENT, "python", "-u", "-c",
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                "s.bind(('0.0.0.0', 50778))\n"
                "s.settimeout(1)\n"
                "print('ready')\n"
                "try:\n"
                "    print(s.recv(32).decode())\n"
                "except socket.timeout:\n"
                "    print('blocked')\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertTrue(select.select([receiver.stdout], [], [], 3)[0])
            self.assertEqual(receiver.stdout.readline().strip(), "ready")
            run_in(
                ROUTER, "python", "-c",
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                "s.bind(('0.0.0.0', 8080))\n"
                f"s.sendto(b'arrived', ('{CLIENT_ADDRESS}', 50778))\n",
            )
            output, error = receiver.communicate(timeout=3)
            self.assertEqual(receiver.returncode, 0, error)
            return output.strip()
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.communicate(timeout=3)

    def test_panic_removes_every_access_effect_and_a_leftover_guard(self):
        self.assertTrue(
            self.manager.apply(
                replace(
                    self.plan,
                    mac=MacPolicy("denylist", (), (CLIENT_MAC,)),
                    websites=WebsitePolicy(blocked_domains=("news.example.com",)),
                )
            )["enabled"]
        )
        self.assertIn("status: NXDOMAIN", self.query("news.example.com", OTHER_CLIENT))
        # A failed earlier change can leave the guard table behind.
        guard = self.directory / "guard.nft"
        guard.write_text(transition_firewall(TOPOLOGY))
        run_in(PI, "nft", "-f", str(guard))
        self.assertIn("jittersim_access_transition", run_in(PI, "nft", "list", "tables").stdout)
        self.executor.fail_restarts = 100

        self.assertTrue(self.helper().handle_payload({"action": "normal-access"})["ok"])

        ruleset = run_in(PI, "nft", "list", "ruleset").stdout
        self.assertNotIn("jittersim_access_transition", ruleset)
        for leftover in ("redirect", "ether saddr", "ether daddr", "reject", "numgen"):
            self.assertNotIn(leftover, ruleset)
        self.assertNotIn("chain outgoing", ruleset)
        self.assertNotIn("table inet jittersim\n", ruleset)
        self.assertFalse(read_access(self.directory / "policy.json").options.enabled)
        # No filtering resolver keeps running and plain DNS answers everyone.
        self.assertIsNone(self.executor.process)
        for namespace in (CLIENT, OTHER_CLIENT):
            self.assertIn(
                "203.0.113.7",
                self.query("news.example.com", namespace, resolver="192.168.50.1"),
            )
        self.assertEqual(self.udp_arrives(5353), "arrived")

    def test_real_hostapd_control_replaces_and_clears_admission_lists(self):
        control = self.directory / "hostapd"
        config = self.directory / "hostapd.conf"
        config.write_text(
            f"interface=wlan0\ndriver=none\nssid=AdmissionTest\nctrl_interface={control}\n"
        )
        daemon = subprocess.Popen(
            ["ip", "netns", "exec", PI, "hostapd", str(config)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                if (control / "wlan0").is_socket():
                    break
                if daemon.poll() is not None:
                    self.fail("hostapd failed to start in controller-only mode")
                time.sleep(0.02)
            self.manager.hostapd_path = control
            self.assertTrue(self.manager.admission_supported())
            plan = replace(
                self.plan,
                options=AccessOptions(reject_wifi_joins=True),
                mac=MacPolicy("denylist", (), (CLIENT_MAC,)),
            )
            self.manager._admission(plan)

            def show(kind):
                return run_in(
                    PI, "hostapd_cli", "-p", str(control), "-i", "wlan0", kind + "_acl", "SHOW"
                ).stdout

            self.assertIn(CLIENT_MAC, show("deny"))
            plan = replace(plan, mac=MacPolicy("denylist", (), ("02:00:00:00:00:11",)))
            self.manager._admission(plan)
            self.assertNotIn(CLIENT_MAC, show("deny"))
            self.assertIn("02:00:00:00:00:11", show("deny"))
            self.manager._admission(replace(plan, options=AccessOptions(enabled=False)))
            self.assertNotIn("02:00", show("deny"))
            self.assertNotIn("02:00", show("accept"))
        finally:
            daemon.terminate()
            daemon.wait(timeout=5)
