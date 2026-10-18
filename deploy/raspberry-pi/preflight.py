"""Check the Pi gateway before an installer changes network services."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from ipaddress import IPv4Interface, IPv4Network
from pathlib import Path
from typing import Any, List, Optional, Sequence

AP_NETWORK = IPv4Network("192.168.50.0/24")
MINIMUM_NETWORK_MANAGER_VERSION = (1, 50)
INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
# The release's own rules, next to this file in the source or the staged update.
FIREWALL_TEMPLATE = Path(__file__).with_name("jittersim-control-firewall.nft")


class PreflightError(ValueError):
    """A gateway prerequisite is missing or unsafe."""


def _output(arguments: List[str]) -> str:
    try:
        result = subprocess.run(
            arguments, check=True, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise PreflightError(f"could not inspect {' '.join(arguments[:2])}") from error
    return result.stdout.strip()


def _json_output(arguments: List[str]) -> Any:
    try:
        return json.loads(_output(arguments))
    except json.JSONDecodeError as error:
        raise PreflightError("the Pi returned invalid network information") from error


def _check_version(output: str) -> None:
    match = re.search(r"\bversion\s+(\d+)\.(\d+)", output)
    if match is None:
        raise PreflightError("could not read the NetworkManager version")
    version = int(match.group(1)), int(match.group(2))
    if version < MINIMUM_NETWORK_MANAGER_VERSION:
        raise PreflightError("NetworkManager 1.50 or newer is required for the reserved DHCP range")


def _check_addresses(addresses: Any, routes: Any) -> None:
    if not isinstance(addresses, list) or not isinstance(routes, list):
        raise PreflightError("the Pi returned invalid IPv4 interface information")
    uplink_networks = []
    for interface in addresses:
        if not isinstance(interface, dict):
            raise PreflightError("the Pi returned invalid IPv4 address information")
        address_info = interface.get("addr_info")
        if not isinstance(address_info, list):
            raise PreflightError("the Pi returned invalid IPv4 address information")
        for address in address_info:
            if not isinstance(address, dict) or address.get("family") != "inet":
                continue
            try:
                uplink_networks.append(
                    IPv4Interface(f"{address['local']}/{address['prefixlen']}").network
                )
            except (KeyError, TypeError, ValueError) as error:
                raise PreflightError("the Pi returned an invalid uplink IPv4 address") from error
    if not uplink_networks:
        raise PreflightError("the Ethernet uplink has no IPv4 address")
    if any(network.overlaps(AP_NETWORK) for network in uplink_networks):
        raise PreflightError("the Ethernet network overlaps JitterSim's 192.168.50.0/24 AP")
    if not any(isinstance(route, dict) and route.get("dst") == "default" for route in routes):
        raise PreflightError("the Ethernet uplink has no IPv4 default route")


def check_gateway(*, uplink: str, test_interface: str) -> None:
    for interface in (uplink, test_interface):
        if not INTERFACE_PATTERN.fullmatch(interface) or interface == "lo":
            raise PreflightError("runtime.env contains an unsafe interface name")
    if uplink == test_interface:
        raise PreflightError("uplink and test interfaces must be different")
    _check_version(_output(["nmcli", "--version"]))
    for interface in (uplink, test_interface):
        _json_output(["ip", "-j", "link", "show", "dev", interface])
    if _output(["nmcli", "radio", "wifi"]) != "enabled":
        raise PreflightError(
            "Wi-Fi is disabled. Set the WLAN country and enable Wi-Fi before installation"
        )
    device_type = _output(["nmcli", "-g", "GENERAL.TYPE", "device", "show", test_interface])
    if device_type != "wifi":
        raise PreflightError("the test interface is not a NetworkManager Wi-Fi device")
    ap_support = _output(["nmcli", "-g", "WIFI-PROPERTIES.AP", "device", "show", test_interface])
    if ap_support != "yes":
        raise PreflightError("the test Wi-Fi device does not support access-point mode")
    active_connection = _output(
        ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", test_interface]
    )
    if active_connection not in ("", "--", "jittersim-ap"):
        raise PreflightError("the test Wi-Fi has another active connection")
    _check_addresses(
        _json_output(["ip", "-j", "-4", "address", "show", "dev", uplink]),
        _json_output(["ip", "-j", "-4", "route", "show", "default", "dev", uplink]),
    )


def check_firewall(*, uplink: str, test_interface: str, template: Path = FIREWALL_TEMPLATE,
                   nft: Optional[str] = None) -> bool:
    """Ask this kernel whether it can load the release's management firewall.

    `nft --check` sends the rules as a test transaction and changes no rules (it
    may load the kernel modules the rules need). It fails when the kernel lacks
    a module the rules need (such as nft_fib_inet), so an install or update stops
    before it stops a service or replaces the running rules. Returns False when nft is not installed yet (a first install
    adds nftables later and checks again with --firewall-only).
    """
    for interface in (uplink, test_interface):
        if not INTERFACE_PATTERN.fullmatch(interface):
            raise PreflightError("runtime.env contains an unsafe interface name")
    nft = nft or shutil.which("nft") or shutil.which("nft", path="/usr/sbin:/sbin")
    if nft is None:
        return False
    rules = template.read_text().replace("@UPLINK_INTERFACE@", uplink).replace(
        "@TEST_INTERFACE@", test_interface)
    try:
        result = subprocess.run([nft, "--check", "-f", "-"], input=rules, capture_output=True,
                                text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PreflightError("could not check the management firewall rules with nft") from error
    if result.returncode != 0:
        raise PreflightError(
            "this kernel cannot load the management firewall rules (nft --check failed). "
            "No existing service was stopped and no existing file or rule was replaced "
            "(an install in progress may already have added packages). Check that the "
            "nft_fib_inet and nft_fib_ipv4 kernel modules are available")
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check a JitterSim Pi gateway before installation.")
    parser.add_argument("--uplink", required=True)
    parser.add_argument("--test-interface", required=True)
    parser.add_argument("--firewall-only", action="store_true",
                        help="only check that the kernel can load the management firewall")
    arguments = parser.parse_args(argv)
    try:
        if not arguments.firewall_only:
            check_gateway(uplink=arguments.uplink, test_interface=arguments.test_interface)
        checked = check_firewall(uplink=arguments.uplink, test_interface=arguments.test_interface)
        if arguments.firewall_only and not checked:
            raise PreflightError("nft is not installed, so the firewall rules cannot be checked")
    except PreflightError as error:
        print(f"JitterSim network preflight failed: {error}", file=sys.stderr)
        return 1
    print("JitterSim network preflight passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
