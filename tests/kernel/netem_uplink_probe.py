"""Measure which bytes netem corrupt changes on frames leaving the uplink.

Run as root in a disposable Linux container (it creates and leaves the network
namespaces "pi" and "rt"): python tests/kernel/netem_uplink_probe.py CORRUPT DUPLICATE
It rebuilds JitterSim's upload shape on the uplink (prio root, unmarked traffic
to fq_codel, mark 1 to netem) and counts changed header fields per class.
Results are in docs/pi-only-datapath-audit-2026-10-09.md.
"""
import socket, struct, subprocess, sys, time, collections
def sh(*a, check=True): return subprocess.run(a, check=check, capture_output=True, text=True)
for ns in ("pi", "rt"): sh("ip", "netns", "add", ns)
sh("ip", "link", "add", "up0", "netns", "pi", "address", "02:00:00:00:00:aa", "type", "veth", "peer", "name", "lan", "netns", "rt", "address", "02:00:00:00:00:bb")
for ns, dev, ip in (("pi", "up0", "192.168.0.17/24"), ("rt", "lan", "192.168.0.1/24")):
    sh("ip", "netns", "exec", ns, "ip", "addr", "add", ip, "dev", dev)
    sh("ip", "netns", "exec", ns, "ip", "link", "set", dev, "up")
sh("ip", "netns", "exec", "pi", "ip", "neigh", "replace", "192.168.0.1", "lladdr", "02:00:00:00:00:bb", "dev", "up0", "nud", "permanent")
# JitterSim's upload shape on eth0: prio root, unmarked traffic to fq_codel, mark 1 to netem.
for args in (("qdisc","replace","dev","up0","root","handle","10:","prio","bands","2","priomap",*["0"]*16),
             ("qdisc","replace","dev","up0","parent","10:1","handle","100:","fq_codel"),
             ("qdisc","replace","dev","up0","parent","10:2","handle","51:","netem","corrupt",sys.argv[1]+"%","duplicate",sys.argv[2]+"%"),
             ("filter","replace","dev","up0","protocol","ip","parent","10:","pref","1","handle","1","fw","flowid","10:2")):
    sh("ip","netns","exec","pi","tc",*args)
capture = subprocess.Popen(("ip","netns","exec","rt",sys.executable,"-c",
 "import socket,sys,select,time\n"
 "s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3)); s.bind(('lan',0))\n"
 "end=time.time()+25\n"
 "while time.time()<end:\n"
 "  if select.select([s],[],[],0.2)[0]:\n"
 "    f,a=s.recvfrom(65535)\n"
 "    if a[2]!=4: sys.stdout.write(f[:64].hex()+'\\n')\n"), stdout=subprocess.PIPE, text=True)
time.sleep(1)
sender = ("import socket\n"
 "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\n"
 "s.setsockopt(socket.SOL_SOCKET,socket.SO_MARK,{mark})\n"
 "import time\n"
 "for i in range(600): s.sendto({payload}*18,('192.168.0.1',9)); time.sleep(0.0005)\n")
sh("ip","netns","exec","pi",sys.executable,"-c",sender.format(mark=1, payload="b'M'"))
sh("ip","netns","exec","pi",sys.executable,"-c",sender.format(mark=0, payload="b'U'"))
out, _ = capture.communicate()
c = collections.Counter()
srcs = set()
for line in out.split():
    f = bytes.fromhex(line)
    kind = "marked" if f[42:60].count(b"M") > 9 else "unmarked" if f[42:60].count(b"U") > 9 else "unknown"
    c[kind + "_frames"] += 1
    if kind != "marked": 
        if f[0:14] != bytes.fromhex("0200000000bb0200000000aa0800"): c[kind + "_header_changed"] += 1
        continue
    if f[0:6] != bytes.fromhex("02000000" "00bb"): c["dst_mac_changed"] += 1
    if f[0] & 1: c["dst_mac_now_multicast"] += 1
    if f[6:12] != bytes.fromhex("0200000000aa"): c["src_mac_changed"] += 1; srcs.add(f[6:12].hex(":"))
    if f[6] & 1: c["src_mac_multicast_bit"] += 1
    if f[12:14] != b"\x08\x00": c["ethertype_changed"] += 1; continue
    ip = f[14:34]
    total = sum(struct.unpack("!10H", ip)); total = (total & 0xffff) + (total >> 16); total = (total & 0xffff) + (total >> 16)
    if total != 0xffff: c["ip_header_checksum_bad"] += 1
    if ip[12:16] != socket.inet_aton("192.168.0.17"): c["ip_source_changed"] += 1
print(dict(c)); print("distinct bogus source MACs:", len(srcs), sorted(srcs)[:6])
