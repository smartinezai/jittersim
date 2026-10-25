"""The global block list at its limits: the helper request size and the address cap.

Protected-device exemptions are covered in test_protected_global_block.py.
"""

import json
import logging
import unittest
from ipaddress import IPv4Address

from jittersim.config.models import (
    MAX_GLOBAL_BLOCKED_CHARACTERS,
    MAX_GLOBAL_BLOCKED_DOMAINS,
    MAX_LISTED_DOMAINS,
    Device,
    Impairment,
    MacPolicy,
    Profile,
    WebsitePolicy,
)
from jittersim.engine import compiler
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.engine.codec import plan_from_payload, plan_to_payload
from jittersim.engine.compiler import MAX_DEVICES_PER_PLAN, MAX_GLOBAL_BLOCKED_ADDRESSES, compile_plan
from jittersim.networking.helper import MAX_MESSAGE_BYTES
from jittersim.networking.topology import Topology

TOPOLOGY = Topology("eth0", "wlan0")
PROFILE = Profile(id="p", name="P", upload=Impairment(delay_ms=50), download=Impairment(delay_ms=50))
PROTECTED_MAC = "02:00:00:00:00:99"


def longest_block_list():
    """The most names, as long as the character limit allows."""

    length = MAX_GLOBAL_BLOCKED_CHARACTERS // MAX_GLOBAL_BLOCKED_DOMAINS
    suffix = ".example.com"
    return tuple(
        f"{n:04d}{'a' * (length - 4 - len(suffix))}{suffix}" for n in range(MAX_GLOBAL_BLOCKED_DOMAINS)
    )


def addresses(count):
    return tuple(IPv4Address(0x0A000000 + n) for n in range(count))


class GlobalBlockListLimitTests(unittest.TestCase):
    def test_the_largest_list_fits_one_helper_request_with_room_to_spare(self) -> None:
        names = longest_block_list()
        self.assertEqual(sum(map(len, names)), MAX_GLOBAL_BLOCKED_CHARACTERS)
        devices = [
            Device(f"d{n}", f"d{n}", f"192.168.50.{10 + n}", "p", f"02:00:00:00:01:{n:02x}")
            for n in range(MAX_DEVICES_PER_PLAN)
        ]
        websites = WebsitePolicy(
            mode="allow_only",
            allowed_domains=tuple(f"allowed{n}.example.com" for n in range(MAX_LISTED_DOMAINS)),
            blocked_domains=names,
        )
        access = AccessPlan(
            websites=websites,
            mac=MacPolicy("denylist", (), tuple(f"02:00:00:00:02:{n:02x}" for n in range(200))),
            clients=tuple(AccessClient(d.id, str(d.ipv4_address), d.mac_address) for d in devices),
        )
        plan = compile_plan(
            devices=devices,
            profiles={"p": PROFILE},
            global_blocked_addresses=addresses(MAX_GLOBAL_BLOCKED_ADDRESSES),
            access=access,
        )
        frame = (json.dumps({"action": "apply", "plan": plan_to_payload(plan)}, sort_keys=True)
                 + "\n").encode()

        # The helper refuses a request line over MAX_MESSAGE_BYTES. Keep a quarter free
        # for site rules, service rules and the rest of a busy plan.
        self.assertLess(len(frame), MAX_MESSAGE_BYTES * 3 // 4)
        decoded = plan_from_payload(json.loads(frame)["plan"])
        self.assertEqual(decoded.access.websites.blocked_domains, names)

    def test_a_plan_blocks_at_most_the_address_cap_and_says_so_once(self) -> None:
        many = addresses(MAX_GLOBAL_BLOCKED_ADDRESSES + 10)
        compiler._address_cap_logged = False
        with self.assertLogs("jittersim.engine.compiler", logging.WARNING) as logged:
            first = compile_plan(devices=[], profiles={"p": PROFILE}, global_blocked_addresses=many)
            # A changing answer above the cap is not logged again.
            again = compile_plan(devices=[], profiles={"p": PROFILE},
                                 global_blocked_addresses=tuple(reversed(many[:-1])))
            logging.getLogger("jittersim.engine.compiler").warning("end")
        # The numerically lowest addresses (10.0.0.0 to 10.0.15.255), the same each time,
        # so the nft set does not change from plan to plan.
        self.assertEqual(set(first.global_blocked_addresses),
                         set(many[:MAX_GLOBAL_BLOCKED_ADDRESSES]))
        self.assertEqual(first.global_blocked_addresses, again.global_blocked_addresses)
        self.assertEqual(len(logged.records), 2)
        self.assertIn("4106 addresses", logged.output[0])

        below = compile_plan(devices=[], profiles={"p": PROFILE},
                             global_blocked_addresses=addresses(5))
        self.assertEqual(len(below.global_blocked_addresses), 5)
        self.assertFalse(compiler._address_cap_logged)




if __name__ == "__main__":
    unittest.main()
