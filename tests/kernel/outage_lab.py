"""A household network for the Pi-only cutover, built from mature open-source tools.

Namespaces (all created and deleted here, never the host's own interfaces):

  INET    the fake internet: dnsmasq answers *.example.org, Python's http.server
          serves one page.
  ROUTER  the household router stand-in: a Linux bridge for its LAN, dnsmasq for
          DHCP and DNS on 192.168.0.0/24, nftables masquerade to INET, conntrack
          event logging, a "radio" port and fault switches.
  HOUSE   one wired household device on the router LAN. It sees every broadcast
          the Pi sends.
  PI      the real JitterSim code. eth0 takes a DHCP lease from the router with ISC
          dhclient like NetworkManager would. wlan0 is 192.168.50.1/24 with the
          exact dnsmasq command line and nftables table NetworkManager's shared
          mode installs, plus JitterSim's control firewall.
  AIR     two Linux bridges stand for the two radios (router Wi-Fi and Pi AP).
          A Wi-Fi client "associates" by moving its station port between them,
          which drops and restores its carrier like a real roam.
  CLIENT, OTHER_CLIENT  two Wi-Fi devices running ISC dhclient.

Why these tools: dnsmasq is what NetworkManager runs for shared mode and what many
home routers run, ISC dhclient behaves like a stock DHCP client (INIT-REBOOT with
its saved lease, renew, rebind, expire), tcpdump and scapy capture and decode
frames without custom parsers, nftables and conntrack are the Pi's own datapath.
Only the glue (namespaces, process start and stop, timing loops) is new code.

Everything needs root on Linux inside the lab container (JITTERSIM_OUTAGE_LAB=1),
because the DHCP client hook lives in /etc/dhcp.
"""

import ctypes
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .lab import CLIENT, CLIENT_MAC, OTHER_CLIENT, OTHER_CLIENT_MAC, PI, ROUTER, run


def run_in(namespace, *argv, check=True, stdin=None):
    return run("ip", "netns", "exec", namespace, *argv, check=check, stdin=stdin)

ROOT = Path(__file__).resolve().parents[2]
PREFIX = f"jt{os.getpid()}"
AIR, INET, HOUSE = (f"{PREFIX}-{role}" for role in ("air", "inet", "house"))
NAMESPACES = (INET, ROUTER, HOUSE, PI, AIR, CLIENT, OTHER_CLIENT)
WEB_ADDRESS = "198.51.100.80"
INTERNET_DNS = "198.51.100.53"
PAGE_MARKER = "jittersim-lab-page-ok"
PI_ETH0_MAC = "02:00:00:00:00:e0"
PI_WLAN0_MAC = "02:00:00:00:50:01"
HOUSE_MAC = "02:00:00:00:00:4d"
# The station ports in AIR for each Wi-Fi client.
STATIONS = {CLIENT: "sta", OTHER_CLIENT: "sta2"}
CLIENT_INTERFACES = {CLIENT: "c0", OTHER_CLIENT: "c1", HOUSE: "h0"}
RADIOS = {"router": "br-rwifi", "pi": "br-piap"}
# Explicit, documented client timers. ISC dhclient's defaults except a 1 s first
# retransmission (phones retransmit after about 1 to 4 s, dhclient's default is 10).
DHCLIENT_CONF = """timeout 30;
retry 5;
reboot 10;
select-timeout 0;
initial-interval 1;
backoff-cutoff 2;
request subnet-mask, broadcast-address, time-offset, routers, domain-name-servers;
"""
# Debian's dhclient-script replaces /etc/resolv.conf with mv, which fails on the
# per-namespace bind mount of `ip netns exec`. This hook writes it in place.
RESOLV_HOOK = """make_resolv_conf() {
    if [ -n "$new_domain_name_servers" ]; then
        {
            for server in $new_domain_name_servers; do echo "nameserver $server"; done
            echo "options timeout:1 attempts:2"
        } > /etc/resolv.conf
    fi
}
"""
HOOK_PATH = Path("/etc/dhcp/dhclient-enter-hooks.d/jittersim-lab-resolv")

