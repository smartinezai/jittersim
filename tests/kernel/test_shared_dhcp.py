"""Check the test Wi-Fi DHCP server with the real dnsmasq that NetworkManager runs.

A household device that joins the Pi's copied router name often still holds
its router lease (192.168.0.x). It asks for that address again with an
INIT-REBOOT DHCPREQUEST. A dnsmasq that is not authoritative ignores the
request when it has no lease for the device, so the device waits and may keep
its router address: associated but without internet. The dnsmasq-shared.d
file that jittersim.localname installs makes the Pi's DHCP authoritative, so
the device is told at once (DHCPNAK) and asks for a 192.168.50.x address.

dnsmasq is started with the arguments NetworkManager 1.50 uses for a shared
connection (src/core/dnsmasq/nm-dnsmasq-manager.c), in network namespaces
created and removed here.
"""

import os
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from jittersim import localname

ROOT = Path(__file__).resolve().parents[2]
ENABLED = (
    sys.platform == "linux"
    and os.geteuid() == 0
    and os.environ.get("JITTERSIM_KERNEL_TESTS") == "1"
    and shutil.which("dnsmasq") is not None
)
PI, CLIENT = (f"jd{os.getpid()}-{role}" for role in ("pi", "client"))
CLIENT_MAC = bytes.fromhex("02000000500a")
ROUTER_LEASE = "192.168.0.34"
DHCPNAK, DHCPACK, DHCPOFFER = 6, 5, 2


def run(*argv, check=True):
    return subprocess.run(argv, capture_output=True, text=True, check=check, timeout=20)


