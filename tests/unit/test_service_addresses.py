import tempfile
import threading
import unittest
from ipaddress import IPv4Address
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceListRule
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import (
    HelperClient,
    HelperClientError,
    HelperService,
    UnixHelperServer,
)
from jittersim.networking.service_addresses import ServiceAddressBook
from jittersim.networking.topology import Topology

SLOW = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=300))


def ip(value: str) -> IPv4Address:
    return IPv4Address(value)


def list_plan(*list_ids: str):
    return compile_plan(
        devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
        profiles={"slow": SLOW},
        service_list_rules=tuple(
            ServiceListRule(
                list_id=list_id,
                domains=(f"{list_id}.example",),
                mark=0,
                upload_impairment=SLOW.upload,
                download_impairment=SLOW.download,
            )
            for list_id in (list_ids or ("streaming",))
        ),
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class ServiceAddressBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.book = ServiceAddressBook(ttl_seconds=60, max_per_list=3, clock=self.clock)

    def test_only_new_addresses_are_reported_as_fresh(self) -> None:
        fresh, evicted = self.book.record("streaming", [ip("203.0.113.1"), ip("203.0.113.2")])
        self.assertEqual(fresh, (ip("203.0.113.1"), ip("203.0.113.2")))
        self.assertEqual(evicted, ())

        fresh, _ = self.book.record("streaming", [ip("203.0.113.2"), ip("203.0.113.3")])
        self.assertEqual(fresh, (ip("203.0.113.3"),))

    def test_addresses_expire_unless_they_are_seen_again(self) -> None:
        self.book.record("streaming", [ip("203.0.113.1"), ip("203.0.113.2")])
        self.clock.now += 40
        self.book.record("streaming", [ip("203.0.113.2")])
        self.clock.now += 40

        self.assertEqual(self.book.expire(), {"streaming": (ip("203.0.113.1"),)})
        self.assertEqual(self.book.snapshot(["streaming"]), {"streaming": (ip("203.0.113.2"),)})
        self.assertEqual(self.book.expire(), {})

    def test_the_oldest_address_is_evicted_when_a_list_is_full(self) -> None:
        for number in range(1, 4):
            self.clock.now += 1
            self.book.record("streaming", [ip(f"203.0.113.{number}")])
        self.clock.now += 1

        fresh, evicted = self.book.record("streaming", [ip("203.0.113.9")])

        self.assertEqual(fresh, (ip("203.0.113.9"),))
        self.assertEqual(evicted, (ip("203.0.113.1"),))
        self.assertEqual(
            self.book.snapshot(["streaming"]),
            {"streaming": (ip("203.0.113.2"), ip("203.0.113.3"), ip("203.0.113.9"))},
        )

    def test_a_batch_larger_than_the_limit_keeps_the_newest(self) -> None:
        fresh, evicted = self.book.record(
            "streaming", [ip(f"203.0.113.{number}") for number in range(1, 6)]
        )

        self.assertEqual(len(self.book.snapshot(["streaming"])["streaming"]), 3)
        self.assertEqual(
            set(fresh) - set(evicted), set(self.book.snapshot(["streaming"])["streaming"])
        )

    def test_discard_forgets_addresses_that_never_reached_the_kernel(self) -> None:
        self.book.record("streaming", [ip("203.0.113.1"), ip("203.0.113.2")])

        self.book.discard("streaming", [ip("203.0.113.1")])

        self.assertEqual(self.book.snapshot(["streaming"]), {"streaming": (ip("203.0.113.2"),)})

    def test_snapshot_only_returns_the_requested_lists(self) -> None:
        self.book.record("streaming", [ip("203.0.113.1")])
        self.book.record("work", [ip("203.0.113.2")])

        self.assertEqual(self.book.snapshot(["work"]), {"work": (ip("203.0.113.2"),)})
        self.assertEqual(self.book.snapshot(["streaming"]), {"streaming": (ip("203.0.113.1"),)})
        self.assertEqual(self.book.snapshot(["missing"]), {})


class ServiceAddressHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = FakeExecutor()
        self.clock = Clock()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.plan_path = Path(self.directory.name) / "active-plan.json"
        self.service = HelperService(
            NetworkApplier(
                executor=self.executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            active_plan_path=self.plan_path,
            service_address_clock=self.clock,
        )

    def apply(self, plan):
        response = self.service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
        self.assertTrue(response["ok"], response)

    def send(self, addresses):
        return self.service.handle_payload({"action": "service-addresses", "addresses": addresses})

    def nft_scripts(self):
        return [
            command.stdin
            for command in self.executor.commands
            if command.executable == "nft" and command.stdin
        ]

    def test_the_helper_announces_service_list_support(self) -> None:
        self.assertIs(
            self.service.handle_payload({"action": "capabilities"})["service_lists"], True
        )

    def test_nothing_is_added_while_no_plan_has_lists(self) -> None:
        self.assertEqual(self.send({"streaming": ["203.0.113.1"]})["added"], 0)
        self.apply(compile_plan(devices=(), profiles={}))
        self.executor.commands.clear()

        self.assertEqual(self.send({"streaming": ["203.0.113.1"]})["added"], 0)
        self.assertEqual(self.executor.commands, [])

    def test_addresses_join_the_set_of_the_active_plan(self) -> None:
        self.apply(list_plan("streaming", "work"))
        self.executor.commands.clear()

        response = self.send({"streaming": ["203.0.113.2", "203.0.113.1"], "work": ["203.0.113.7"]})

        self.assertEqual(response, {"ok": True, "action": "service-addresses", "added": 3})
        (script,) = self.nft_scripts()
        self.assertEqual(
            script.splitlines(),
            [
                "add element inet jittersim svc_2 { 203.0.113.1, 203.0.113.2 }",
                "add element inet jittersim svc_3 { 203.0.113.7 }",
            ],
        )

    def test_addresses_already_known_are_not_sent_to_the_kernel_again(self) -> None:
        self.apply(list_plan())
        self.send({"streaming": ["203.0.113.1"]})
        self.executor.commands.clear()

        response = self.send({"streaming": ["203.0.113.1"]})

        self.assertEqual(response["added"], 0)
        self.assertEqual(self.executor.commands, [])

    def test_unsafe_addresses_and_unknown_lists_are_ignored(self) -> None:
        self.apply(list_plan())
        self.executor.commands.clear()

        response = self.send(
            {
                "streaming": [
                    "127.0.0.1",
                    "192.168.50.1",
                    "10.1.2.3",
                    "172.16.0.9",
                    "169.254.1.1",
                    "224.0.0.1",
                    "0.0.0.0",
                    "240.0.0.1",
                    "203.0.113.5",
                ],
                "other": ["203.0.113.6"],
            }
        )

        self.assertEqual(response["added"], 1)
        self.assertEqual(self.nft_scripts(), ["add element inet jittersim svc_2 { 203.0.113.5 }\n"])

    def test_malformed_requests_are_rejected_before_any_command_runs(self) -> None:
        self.apply(list_plan())
        self.executor.commands.clear()
        for addresses in (
            "203.0.113.1",
            {"streaming": "203.0.113.1"},
            {"streaming": [5]},
            {"streaming": ["not-an-address"]},
            {"streaming": ["2001:db8::1"]},
            {"Bad Id": ["203.0.113.1"]},
            {"streaming": [f"198.51.{n // 250}.{n % 250}" for n in range(1025)]},
            {f"list-{n}": [] for n in range(16)},
        ):
            with self.subTest(addresses=str(addresses)[:50]):
                response = self.send(addresses)
                self.assertFalse(response["ok"])
        self.assertEqual(self.executor.commands, [])

    def test_a_new_table_starts_with_the_addresses_learned_so_far(self) -> None:
        self.apply(list_plan())
        self.send({"streaming": ["203.0.113.1", "203.0.113.2"]})
        self.executor.commands.clear()

        self.apply(list_plan())

        (script,) = self.nft_scripts()
        self.assertIn("add element inet jittersim svc_2 { 203.0.113.1, 203.0.113.2 }", script)
        self.assertLess(script.index("add set"), script.index("add element"))
        self.assertLess(script.index("add element"), script.index("@svc_2"))

    def test_a_plan_without_the_list_does_not_replay_its_addresses(self) -> None:
        self.apply(list_plan("streaming"))
        self.send({"streaming": ["203.0.113.1"]})
        self.executor.commands.clear()

        self.apply(list_plan("work"))

        self.assertNotIn("203.0.113.1", self.nft_scripts()[0])

    def test_expired_addresses_are_removed_and_not_replayed(self) -> None:
        self.apply(list_plan())
        self.send({"streaming": ["203.0.113.1"]})
        self.clock.now += 6 * 3600 + 1
        self.executor.commands.clear()

        response = self.send({"streaming": ["203.0.113.2"]})

        self.assertEqual(response["added"], 1)
        deletes = [
            c
            for c in self.executor.commands
            if c.arguments[:2] == ("-f", "-") and "delete element" in (c.stdin or "")
        ]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0].stdin, "delete element inet jittersim svc_2 { 203.0.113.1 }\n")
        self.assertTrue(deletes[0].ignore_failure)
        self.executor.commands.clear()
        self.apply(list_plan())
        self.assertNotIn("203.0.113.1", self.nft_scripts()[0])
        self.assertIn("203.0.113.2", self.nft_scripts()[0])

    def test_reset_stops_updates_until_the_next_plan(self) -> None:
        self.apply(list_plan())
        self.assertTrue(self.service.handle_payload({"action": "reset"})["ok"])
        self.executor.commands.clear()

        self.assertEqual(self.send({"streaming": ["203.0.113.1"]})["added"], 0)
        self.assertEqual(self.executor.commands, [])

    def test_a_restarted_helper_finds_the_plan_that_is_still_applied(self) -> None:
        self.apply(list_plan())
        restarted = HelperService(
            NetworkApplier(
                executor=self.executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            active_plan_path=self.plan_path,
        )
        self.executor.commands.clear()

        response = restarted.handle_payload(
            {"action": "service-addresses", "addresses": {"streaming": ["203.0.113.1"]}}
        )

        self.assertEqual(response["added"], 1)

    def test_a_failed_kernel_update_is_reported_and_not_remembered(self) -> None:
        self.apply(list_plan())
        real_run = self.executor.run

        def failing(command):
            if command.stdin and "add element" in command.stdin:
                from jittersim.networking.commands import CommandResult

                return CommandResult(return_code=1, stderr="private detail")
            return real_run(command)

        self.executor.run = failing

        with self.assertLogs("jittersim.networking.helper", level="ERROR"):
            response = self.send({"streaming": ["203.0.113.1"]})

        self.assertEqual(response, {"ok": False, "error": "service address update failed"})
        self.executor.run = real_run
        self.assertEqual(self.send({"streaming": ["203.0.113.1"]})["added"], 1)

    def test_a_busy_helper_asks_the_caller_to_retry(self) -> None:
        self.apply(list_plan())
        self.service._network_lock.acquire()
        try:
            response = self.send({"streaming": ["203.0.113.1"]})
        finally:
            self.service._network_lock.release()

        self.assertEqual(response["reason"], "helper-busy")


class ServiceAddressClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.executor = FakeExecutor()
        self.service = HelperService(
            NetworkApplier(
                executor=self.executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )
        self.socket_path = Path(self.directory.name) / "helper.sock"
        self.server = UnixHelperServer(self.socket_path, self.service)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_the_client_sends_addresses_and_returns_the_count(self) -> None:
        client = HelperClient(socket_path=self.socket_path)
        client.apply(plan=list_plan())

        added = client.service_addresses({"streaming": [ip("203.0.113.1"), "203.0.113.2"]})

        self.assertEqual(added, 2)

    def test_the_client_rejects_a_reply_that_is_not_a_count(self) -> None:
        client = HelperClient(socket_path=self.socket_path)
        client._request = lambda payload, **kwargs: {"ok": True, "added": "many"}

        with self.assertRaises(HelperClientError):
            client.service_addresses({"streaming": ["203.0.113.1"]})

    def test_applying_a_plan_with_lists_needs_a_helper_that_supports_them(self) -> None:
        client = HelperClient(socket_path=self.socket_path)
        client._request = lambda payload, **kwargs: {"ok": True, "general_profile": True}

        with self.assertRaisesRegex(HelperClientError, "Update the Pi helper"):
            client.apply(plan=list_plan())


if __name__ == "__main__":
    unittest.main()