# NetworkManager 1.4x shared mode, from nm-firewall-utils.c (_fw_nft_set_shared).
NM_SHARED_TABLE = """table ip nm-shared-wlan0 {
    chain nat_postrouting {
        type nat hook postrouting priority 100; policy accept;
        ip saddr 192.168.50.0/24 ip daddr != 192.168.50.0/24 masquerade
    }
    chain filter_forward {
        type filter hook forward priority 0; policy accept;
        ip daddr 192.168.50.0/24 oifname "wlan0" ct state { established, related } accept
        ip saddr 192.168.50.0/24 iifname "wlan0" accept
        iifname "wlan0" oifname "wlan0" accept
        iifname "wlan0" reject
        oifname "wlan0" reject
    }
}
"""
ROUTER_TABLE = """table ip lab_router {
    chain post {
        type nat hook postrouting priority 100; policy accept;
        oifname "wan0" masquerade
    }
    chain fault {
        type filter hook forward priority -10; policy accept;
    }
    chain forward {
        type filter hook forward priority 0; policy accept;
        iifname "br-lan" oifname "wan0" ct state new counter
    }
}
"""

ENABLED = (
    os.environ.get("JITTERSIM_OUTAGE_LAB") == "1"
    and os.environ.get("JITTERSIM_KERNEL_TESTS") == "1"
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
    and all(
        shutil.which(tool)
        for tool in ("ip", "nft", "tc", "dnsmasq", "dhclient", "tcpdump", "curl", "dig",
                     "unbound", "conntrack", "hostapd")
    )
)


def nm_dnsmasq_argv(directory: Path) -> list:
    """NetworkManager's shared-mode dnsmasq command line (nm-dnsmasq-manager.c).

    ipv4.shared-dhcp-range is 192.168.50.100,192.168.50.199 (install.sh) and the
    lease time is NetworkManager's default of 3600 s. NM adds --log-dhcp and
    --log-queries only with trace logging; the lab always logs to a file.
    """

    return [
        "dnsmasq",
        "--conf-file=/dev/null",
        "--no-hosts",
        "--keep-in-foreground",
        "--bind-interfaces",
        "--except-interface=lo",
        "--clear-on-reload",
        "--strict-order",
        "--listen-address=192.168.50.1",
        "--dhcp-range=192.168.50.100,192.168.50.199,3600",
        f"--dhcp-leasefile={directory}/dnsmasq-wlan0.leases",
        f"--pid-file={directory}/nm-dnsmasq-wlan0.pid",
        f"--conf-dir={directory}/dnsmasq-shared.d",
        "--log-dhcp",
        "--log-queries",
        f"--log-facility={directory}/pi-dnsmasq.log",
    ]


class _Netns:
    """Enter a named network namespace in this thread for socket work (setns(2))."""

    CLONE_NEWNET = 0x40000000

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self):
        libc = ctypes.CDLL(None, use_errno=True)
        self.original = os.open("/proc/thread-self/ns/net", os.O_RDONLY)
        target = os.open(f"/run/netns/{self.name}", os.O_RDONLY)
        try:
            if libc.setns(target, self.CLONE_NEWNET) != 0:
                raise OSError(ctypes.get_errno(), "setns failed")
        finally:
            os.close(target)
        return self

    def __exit__(self, *_):
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            if libc.setns(self.original, self.CLONE_NEWNET) != 0:
                raise OSError(ctypes.get_errno(), "setns back failed")
        finally:
            os.close(self.original)


in_namespace = _Netns


