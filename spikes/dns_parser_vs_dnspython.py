import random, time, dns.message, dns.rrset, dns.rdatatype, dns.flags, dns.rcode
from ipaddress import IPv4Address
from jittersim.networking.dns_watch import parse_dns_message
random.seed(7)
names = ["www.netflix.com.","a1051.dscg4.akamai.net.","teams.microsoft.com.","x.y.z.example.co.uk.","m.media-amazon.com."]
def build(name, n_a, cname):
    q = dns.message.make_query(name, "A"); r = dns.message.make_response(q)
    ans=[]
    if cname:
        r.answer.append(dns.rrset.from_text(name, 60, "IN", "CNAME", "edge."+name)); name="edge."+name
    if n_a:
        r.answer.append(dns.rrset.from_text(name, 60, "IN", "A", *[".".join(str(random.randrange(1,255)) for _ in range(4)) for _ in range(n_a)]))
    return r
def ref(wire):
    try: m = dns.message.from_wire(wire)
    except Exception: return None
    if not (m.flags & dns.flags.QR) or m.rcode()!=0: return None
    out=[]
    for rr in m.answer:
        if rr.rdtype==dns.rdatatype.A:
            for it in rr:
                if hasattr(it,'address'): out.append((rr.name.to_text().rstrip(".").lower(), IPv4Address(it.address)))
    return out
valid=0; same=0; diffs=[]
msgs=[]
for i in range(3000):
    r = build(random.choice(names), random.randrange(0,6), random.random()<.4)
    msgs.append(r.to_wire())
for w in msgs:
    a = parse_dns_message(w); b = ref(w)
    ours = None if a is None else list(a.addresses)
    valid+=1
    if ours==b: same+=1
    else: diffs.append(("valid", w, ours, b))
print("valid responses", valid, "agree", same, "diffs", len(diffs))
# mutation fuzz
crash=0; lenient=0; agree=0; N=60000
for _ in range(N):
    w = bytearray(random.choice(msgs))
    for _ in range(random.randrange(1,5)):
        k=random.randrange(3)
        if k==0: w[random.randrange(len(w))]=random.randrange(256)
        elif k==1: del w[random.randrange(len(w)):]
        else: w[random.randrange(len(w)):random.randrange(len(w))]=bytes(random.randrange(256) for _ in range(3))
        if not w: w=bytearray(b"\0")
    w=bytes(w)
    try: a=parse_dns_message(w)
    except Exception as e: crash+=1; print("CRASH", type(e).__name__); continue
    b=ref(w); ours=None if a is None else list(a.addresses)
    if ours==b: agree+=1
    elif b is None and ours is not None: lenient+=1   # we accept, dnspython rejects
    else: diffs.append(("mut", w, ours, b))
print("mutated", N, "agree", agree, "we-accept-dnspython-rejects", lenient, "other-diffs", len([d for d in diffs if d[0]=='mut']), "crashes", crash)
for d in [d for d in diffs][:5]: print(d[0], d[2], d[3])
# speed
w = msgs[0]; w2=[m for m in msgs if len(m)>80][0]
for label,f in (("custom",lambda x: parse_dns_message(x)),("dnspython",lambda x: dns.message.from_wire(x))):
    t=time.perf_counter()
    for _ in range(20000): f(w2)
    print(label, round((time.perf_counter()-t)/20000*1e6,1), "us/message")
