import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

PREFLIGHT_PATH = (
    Path(__file__).resolve().parents[2] / "deploy" / "raspberry-pi" / "preflight.py"
)
SPEC = importlib.util.spec_from_file_location("jittersim_pi_preflight", PREFLIGHT_PATH)
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def uplink_address(address: str, prefix: int = 24):
    return [{"addr_info": [{"family": "inet", "local": address, "prefixlen": prefix}]}]


class PiPreflightTests(unittest.TestCase):
    def test_rejects_an_old_network_manager_before_installation(self) -> None:
        with self.assertRaisesRegex(preflight.PreflightError, "1.50"):
            preflight._check_version("nmcli tool, version 1.42.4")
        preflight._check_version("nmcli tool, version 1.52.1")

    def test_rejects_a_router_subnet_that_overlaps_the_wifi(self) -> None:
        with self.assertRaisesRegex(preflight.PreflightError, "overlaps"):
            preflight._check_addresses(
                uplink_address("192.168.50.20", 23), [{"dst": "default"}]
            )

    def test_requires_an_ethernet_route_before_changing_the_ap(self) -> None:
        with self.assertRaisesRegex(preflight.PreflightError, "default route"):
            preflight._check_addresses(uplink_address("192.168.0.20"), [])
        preflight._check_addresses(
            uplink_address("192.168.0.20"), [{"dst": "default"}]
        )

    def test_refuses_to_replace_another_active_wifi_connection(self) -> None:
        with patch.object(
            preflight,
            "_output",
            side_effect=["nmcli tool, version 1.52.1", "enabled", "wifi", "yes", "home-wifi"],
        ), patch.object(preflight, "_json_output", return_value=[{}]):
            with self.assertRaisesRegex(preflight.PreflightError, "another active connection"):
                preflight.check_gateway(uplink="eth0", test_interface="wlan0")

    def test_rejects_disabled_wifi_before_installation(self) -> None:
        with patch.object(
            preflight, "_output", side_effect=["nmcli tool, version 1.52.1", "disabled"]
        ), patch.object(preflight, "_json_output", return_value=[{}]):
            with self.assertRaisesRegex(preflight.PreflightError, "WLAN country"):
                preflight.check_gateway(uplink="eth0", test_interface="wlan0")