class Lab:
    """Build and drive the household, the Pi and the Wi-Fi clients."""

    def __init__(self, directory: Path, *, router_lease=3600, router_authoritative=True,
                 pi_authoritative=True) -> None:
        self.directory = Path(directory)
        self.router_lease = router_lease
        self.router_authoritative = router_authoritative
        self.pi_authoritative = pi_authoritative
        self.processes = []
        self.dhclients = {}
        self.pi_dnsmasq = None
        self.pi_eth0_address = None

    # ----- construction -------------------------------------------------
    def start(self) -> "Lab":
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o755)
        HOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
        HOOK_PATH.write_text(RESOLV_HOOK)
        (self.directory / "dhclient.conf").write_text(DHCLIENT_CONF)
        for namespace in NAMESPACES:
            run("ip", "netns", "add", namespace)
            netns_etc = Path("/etc/netns") / namespace
            netns_etc.mkdir(parents=True, exist_ok=True)
            (netns_etc / "resolv.conf").write_text("")
            run_in(namespace, "ip", "link", "set", "lo", "up")
        self._links()
        self._internet()
        self._router()
        self._pi_uplink()
        self.start_pi_access_point()
        return self

    def _veth(self, left_ns, left, right_ns, right, *, left_mac=None):
        run("ip", "-n", left_ns, "link", "add", left, "type", "veth", "peer", "name", right,
            "netns", right_ns)
        if left_mac:
            run("ip", "-n", left_ns, "link", "set", left, "address", left_mac)
        for namespace, name in ((left_ns, left), (right_ns, right)):
            run("ip", "-n", namespace, "link", "set", name, "up")

    def _bridge(self, namespace, name, *ports):
        run("ip", "-n", namespace, "link", "add", name, "type", "bridge", "forward_delay", "0")
        for port in ports:
            run("ip", "-n", namespace, "link", "set", port, "master", name)
        run("ip", "-n", namespace, "link", "set", name, "up")

    def _links(self):
        self._veth(INET, "inet0", ROUTER, "wan0")
        self._veth(PI, "eth0", ROUTER, "r-pi", left_mac=PI_ETH0_MAC)
        self._veth(HOUSE, "h0", ROUTER, "r-house", left_mac=HOUSE_MAC)
        self._veth(AIR, "rap", ROUTER, "r-wifi")
        self._bridge(ROUTER, "br-lan", "r-pi", "r-house", "r-wifi")
        self._veth(PI, "wlan0", AIR, "ap", left_mac=PI_WLAN0_MAC)
        self._veth(CLIENT, "c0", AIR, "sta", left_mac=CLIENT_MAC)
        self._veth(OTHER_CLIENT, "c1", AIR, "sta2", left_mac=OTHER_CLIENT_MAC)
        self._bridge(AIR, RADIOS["router"], "rap")
        self._bridge(AIR, RADIOS["pi"], "ap")
        for station in STATIONS.values():
            run("ip", "-n", AIR, "link", "set", station, "down")
        # A real NIC fills in checksums; a veth leaves them to the receiver. Without
        # this every captured frame would look damaged.
        for namespace, name in ((PI, "eth0"), (PI, "wlan0"), (CLIENT, "c0"),
                                (OTHER_CLIENT, "c1"), (HOUSE, "h0")):
            run_in(namespace, "ethtool", "-K", name, "tx", "off")

    def spawn(self, namespace, argv, *, log=None):
        stream = open(self.directory / log, "ab") if log else subprocess.DEVNULL
        try:
            process = subprocess.Popen(["ip", "netns", "exec", namespace, *argv],
                                       stdout=stream, stderr=subprocess.STDOUT)
        finally:
            if log:
                stream.close()
        self.processes.append(process)
        return process

    def _internet(self):
        for address in ("198.51.100.1/24", f"{INTERNET_DNS}/24", f"{WEB_ADDRESS}/24"):
            run("ip", "-n", INET, "addr", "add", address, "dev", "inet0")
        www = self.directory / "www"
        www.mkdir(exist_ok=True)
        (www / "index.html").write_text(PAGE_MARKER + "\n")
        (www / "big.bin").write_bytes(os.urandom(4 * 1024 * 1024))
        self.spawn(INET, ["dnsmasq", "-k", "--conf-file=/dev/null", "--no-hosts", "--no-resolv",
                          "--bind-interfaces", f"--listen-address={INTERNET_DNS}",
                          # Real names answer A and an empty AAAA, like a v4-only site.
                          "--local=/example.org/",
                          *(f"--host-record={name}.example.org,{WEB_ADDRESS}"
                            for name in ("www", "video", "news", "cdn.video")),
                          f"--address=/flood.example.org/{WEB_ADDRESS}", "--log-queries",
                          f"--log-facility={self.directory}/inet-dns.log"])
        self.spawn(INET, ["python", "-m", "http.server", "80", "--bind", WEB_ADDRESS,
                          "--directory", str(www)], log="inet-http.log")

    def _router(self):
        run("ip", "-n", ROUTER, "addr", "add", "198.51.100.2/24", "dev", "wan0")
        run("ip", "-n", ROUTER, "addr", "add", "192.168.0.1/24", "dev", "br-lan")
        run("ip", "-n", ROUTER, "route", "add", "default", "via", "198.51.100.1")
        run_in(ROUTER, "sysctl", "-qw", "net.ipv4.ip_forward=1")
        run_in(ROUTER, "nft", "-f", "-", stdin=ROUTER_TABLE)
        self.start_router_dhcp()
        self.spawn(ROUTER, ["conntrack", "-E", "-e", "NEW,DESTROY", "-o", "timestamp"],
                   log="router-flows.log")

    def start_router_dhcp(self):
        argv = ["dnsmasq", "-k", "--conf-file=/dev/null", "--no-hosts", "--no-resolv",
                "--bind-interfaces", "--interface=br-lan", "--except-interface=lo",
                f"--server={INTERNET_DNS}",
                f"--dhcp-range=192.168.0.10,192.168.0.250,{self.router_lease}",
                f"--dhcp-leasefile={self.directory}/router.leases",
                f"--pid-file={self.directory}/router-dnsmasq.pid", "--log-dhcp",
                f"--log-facility={self.directory}/router-dnsmasq.log"]
        if self.router_authoritative:
            argv.append("--dhcp-authoritative")
        self.spawn(ROUTER, argv)
        self._wait_port(ROUTER, "192.168.0.1", 53)

    def router_fault(self, rule=None):
        """Install one nft rule in the router's forward fault chain, or clear it."""
        run_in(ROUTER, "nft", "flush", "chain", "ip", "lab_router", "fault")
        if rule:
            run_in(ROUTER, "nft", "add", "rule", "ip", "lab_router", "fault", *rule.split())

    def _pi_uplink(self):
        run_in(PI, "sysctl", "-qw", "net.ipv4.ip_forward=1")
        result = self.dhcp(PI, "eth0", daemon=True)
        if not result["address"]:
            raise RuntimeError("the Pi did not get an address from the router")
        self.pi_eth0_address = result["address"]

    def start_pi_access_point(self):
        """What `nmcli connection up jittersim-ap` does on wlan0 in shared mode."""
        run("ip", "-n", PI, "addr", "replace", "192.168.50.1/24", "dev", "wlan0")
        run("ip", "-n", PI, "link", "set", "wlan0", "up")
        run_in(PI, "nft", "-f", "-", stdin=NM_SHARED_TABLE)
        # The installer's own files for NetworkManager's shared dnsmasq (the
        # dashboard name record and dhcp-authoritative, PR 105).
        # pi_authoritative=False reproduces a Pi installed before that change.
        from jittersim import localname

        shared = self.directory / "dnsmasq-shared.d"
        config = self.directory / "jittersim-config"
        config.mkdir(exist_ok=True)
        localname.install_config(config, shared)
        if not self.pi_authoritative:
            (shared / localname.SHARED_DHCP_FILE).unlink()
        self.pi_dnsmasq = self.spawn(PI, nm_dnsmasq_argv(self.directory))
        self._wait_port(PI, "192.168.50.1", 53)

    def stop_pi_access_point(self):
        """What `nmcli connection down jittersim-ap` does: no address, no DHCP, no table."""
        if self.pi_dnsmasq is not None:
            self.pi_dnsmasq.terminate()
            self.pi_dnsmasq.wait(timeout=5)
            self.pi_dnsmasq = None
        run("ip", "-n", PI, "addr", "flush", "dev", "wlan0")
        run_in(PI, "nft", "delete", "table", "ip", "nm-shared-wlan0", check=False)
        run("ip", "-n", AIR, "link", "set", "ap", "down")

    def restart_pi_access_point(self):
        self.stop_pi_access_point()
        run("ip", "-n", AIR, "link", "set", "ap", "up")
        self.start_pi_access_point()

    def load_control_firewall(self):
        text = (ROOT / "deploy/raspberry-pi/jittersim-control-firewall.nft").read_text()
        run_in(PI, "nft", "-f", "-",
               stdin=text.replace("@TEST_INTERFACE@", "wlan0").replace(
                   "@UPLINK_INTERFACE@", "eth0"))

    def _wait_port(self, namespace, address, port, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            listening = run_in(namespace, "ss", "-lnu", check=False).stdout
            if f"{address}:{port} " in listening:
                return
            time.sleep(0.05)
        raise RuntimeError(f"nothing listens on {address}:{port} in {namespace}")

    def stop(self):
        for namespace in NAMESPACES:
            pids = run("ip", "netns", "pids", namespace, check=False).stdout.split()
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for namespace in NAMESPACES:
            run("ip", "netns", "del", namespace, check=False)
            shutil.rmtree(Path("/etc/netns") / namespace, ignore_errors=True)

    # ----- Wi-Fi association and DHCP ----------------------------------
    def associate(self, namespace, radio):
        """Roam a Wi-Fi client to the router's radio, the Pi's radio, or none."""
        station = STATIONS[namespace]
        run("ip", "-n", AIR, "link", "set", station, "down")
        run("ip", "-n", AIR, "link", "set", station, "nomaster")
        if radio is None:
            return
        run("ip", "-n", AIR, "link", "set", station, "master", RADIOS[radio])
        run("ip", "-n", AIR, "link", "set", station, "up")

    def dhcp(self, namespace, interface=None, *, daemon=True, fresh=False):
        """Run ISC dhclient. With a saved lease it starts with INIT-REBOOT.

        daemon=True keeps it running (renew, rebind, expire) like a real device.
        Returns the time to the first address and the address.
        """
        interface = interface or CLIENT_INTERFACES[namespace]
        key = (namespace, interface)
        lease = self.directory / f"{namespace}-{interface}.leases"
        if fresh and lease.exists():
            lease.unlink()
        self.stop_dhcp(namespace, interface)
        log = f"dhclient-{namespace}-{interface}.log"
        started = time.monotonic()
        process = self.spawn(namespace, [
            "dhclient", "-d", "-v", "-cf", str(self.directory / "dhclient.conf"),
            "-lf", str(lease), "-pf", str(self.directory / f"{namespace}-{interface}.pid"),
            interface,
        ], log=log)
        self.dhclients[key] = process
        address = None
        while time.monotonic() - started < 40:
            address = self.address(namespace, interface)
            if address:
                break
            time.sleep(0.1)
        elapsed = time.monotonic() - started
        if not daemon:
            self.stop_dhcp(namespace, interface)
        return {"address": address, "seconds": round(elapsed, 2) if address else None}

    def start_dhcp_daemon(self, namespace, interface=None):
        """Start dhclient without waiting (for a roam where the old address stays)."""
        interface = interface or CLIENT_INTERFACES[namespace]
        lease = self.directory / f"{namespace}-{interface}.leases"
        self.stop_dhcp(namespace, interface)
        self.dhclients[(namespace, interface)] = self.spawn(namespace, [
            "dhclient", "-d", "-v", "-cf", str(self.directory / "dhclient.conf"),
            "-lf", str(lease), "-pf", str(self.directory / f"{namespace}-{interface}.pid"),
            interface,
        ], log=f"dhclient-{namespace}-{interface}.log")

    def stop_dhcp(self, namespace, interface=None):
        """Stop the client like a device that leaves: no DHCPRELEASE, address kept."""
        interface = interface or CLIENT_INTERFACES[namespace]
        process = self.dhclients.pop((namespace, interface), None)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    def address(self, namespace, interface=None):
        interface = interface or CLIENT_INTERFACES[namespace]
        output = run("ip", "-n", namespace, "-4", "-o", "addr", "show", "dev", interface,
                     check=False).stdout
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", output)
        return match.group(1) if match else None

    def default_gateway(self, namespace):
        output = run("ip", "-n", namespace, "-4", "route", "show", "default", check=False).stdout
        match = re.search(r"default via (\S+)", output)
        return match.group(1) if match else None

    def resolver(self, namespace):
        text = (Path("/etc/netns") / namespace / "resolv.conf").read_text()
        match = re.search(r"nameserver (\S+)", text)
        return match.group(1) if match else None

    # ----- the pass/fail probe -----------------------------------------
    def browse(self, namespace, name="www.example.org"):
        """Can the client resolve a name with its own resolver and fetch the page?"""
        resolved = run_in(namespace, "dig", "+time=1", "+tries=1", "+short", name,
                          check=False).stdout.split()
        dns_ok = WEB_ADDRESS in resolved
        page = run_in(namespace, "curl", "-s", "--max-time", "3", f"http://{name}/",
                      check=False).stdout if dns_ok else ""
        return {"dns": dns_ok, "http": PAGE_MARKER in page, "ok": dns_ok and PAGE_MARKER in page}

    def wait_browse(self, namespace, timeout, *, since=None, interval=0.25):
        """Seconds from `since` until browse() first succeeds, or None."""
        since = time.monotonic() if since is None else since
        while time.monotonic() - since < timeout:
            if self.browse(namespace)["ok"]:
                return round(time.monotonic() - since, 2)
            time.sleep(interval)
        return None

    @contextmanager
    def browse_monitor(self, namespace, interval=0.2):
        """Probe browsing in the background and report the longest failing stretch."""
        samples = []
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                samples.append((time.monotonic(), self.browse(namespace)["ok"]))
                stop.wait(interval)

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        report = {}
        try:
            yield report
        finally:
            stop.set()
            thread.join(timeout=10)
            report.update(outage_summary(samples))

    @contextmanager
    def stream(self, namespace, destination=WEB_ADDRESS, interval="0.05"):
        """A steady ping stream: the longest gap between replies is the outage."""
        process = subprocess.Popen(
            ["ip", "netns", "exec", namespace, "ping", "-n", "-D", "-i", interval, "-W", "1",
             destination], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        report = {}
        started = time.time()
        try:
            yield report
        finally:
            process.send_signal(signal.SIGINT)
            output, _ = process.communicate(timeout=10)
            stamps = [float(value) for value in re.findall(r"^\[(\d+\.\d+)\].*icmp_seq=",
                                                           output, re.M)]
            sent = re.search(r"(\d+) packets transmitted, (\d+) received", output)
            gaps = [b - a for a, b in zip([started] + stamps, stamps + [time.time()])]
            report.update({
                "sent": int(sent.group(1)) if sent else None,
                "received": int(sent.group(2)) if sent else len(stamps),
                "max_gap_seconds": round(max(gaps), 2) if gaps else None,
            })

    @contextmanager
    def capture(self, namespace, interface, name, *, inbound_only=False, snaplen=256):
        """tcpdump every frame on one interface into a pcap file."""
        direction = ("-Q", "in") if inbound_only else ()
        path = self.directory / f"{name}.pcap"
        process = subprocess.Popen(
            ["ip", "netns", "exec", namespace, "tcpdump", "-i", interface, *direction, "-U",
             "-n", "-s", str(snaplen), "-w", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        report = {"path": path, "started": time.time()}
        try:
            yield report
        finally:
            time.sleep(0.3)
            process.send_signal(signal.SIGINT)
            process.communicate(timeout=10)
            report["seconds"] = round(time.time() - report["started"], 2)

    def conntrack_count(self, namespace=PI):
        output = run_in(namespace, "conntrack", "-C", check=False).stdout.strip()
        return int(output) if output.isdigit() else None


def outage_summary(samples):
    """The longest stretch of failed samples, and how often probes failed."""
    longest, start, failures = 0.0, None, 0
    for moment, ok in samples:
        if not ok:
            failures += 1
            start = moment if start is None else start
            longest = max(longest, moment - start)
        else:
            if start is not None:
                longest = max(longest, moment - start)
            start = None
    return {"probes": len(samples), "failed_probes": failures,
            "longest_outage_seconds": round(longest, 2)}


def classify_frames(path, pi_mac=PI_ETH0_MAC, pi_address=None):
    """Count every frame that arrived on the router port from the Pi, by kind (scapy).

    The capture runs with `tcpdump -Q in` on the router's port to the Pi, so every
    frame came from the Pi's cable. A source MAC other than the Pi's would be a
    Wi-Fi client's frame bridged or leaked onto the household LAN.
    """
    from scapy.all import Ether, rdpcap

    counts, foreign_sources, frames = {}, {}, 0
    first = last = None
    for packet in rdpcap(str(path)):
        if Ether not in packet:
            continue
        sender = packet[Ether].src.lower()
        if sender != pi_mac:
            foreign_sources[sender] = foreign_sources.get(sender, 0) + 1
        frames += 1
        first = packet.time if first is None else first
        last = packet.time
        kind = _kind(packet, pi_address)
        counts[kind] = counts.get(kind, 0) + 1
    seconds = float(last - first) if frames > 1 else 0.0
    return {"frames": frames, "kinds": dict(sorted(counts.items())),
            "first_to_last_seconds": round(seconds, 2),
            "foreign_source_macs": foreign_sources}


def _kind(packet, pi_address):
    from scapy.all import ARP, ICMP, IP, TCP, UDP, Ether, IPv6
    from scapy.layers.inet6 import (
        ICMPv6MLReport,
        ICMPv6MLReport2,
        ICMPv6ND_NA,
        ICMPv6ND_NS,
        ICMPv6ND_RA,
        ICMPv6ND_RS,
    )

    ether = packet[Ether]
    broadcast = ether.dst.lower() == "ff:ff:ff:ff:ff:ff"
    multicast = bool(int(ether.dst.split(":")[0], 16) & 1) and not broadcast
    if ARP in packet:
        arp = packet[ARP]
        if arp.psrc == arp.pdst:
            label = "arp-gratuitous"
        else:
            label = "arp-request" if arp.op == 1 else "arp-reply"
        if pi_address and arp.psrc not in (pi_address, "0.0.0.0"):
            label += f"-SENDER-{arp.psrc}"
        return label
    if ether.type == 0x88CC:
        return "LLDP"
    if IPv6 in packet:
        for layer, name in ((ICMPv6ND_RA, "ipv6-ROUTER-ADVERTISEMENT"),
                            (ICMPv6ND_RS, "ipv6-router-solicitation"),
                            (ICMPv6ND_NS, "ipv6-neighbor-solicitation"),
                            (ICMPv6ND_NA, "ipv6-neighbor-advertisement"),
                            (ICMPv6MLReport2, "ipv6-mld-report"),
                            (ICMPv6MLReport, "ipv6-mld-report")):
            if layer in packet:
                return name
        if UDP in packet and packet[UDP].dport == 5353:
            return "mdns-ipv6"
        if UDP in packet and packet[UDP].dport == 547:
            return "dhcpv6-client"
        return "ipv6-other" + ("-multicast" if multicast else "")
    if IP in packet:
        ip = packet[IP]
        if UDP in packet and {packet[UDP].sport, packet[UDP].dport} & {67, 68}:
            return "DHCP-SERVER-REPLY" if packet[UDP].sport == 67 else "dhcp-client"
        if UDP in packet and packet[UDP].dport == 5353:
            return "mdns"
        if UDP in packet and packet[UDP].dport == 1900:
            return "ssdp"
        if UDP in packet and packet[UDP].dport in (137, 138, 5355):
            return "netbios-llmnr"
        if ip.proto == 2:
            return "igmp"
        if pi_address and ip.src != pi_address:
            return f"ipv4-FOREIGN-SOURCE-{ip.src}"
        if broadcast or multicast:
            return "ipv4-broadcast-other"
        if TCP in packet:
            return "ipv4-unicast-tcp"
        if UDP in packet:
            return "ipv4-unicast-udp" + ("-dns" if packet[UDP].dport == 53 else "")
        if ICMP in packet:
            return "ipv4-unicast-icmp"
        return "ipv4-unicast-other"
    return f"ethertype-{ether.type:#06x}"


def _checksum(data: bytes) -> int:
    """RFC 1071 sum. 0 means a header with a correct checksum field."""
    if len(data) % 2:
        data += b"\0"
    total = sum(int.from_bytes(data[index:index + 2], "big") for index in range(0, len(data), 2))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def mac_bytes(text: str) -> bytes:
    return bytes.fromhex(text.replace(":", ""))


def audit_uplink_frames(path, *, pi_mac, router_mac, pi_address, local_dns="192.168.0.1"):
    """Check every frame the router received from the Pi for damage and duplicates.

    The capture must hold whole frames (snaplen 0) from `tcpdump -Q in` on the
    router's port. Frames are read raw (scapy's RawPcapReader) so a damaged header
    cannot confuse a dissector. "local" frames are the Pi's own traffic (ARP, its
    DHCP client, its DNS forwarder's queries to the router, mDNS); "forwarded"
    frames carry Wi-Fi clients' traffic after NAT.
    """
    from collections import Counter

    from scapy.utils import RawPcapReader

    pi_mac, router_mac = mac_bytes(pi_mac), mac_bytes(router_mac)
    pi_ip = bytes(int(part) for part in pi_address.split("."))
    dns_ip = bytes(int(part) for part in local_dns.split("."))
    totals, problems, duplicates = Counter(), {}, Counter()
    source_macs, seen, examples = Counter(), {}, []
    reader = RawPcapReader(str(path))
    for data, meta in reader:
        moment = meta.sec + meta.usec / 1e6
        totals["frames"] += 1
        found = []
        if len(data) < 14:
            totals["runt"] += 1
            continue
        dst, src, ethertype = data[0:6], data[6:12], int.from_bytes(data[12:14], "big")
        kind = "unknown"
        if src != pi_mac:
            found.append("src-mac-not-pi")
            source_macs[src.hex(":")] += 1
        if src[0] & 1:
            found.append("src-mac-multicast-bit")
        if dst[0] & 1:
            if dst != b"\xff" * 6 and not dst.startswith((b"\x01\x00\x5e", b"\x33\x33")):
                found.append("dst-mac-odd-multicast")
        elif dst != router_mac:
            found.append("dst-mac-unknown-unicast")
        if ethertype == 0x0806:
            kind = "local-arp"
            arp = data[14:42]
            if len(arp) < 28 or arp[0:6] != b"\x00\x01\x08\x00\x06\x04" \
                    or int.from_bytes(arp[6:8], "big") not in (1, 2):
                found.append("arp-malformed")
            elif arp[8:14] != pi_mac or arp[14:18] != pi_ip:
                found.append("arp-sender-not-pi")
        elif ethertype == 0x86DD:
            kind = "local-ipv6"
        elif ethertype == 0x0800:
            ip = data[14:]
            header = (ip[0] & 0x0F) * 4 if ip else 0
            protocol = ip[9] if len(ip) > 9 else None
            source, destination = ip[12:16], ip[16:20]
            ports = (int.from_bytes(ip[header:header + 2], "big"),
                     int.from_bytes(ip[header + 2:header + 4], "big")
                     ) if len(ip) >= header + 4 else (0, 0)
            local = protocol == 17 and (ports == (68, 67) or source == pi_ip and (
                destination == dns_ip and ports[1] == 53 or ports[1] == 5353))
            name = {6: "tcp", 17: "udp", 1: "icmp"}.get(protocol, f"proto{protocol}")
            if protocol == 17 and ports[1] == 53:
                name = "dns"
            kind = ("local-" if local else "forwarded-") + name
            if len(ip) < 20 or ip[0] >> 4 != 4 or header < 20:
                found.append("ipv4-bad-version-or-ihl")
            else:
                if _checksum(ip[:header]) != 0:
                    found.append("ipv4-bad-header-checksum")
                length = int.from_bytes(ip[2:4], "big")
                if length > len(ip) or length < header:
                    found.append("ipv4-bad-total-length")
                if source != pi_ip and not (ports == (68, 67) and source == bytes(4)):
                    found.append("ipv4-source-not-pi")
                segment = ip[header:length]
                if protocol in (6, 17) and len(segment) >= 8 and length <= len(ip):
                    field = segment[16:18] if protocol == 6 else segment[6:8]
                    stored = int.from_bytes(field, "big")
                    if not (protocol == 17 and stored == 0):
                        pseudo = (source + destination + bytes([0, protocol])
                                  + len(segment).to_bytes(2, "big"))
                        if _checksum(pseudo + segment) != 0:
                            found.append(f"{name}-bad-l4-checksum")
        else:
            found.append("bad-ethertype")
        totals[kind] += 1
        previous = seen.get(data)
        if previous is not None and moment - previous < 2.0:
            duplicates[kind] += 1
        seen[data] = moment
        for item in found:
            problems.setdefault(item, Counter())[kind] += 1
        if found and len(examples) < 12:
            examples.append({"kind": kind, "problems": found, "head": data[:34].hex()})
    reader.close()
    return {
        "frames": totals["frames"],
        "by_kind": {key: value for key, value in sorted(totals.items()) if key != "frames"},
        "problems": {key: dict(value) for key, value in sorted(problems.items())},
        "byte_identical_duplicates_within_2s": dict(duplicates),
        "source_macs_not_pi": dict(source_macs),
        "examples": examples,
    }


def nstat(namespace):
    """Kernel SNMP counters (iproute2 nstat) as a dict."""
    output = run_in(namespace, "nstat", "-asz", check=False).stdout
    values = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            values[parts[0]] = int(parts[1])
    return values
