import importlib.util
import os
import stat
import tempfile
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


FAKE_NFT = """#!/bin/sh
cat > "$FAKE_NFT_INPUT"
printf '%s\\n' "$@" > "$FAKE_NFT_ARGS"
exit "$FAKE_NFT_EXIT"
"""


class FirewallPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.nft = self.root / "nft"
        self.nft.write_text(FAKE_NFT)
        self.nft.chmod(self.nft.stat().st_mode | stat.S_IXUSR)
        patcher = patch.dict(os.environ, {"FAKE_NFT_INPUT": str(self.root / "input"),
                                          "FAKE_NFT_ARGS": str(self.root / "args"),
                                          "FAKE_NFT_EXIT": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_rendered_rules_are_checked_without_loading_them(self) -> None:
        self.assertTrue(preflight.check_firewall(uplink="eth0", test_interface="wlan0",
                                                 nft=str(self.nft)))
        self.assertEqual((self.root / "args").read_text().split(), ["--check", "-f", "-"])
        rules = (self.root / "input").read_text()
        self.assertIn('oifname "eth0"', rules)
        self.assertIn('iifname "wlan0"', rules)
        self.assertNotIn("@", rules.replace("@clients", ""))

    def test_a_kernel_that_cannot_load_the_rules_stops_the_install(self) -> None:
        os.environ["FAKE_NFT_EXIT"] = "1"
        with self.assertRaisesRegex(preflight.PreflightError, "nft_fib_inet"):
            preflight.check_firewall(uplink="eth0", test_interface="wlan0", nft=str(self.nft))

    def test_firewall_only_needs_nft_and_skips_the_gateway_checks(self) -> None:
        with patch.object(preflight, "check_gateway") as gateway, \
                patch.object(preflight, "check_firewall", return_value=False):
            self.assertEqual(preflight.main(["--uplink", "eth0", "--test-interface", "wlan0",
                                             "--firewall-only"]), 1)
        gateway.assert_not_called()
        with patch.object(preflight, "check_gateway") as gateway, \
                patch.object(preflight, "check_firewall", return_value=True) as firewall:
            self.assertEqual(preflight.main(["--uplink", "eth0", "--test-interface", "wlan0"]), 0)
        gateway.assert_called_once()
        firewall.assert_called_once()

    def test_the_installer_checks_the_firewall_after_installing_nftables(self) -> None:
        installer = (PREFLIGHT_PATH.parent / "install.sh").read_text()
        packages = installer.index("apt-get install -y dnsmasq-base")
        check = installer.index("preflight.py\" --firewall-only")
        restart = installer.index("systemctl restart jittersim-control-firewall.service")
        self.assertLess(packages, check)
        self.assertLess(check, restart)
