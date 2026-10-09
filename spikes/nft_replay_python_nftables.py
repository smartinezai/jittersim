import json, subprocess, sys, time
import nftables
scripts = [json.loads(l) for l in open(sys.argv[1])]
def sh(*a, **k): return subprocess.run(a, text=True, capture_output=True, **k)
import re
for dev in sorted({m for sc in scripts for m in re.findall(r'device "([^"]+)"', sc)} | {"eth0"}):
    sh("ip", "link", "add", dev, "type", "dummy")
nft = nftables.Nftables(); nft.set_json_output(True); nft.set_handle_output(False)
def dump_cli():
    return json.loads(sh("nft", "-j", "list", "ruleset").stdout)["nftables"]
def dump_lib():
    rc, out, err = nft.cmd("list ruleset"); return json.loads(out)["nftables"]
def strip(x):
    if isinstance(x, dict): return {k: strip(v) for k, v in x.items() if k != "handle"}
    if isinstance(x, list): return [strip(v) for v in x]
    return x
norm = lambda d: strip([x for x in d if "metainfo" not in x])
same = bad = failed = 0; cli_t = lib_t = 0.0; examples = []
for s in scripts:
    sh("nft", "flush", "ruleset"); sh("nft", "delete", "table", "inet", "jittersim"); sh("nft", "delete", "table", "netdev", "jittersim_download")
    t = time.perf_counter(); r = sh("nft", "-f", "-", input=s); cli_t += time.perf_counter() - t
    if r.returncode: failed += 1; examples.append(r.stderr[:200]); continue
    a = norm(dump_cli())
    sh("nft", "delete", "table", "inet", "jittersim"); sh("nft", "delete", "table", "netdev", "jittersim_download")
    nft.set_json_output(False)
    t = time.perf_counter(); rc, out, err = nft.cmd(s); lib_t += time.perf_counter() - t
    nft.set_json_output(True)
    if rc: failed += 1; examples.append("lib:" + err[:200]); continue
    b = norm(dump_lib())
    if a == b: same += 1
    else: bad += 1
print("scripts", len(scripts), "applied-identically", same, "different", bad, "failed", failed)
print("nft -f - subprocess: %.1f ms/script, python-nftables cmd(): %.1f ms/script" % (cli_t / len(scripts) * 1e3, lib_t / len(scripts) * 1e3))
for e in examples[:3]: print(e)
rc, out, err = nft.cmd("list ruleset"); print("JSON ruleset objects after last apply:", len(json.loads(out)["nftables"]))
