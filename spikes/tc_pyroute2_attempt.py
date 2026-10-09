import json, subprocess
from pyroute2 import IPRoute
sh = lambda *a: subprocess.run(a, text=True, capture_output=True)
sh("ip", "link", "add", "wlan0", "type", "dummy"); sh("ip", "link", "set", "wlan0", "up")
def show(): return json.loads(sh("tc", "-j", "qdisc", "show", "dev", "wlan0").stdout)
# reference: what JitterSim emits (renderer._render_tc_commands shape)
ref = [("qdisc","replace","dev","wlan0","root","handle","10:","prio","bands","3","priomap",*["0"]*16),
       ("qdisc","replace","dev","wlan0","parent","10:1","handle","100:","fq_codel"),
       ("qdisc","replace","dev","wlan0","parent","10:2","handle","41:","netem","delay","80ms","20ms","loss","2%","rate","5mbit"),]
for c in ref: r = sh("tc", *c); assert r.returncode == 0, r.stderr
cli = show(); sh("tc","qdisc","del","dev","wlan0","root")
ip = IPRoute(); idx = ip.link_lookup(ifname="wlan0")[0]
notes = []
try:
    ip.tc("add", "prio", idx, "10:", bands=3, priomap=[0]*16)
    ip.tc("add", "fq_codel", idx, "100:", parent="10:1")
    ip.tc("add", "netem", idx, "41:", parent="10:2", delay=80000, jitter=20000, loss=2*(2**32//100), rate=625000)
except Exception as e:
    notes.append(f"pyroute2 error: {type(e).__name__}: {e}")
lib = show()
kinds = lambda q: [(x["kind"], x.get("handle"), x.get("parent")) for x in q]
print("tc CLI :", kinds(cli)); print("pyroute2:", kinds(lib)); print("notes:", notes)
nm = lambda q: [x["options"] for x in q if x["kind"]=="netem"]
print("netem opts CLI:", nm(cli)); print("netem opts lib:", nm(lib))
