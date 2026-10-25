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

from jittersim.config.models import (
    AccessOptions,
    Device,
    Impairment,
    MacPolicy,
    Profile,
    SiteBlock,
    WebsitePolicy,
)
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
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
    OTHER_CLIENT_MAC,
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

    def test_protected_mac_skips_global_dns_blocks_but_keeps_allow_only(self):
        plan = AccessPlan(
            websites=WebsitePolicy("allow_only", ("news.example.com",), ("news.example.com",)),
            protected_mac_addresses=(OTHER_CLIENT_MAC,),
        )
        self.manager.apply(plan)
        for tcp in (False, True):
            self.assertIn("203.0.113.7", self.query("news.example.com", OTHER_CLIENT, tcp=tcp))
            self.assertIn("status: NXDOMAIN", self.query("news.example.com", tcp=tcp))
            for namespace in (CLIENT, OTHER_CLIENT):
                self.assertIn("status: NXDOMAIN", self.query("notvideo.example.com", namespace, tcp=tcp))
        # Removing protection changes DNS routes even for existing UDP sockets.
        self.manager.apply(replace(plan, protected_mac_addresses=()))
        self.assertIn("status: NXDOMAIN", self.query("news.example.com", OTHER_CLIENT))

    def test_protected_dns_reuses_no_stale_ip_grant_and_private_views_refuse_direct_queries(self):
        from jittersim.networking.access import _dns_views

        plan = AccessPlan(
            websites=WebsitePolicy(blocked_domains=("news.example.com",)),
            # This saved address now belongs to the protected MAC, not to this
            # stale ordinary record. The exemption must use the MAC on the wire.
            clients=(AccessClient("stale", OTHER_CLIENT_ADDRESSES[0], CLIENT_MAC),),
            protected_mac_addresses=(OTHER_CLIENT_MAC,),
        )
        self.manager.apply(plan)
        for tcp in (False, True):
            self.assertIn("203.0.113.7", self.query("news.example.com", OTHER_CLIENT, tcp=tcp))
            self.assertIn("status: NXDOMAIN", self.query("news.example.com", tcp=tcp))
        _, ports = _dns_views(plan)
        port = ports[plan.blocked_domains(client_mac=OTHER_CLIENT_MAC)]
        run_in(ROUTER, "ip", "route", "add", "192.168.50.0/24", "via", "192.168.0.2")
        for namespace in (CLIENT, OTHER_CLIENT, ROUTER):
            for listener in (5335, port):
                for tcp in (False, True):
                    response = run_in(namespace, "dig", "@192.168.50.1", "-p", str(listener),
                                      "news.example.com", "+time=1", "+tries=1",
                                      *(("+tcp",) if tcp else ()), check=False)
                    self.assertNotIn("status: NOERROR", response.stdout)
                    self.assertNotIn("203.0.113.7", response.stdout)

    def test_protected_dns_keeps_site_rules_mac_denies_and_dns_failures(self):
        plan = replace(self.plan, protected_mac_addresses=(CLIENT_MAC,),
                       websites=WebsitePolicy(blocked_domains=("news.example.com",)))
        self.manager.apply(plan)
        self.assertIn("203.0.113.7", self.query("news.example.com"))
        self.assertIn("status: NXDOMAIN", self.query("video.example.com"))
        for changed in (replace(plan, options=AccessOptions(dns_failure_percent=100)),
                        replace(plan, mac=MacPolicy("denylist", (), (CLIENT_MAC,)))):
            self.manager.apply(changed)
            for tcp in (False, True):
                response = self.query("news.example.com", tcp=tcp)
                self.assertNotIn("203.0.113.7", response)

    def test_protected_optional_ads_keep_allow_only_and_remove_only_global_site_blocks(self):
        from jittersim.networking.adblocking import AdRouting, filtering_config

        plan = AccessPlan(
            websites=WebsitePolicy("allow_only", ("news.example.com", "video.example.com"),
                                   ("news.example.com",)),
            protected_mac_addresses=(OTHER_CLIENT_MAC,),
        )
        self.manager.apply(plan)
        config = self.directory / "ads.conf"
        config.write_bytes(filtering_config(("video.example.com",), (), plan, dns_user="unbound"))
        process = subprocess.Popen(["ip", "netns", "exec", PI, "unbound", "-d", "-c", str(config)],
                                   stdout=self.executor.log, stderr=self.executor.log)
        self.addCleanup(lambda: process.wait(timeout=5))
        self.addCleanup(process.terminate)
        routing = AdRouting(TOPOLOGY.test_interface, executor=self.executor)
        routing.initialize()
        routing.refresh((OTHER_CLIENT_MAC,))
        deadline = time.monotonic() + 5
        while True:
            response = self.query("news.example.com", OTHER_CLIENT, resolver="192.168.50.1")
            if "203.0.113.7" in response or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        self.assertIn("203.0.113.7", response)
        for tcp in (False, True):
            self.assertIn("status: NXDOMAIN", self.query("video.example.com", OTHER_CLIENT,
                                                        tcp=tcp, resolver="192.168.50.1"))
            self.assertIn("status: NXDOMAIN", self.query("notvideo.example.com", OTHER_CLIENT,
                                                        tcp=tcp, resolver="192.168.50.1"))
            self.assertIn("status: NXDOMAIN", self.query("news.example.com", tcp=tcp))

    def test_protected_reaches_blocked_address_but_never_an_address_outside_allow_only(self):
        from ipaddress import IPv4Address

        for address in ("203.0.113.8", "203.0.113.9"):
            run_in(ROUTER, "ip", "addr", "add", address + "/32", "dev", "lo")
            run_in(PI, "ip", "route", "add", address + "/32", "via", "192.168.0.1")
        plan = compile_plan(devices=(), profiles={}, protected_mac_addresses=(OTHER_CLIENT_MAC,),
                            global_blocked_addresses=(IPv4Address("203.0.113.8"),),
                            global_allowed_addresses=(IPv4Address("203.0.113.8"),))
        applier = NetworkApplier(executor=self.executor, topology=TOPOLOGY)
        applier.apply(plan=plan)
        for namespace, address, allowed in ((OTHER_CLIENT, "203.0.113.8", True),
                                             (CLIENT, "203.0.113.8", False),
                                             (OTHER_CLIENT, "203.0.113.9", False)):
            response = run_in(namespace, "ping", "-c", "1", "-W", "1", address, check=False)
            self.assertEqual(response.returncode == 0, allowed, response.stdout + response.stderr)

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

    def test_the_global_block_list_covers_subdomains_and_unblock_brings_both_back(self):
        # Block globally on the Traffic page writes this list. The Pi line's engine
        # matched exact names. The filtered resolver also refuses every name below.
        plan = AccessPlan(
            websites=WebsitePolicy(blocked_domains=("video.example.com", "news.example.com")),
            clients=(AccessClient("child", CLIENT_ADDRESS, CLIENT_MAC, ()),),
        )
        self.manager.apply(plan)
        for namespace in (CLIENT, OTHER_CLIENT):
            for name in ("video.example.com", "cdn.video.example.com", "news.example.com"):
                self.assertIn("status: NXDOMAIN", self.query(name, namespace))
            self.assertIn("203.0.113.7", self.query("notvideo.example.com", namespace))
        # Unblock removes one exact entry. The name and the names below it resolve again.
        self.manager.apply(
            replace(plan, websites=WebsitePolicy(blocked_domains=("news.example.com",)))
        )
        for namespace in (CLIENT, OTHER_CLIENT):
            for name in ("video.example.com", "cdn.video.example.com"):
                self.assertIn("203.0.113.7", self.query(name, namespace))
            self.assertIn("status: NXDOMAIN", self.query("news.example.com", namespace))
        # Unblocking the last entry leaves no DNS rule. Plain DNS answers everything.
        self.manager.apply(replace(plan, websites=WebsitePolicy()))
        for namespace in (CLIENT, OTHER_CLIENT):
            for name in ("video.example.com", "cdn.video.example.com", "news.example.com"):
                self.assertIn("203.0.113.7", self.query(name, namespace, resolver="192.168.50.1"))

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

        for tcp in (False, True):
            management_query = run_in(
                PI,
                "dig",
                "@192.168.50.1",
                "news.example.com",
                "+time=1",
                "+tries=1",
                *(("+tcp",) if tcp else ()),
                check=False,
            )
            self.assertEqual(management_query.returncode, 0, management_query.stderr)
            self.assertIn("203.0.113.7", management_query.stdout)

        recovered = replace(self.plan, options=AccessOptions(dns_failure_percent=0))
        self.manager.apply(recovered)

        def check_recovered_answers():
            self.assertEqual(read_access(self.directory / "policy.json"), recovered)
            for tcp in (False, True):
                for namespace in (CLIENT, OTHER_CLIENT):
                    self.assertIn(
                        "203.0.113.7", self.query("news.example.com", namespace, tcp=tcp)
                    )
                for name in ("video.example.com", "cdn.video.example.com"):
                    self.assertIn("status: NXDOMAIN", self.query(name, tcp=tcp))
                    self.assertIn("203.0.113.7", self.query(name, OTHER_CLIENT, tcp=tcp))

        # Zero removes only the injected failures. The child's saved site block stays active.
        check_recovered_answers()
        self.executor.stop()
        self.manager.restore()
        check_recovered_answers()

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

    def integrated_mac_profile(self):
        access = AccessPlan(
            mac=MacPolicy("denylist", (), (CLIENT_MAC,)),
            clients=(AccessClient("child", CLIENT_ADDRESS, CLIENT_MAC),),
        )
        plan = compile_plan(
            devices=(Device("child", "Child", CLIENT_ADDRESS, "slow", CLIENT_MAC),),
            profiles={
                "slow": Profile(
                    "slow", "Slow", upload=Impairment(delay_ms=25),
                    download=Impairment(delay_ms=35),
                )
            },
            mac_policy=access.mac,
            access=access,
        )
        helper = HelperService(
            NetworkApplier(executor=self.executor, topology=TOPOLOGY),
            active_plan_path=self.directory / "active-plan.json",
            access_manager=self.manager,
        )
        return helper, plan

    def profile_queue_settings(self):
        settings = {}
        for interface in ("eth0", "wlan0"):
            queues = json.loads(run_in(PI, "tc", "-j", "qdisc", "show", "dev", interface).stdout)
            settings[interface] = sorted(
                [
                    {key: queue[key] for key in ("kind", "handle", "parent", "options")
                     if key in queue}
                    for queue in queues
                ],
                key=lambda queue: queue["handle"],
            )
        return settings

    def client_datagram_arrives(self, *, download):
        receiver_namespace = CLIENT if download else ROUTER
        sender_namespace = ROUTER if download else CLIENT
        destination = CLIENT_ADDRESS if download else "192.168.0.1"
        sender_address = "192.168.0.1" if download else CLIENT_ADDRESS
        receiver = subprocess.Popen(
            [
                "ip", "netns", "exec", receiver_namespace, "python", "-u", "-c",
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                "s.bind(('0.0.0.0', 50888))\n"
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
                sender_namespace, "python", "-c",
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                f"s.bind(('{sender_address}', 0))\n"
                f"s.sendto(b'arrived', ('{destination}', 50888))\n",
            )
            output, error = receiver.communicate(timeout=3)
            self.assertEqual(receiver.returncode, 0, error)
            return output.strip() == "arrived"
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.communicate(timeout=3)

    def test_household_mac_disable_releases_both_directions_without_replacing_queues(self):
        run_in(ROUTER, "ip", "route", "add", "192.168.50.0/24", "via", "192.168.0.2")
        helper, plan = self.integrated_mac_profile()
        answer = helper.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
        self.assertTrue(answer["ok"], answer)
        published = (self.directory / "active-plan.json").read_bytes()
        queues = self.profile_queue_settings()
        for direction in (False, True):
            self.assertFalse(self.client_datagram_arrives(download=direction))
        for settings in queues.values():
            self.assertTrue(any(queue["kind"] == "netem" for queue in settings))

        disabled = replace(plan.access, options=AccessOptions(enabled=False))
        answer = helper.handle_payload({"action": "access-apply", "policy": disabled.payload()})

        self.assertTrue(answer["ok"], answer)
        self.assertEqual((self.directory / "active-plan.json").read_bytes(), published)
        self.assertEqual(self.profile_queue_settings(), queues)
        self.assertFalse(self.manager.applied.options.enabled)
        self.assertEqual(self.manager.applied.mac, plan.mac_policy)
        arrived = tuple(
            self.client_datagram_arrives(download=direction) for direction in (False, True)
        )
        self.assertEqual(arrived, (True, True), "Disabling access must release upload and download")

    def test_household_mac_blocks_stay_off_after_panic_and_profile_restart(self):
        run_in(ROUTER, "ip", "route", "add", "192.168.50.0/24", "via", "192.168.0.2")
        helper, plan = self.integrated_mac_profile()
        answer = helper.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
        self.assertTrue(answer["ok"], answer)
        before_panic = helper.handle_payload({"action": "control-revision"})
        self.assertTrue(before_panic["ok"], before_panic)
        self.assertTrue(helper.handle_payload({"action": "normal-access"})["ok"])
        self.assertFalse(self.manager.applied.options.enabled)
        self.assertEqual(self.manager.applied.mac, plan.mac_policy)

        restarted = replace(plan, access=self.manager.applied)
        payload = {"action": "apply", "plan": plan_to_payload(restarted)}
        for intent in ({}, {"expected_control_revision": before_panic["control_revision"]}):
            refused = helper.handle_payload({**payload, **intent})
            self.assertFalse(refused["ok"], refused)
            self.assertEqual(refused["reason"], "control-changed")
        current = helper.handle_payload({"action": "control-revision"})
        self.assertTrue(current["ok"], current)
        self.assertFalse(current["recovery_required"])
        self.assertNotEqual(current["control_revision"], before_panic["control_revision"])
        payload["expected_control_revision"] = current["control_revision"]
        implicit = helper.handle_payload(payload)
        self.assertFalse(implicit["ok"], implicit)
        self.assertIn("Panic stopped profiles", implicit["error"])
        self.assertFalse((self.directory / "active-plan.json").exists())
        for settings in self.profile_queue_settings().values():
            self.assertFalse(any(queue["kind"] == "netem" for queue in settings))
        for direction in (False, True):
            self.assertTrue(self.client_datagram_arrives(download=direction))
        answer = helper.handle_payload({**payload, "explicit_apply": True})

        self.assertTrue(answer["ok"], answer)
        self.assertFalse(self.manager.applied.options.enabled)
        self.assertEqual(self.manager.applied.mac, plan.mac_policy)
        for settings in self.profile_queue_settings().values():
            self.assertTrue(any(queue["kind"] == "netem" for queue in settings))
        arrived = tuple(
            self.client_datagram_arrives(download=direction) for direction in (False, True)
        )
        self.assertEqual(
            arrived, (True, True), "Starting profiles must keep household MAC blocks off"
        )

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

    def dhcp_answers(self, namespace=CLIENT):
        """A DHCP-shaped exchange: client port 68 to the Pi's port 67 and back."""

        server = subprocess.Popen(
            [
                "ip", "netns", "exec", PI, "python", "-u", "-c",
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                "s.bind(('0.0.0.0', 67))\n"
                "s.settimeout(3)\n"
                "print('ready')\n"
                "data, peer = s.recvfrom(64)\n"
                "s.sendto(b'offer', peer)\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(lambda: server.poll() is None and server.kill())
        self.assertTrue(select.select([server.stdout], [], [], 3)[0])
        self.assertEqual(server.stdout.readline().strip(), "ready")
        result = run_in(
            namespace, "python", "-c",
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "s.bind(('0.0.0.0', 68))\n"
            "s.settimeout(1.5)\n"
            "s.sendto(b'discover', ('192.168.50.1', 67))\n"
            "try:\n"
            "    print(s.recv(16).decode())\n"
            "except socket.timeout:\n"
            "    print('blocked')\n",
            check=False,
        )
        return result.stdout.strip()

    def test_a_guard_left_by_a_failed_recovery_keeps_sign_in_and_dhcp(self):
        # Block globally sends the household rules at once. With Unbound failing the
        # change and its recovery both fail and the guard stays. A person on the
        # test Wi-Fi must still get an address and reach the dashboard sign-in.
        self.sign_in_listeners()
        self.manager.apply(self.plan)
        self.executor.fail_restarts = 100
        with self.assertLogs(level="ERROR"), self.assertRaises(CommandExecutionError):
            self.manager.apply(
                replace(self.plan, websites=WebsitePolicy(blocked_domains=("news.example.com",)))
            )
        self.assertIn("jittersim_access_transition", run_in(PI, "nft", "list", "tables").stdout)
        self.assertTrue(self.reaches(CLIENT, 8080))
        self.assertEqual(self.dhcp_answers(), "offer")
        # Everything else stays closed behind the guard.
        self.assertFalse(self.reaches(CLIENT, 8081))
        self.assertNotIn("status: ", self.query("news.example.com", OTHER_CLIENT))

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

    def datagram_arrives_between_clients(self, source_port):
        """Send one datagram from CLIENT to OTHER_CLIENT through the Pi."""

        receiver = subprocess.Popen(
            [
                "ip", "netns", "exec", OTHER_CLIENT, "python", "-u", "-c",
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                "s.bind(('0.0.0.0', 9999))\n"
                "s.settimeout(2)\n"
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
                CLIENT, "python", "-c",
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                f"s.bind(('{CLIENT_ADDRESS}', {source_port}))\n"
                f"s.sendto(b'arrived', ('{OTHER_CLIENT_ADDRESSES[0]}', 9999))\n",
            )
            output, error = receiver.communicate(timeout=5)
            self.assertEqual(receiver.returncode, 0, error)
            return output.strip()
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.communicate(timeout=3)

    def test_the_control_firewall_drops_a_forwarded_packet_that_poses_as_a_dns_answer(self):
        # Follow-up F1 of the security audit of 2026-10-18. The DNS watcher reads the frames
        # the Pi sends out of the test interface. A device could send a packet from UDP port
        # 53 to another device through the Pi (or to itself) and pass for a forwarded answer.
        self.load_control_firewall()
        # The packet must really cross the Pi: the client routes the other client's address
        # through it, and the Pi does not tell the client to send it directly instead.
        run_in(
            PI, "sh", "-c",
            "echo 0 > /proc/sys/net/ipv4/conf/all/send_redirects; "
            "echo 0 > /proc/sys/net/ipv4/conf/wlan0/send_redirects",
        )
        run_in(
            CLIENT, "ip", "route", "add", f"{OTHER_CLIENT_ADDRESSES[0]}/32",
            "via", "192.168.50.1", "dev", "c0",
        )
        # A real answer of the Pi's own resolver still reaches the client. It leaves through
        # the output chain, not through the forward chain.
        self.assertIn(
            "203.0.113.7", self.query("news.example.com", CLIENT, resolver="192.168.50.1")
        )
        # An ordinary datagram makes the trip, so the lab path works.
        self.assertEqual(self.datagram_arrives_between_clients(40000), "arrived")
        # The same datagram from source port 53 is dropped by the new rule.
        self.assertEqual(self.datagram_arrives_between_clients(53), "blocked")
        rules = run_in(PI, "nft", "list", "chain", "inet", "jittersim_control", "forward").stdout
        self.assertRegex(rules, r"udp sport (?:53|domain) counter packets [1-9]")

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