def _checksum(header: bytes) -> int:
    total = sum(struct.unpack("!%dH" % (len(header) // 2), header))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def dhcp_frame(message_type: int, xid: int, requested: str = None) -> bytes:
    options = bytes((53, 1, message_type))
    if requested is not None:
        options += bytes((50, 4)) + socket.inet_aton(requested)
    options += bytes((55, 3, 1, 3, 6, 255))
    bootp = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s4s",
        1, 1, 6, 0, xid, 0, 0x8000,
        bytes(4), bytes(4), bytes(4), bytes(4),
        CLIENT_MAC + bytes(10), bytes(64), bytes(128), bytes((99, 130, 83, 99)),
    ) + options
    udp = struct.pack("!HHHH", 68, 67, 8 + len(bootp), 0) + bootp
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                     bytes(4), b"\xff\xff\xff\xff")
    ip = ip[:10] + struct.pack("!H", _checksum(ip)) + ip[12:]
    return b"\xff" * 6 + CLIENT_MAC + b"\x08\x00" + ip + udp


def reply_type(frame: bytes, xid: int):
    """The DHCP message type of a server reply for this exchange, or None."""
    if len(frame) < 14 + 20 + 8 + 240 or frame[12:14] != b"\x08\x00":
        return None
    ihl = (frame[14] & 0x0F) * 4
    if frame[14 + 9] != 17:
        return None
    udp = 14 + ihl
    source, destination = struct.unpack("!HH", frame[udp:udp + 4])
    bootp = frame[udp + 8:]
    if (source, destination) != (67, 68) or struct.unpack("!I", bootp[4:8])[0] != xid:
        return None
    options = bootp[240:]
    index = 0
    while index + 1 < len(options) and options[index] != 255:
        code = options[index]
        if code == 0:
            index += 1
            continue
        length = options[index + 1]
        if code == 53 and length == 1:
            return options[index + 2]
        index += 2 + length
    return None


def exchange(message_type: int, requested: str = None, wait: float = 3.0):
    """Send one DHCP message from the client namespace and return the reply type."""
    xid = int.from_bytes(os.urandom(4), "big")
    code = (
        "import sys, select, socket, time\n"
        "sys.path.insert(0, %r)\n"
        "from tests.kernel.test_shared_dhcp import dhcp_frame, reply_type\n"
        "s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))\n"
        "s.bind(('veth-c', 0))\n"
        "s.send(dhcp_frame(%d, %d, %r))\n"
        "deadline = time.monotonic() + %f\n"
        "while time.monotonic() < deadline:\n"
        "    if select.select([s], [], [], deadline - time.monotonic())[0]:\n"
        "        found = reply_type(s.recv(4096), %d)\n"
        "        if found is not None:\n"
        "            print(found); break\n"
        "else:\n"
        "    print('none')\n"
    ) % (str(ROOT), message_type, xid, requested, wait, xid)
    result = run("ip", "netns", "exec", CLIENT, sys.executable, "-c", code)
    value = result.stdout.strip()
    return None if value == "none" else int(value)


@unittest.skipUnless(ENABLED, "needs root, Linux, dnsmasq and JITTERSIM_KERNEL_TESTS=1")
class SharedDhcpTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.process = None
        for namespace in (PI, CLIENT):
            run("ip", "netns", "add", namespace)
        run("ip", "link", "add", "veth-p", "netns", PI, "type", "veth",
            "peer", "name", "veth-c", "netns", CLIENT)
        run("ip", "netns", "exec", CLIENT, "ip", "link", "set", "veth-c", "address",
            CLIENT_MAC.hex(":"))
        run("ip", "netns", "exec", PI, "ip", "addr", "add", "192.168.50.1/24", "dev", "veth-p")
        for namespace, interface in ((PI, "veth-p"), (CLIENT, "veth-c")):
            run("ip", "netns", "exec", namespace, "ip", "link", "set", interface, "up")
            run("ip", "netns", "exec", namespace, "ip", "link", "set", "lo", "up")

    def tearDown(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process.stderr.close()
        for namespace in (PI, CLIENT):
            run("ip", "netns", "del", namespace, check=False)
        shutil.rmtree(self.directory, ignore_errors=True)

    def start_dnsmasq(self, *, shipped_conf: bool, lease_seconds: int = 3600):
        conf_dir = self.directory / "dnsmasq-shared.d"
        conf_dir.mkdir()
        if shipped_conf:
            (conf_dir / localname.SHARED_DHCP_FILE).write_text(localname.render_shared_dhcp())
        # The NetworkManager 1.50 shared-mode command line, with test paths.
        self.process = subprocess.Popen(
            ("ip", "netns", "exec", PI, shutil.which("dnsmasq"),
             "--conf-file=/dev/null", "--no-hosts", "--keep-in-foreground",
             "--bind-interfaces", "--except-interface=lo", "--clear-on-reload",
             "--strict-order", "--listen-address=192.168.50.1",
             f"--dhcp-range=192.168.50.100,192.168.50.199,{lease_seconds}",
             f"--dhcp-leasefile={self.directory / 'leases'}",
             f"--pid-file={self.directory / 'pid'}",
             f"--conf-dir={conf_dir}", "--port=0", "--user=root"),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            listening = run("ip", "netns", "exec", PI, "ss", "-lun", "sport", "= :67").stdout
            if ":67" in listening:
                return
            if self.process.poll() is not None:
                self.fail("dnsmasq exited: " + self.process.stderr.read().decode())
            time.sleep(0.1)
        self.fail("dnsmasq did not start")

    def test_shipped_settings_refuse_a_router_lease_at_once(self):
        self.start_dnsmasq(shipped_conf=True)

        self.assertEqual(exchange(3, ROUTER_LEASE), DHCPNAK)
        # The refused device then gets a test Wi-Fi address as usual. dnsmasq
        # first pings the address it offers for up to three seconds.
        self.assertEqual(exchange(1, wait=8.0), DHCPOFFER)

    def test_networkmanager_defaults_ignore_a_router_lease(self):
        """The defect the shipped file removes: no answer at all."""
        self.start_dnsmasq(shipped_conf=False)

        self.assertIsNone(exchange(3, ROUTER_LEASE))

    def test_shipped_settings_confirm_a_test_address_lost_from_the_lease_file(self):
        # After a Pi reset the lease file may be gone while devices still hold
        # their test Wi-Fi address. They keep it instead of waiting.
        self.start_dnsmasq(shipped_conf=True)

        self.assertEqual(exchange(3, "192.168.50.150"), DHCPACK)


if __name__ == "__main__":
    unittest.main()
