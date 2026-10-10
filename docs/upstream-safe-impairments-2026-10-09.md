# Upstream-safe impairments (9 October 2026)

Why the household router no longer receives damaged, copied or reordered
frames from the Pi, where every impairment now runs, and what is still open.
Background: `docs/outage-dossier-2026-10-04-2026-10-09.md` (hypothesis H1/H5)
and `docs/pi-only-datapath-audit-2026-10-09.md` (H5-1 to H5-5).

## Where impairments ran before this change

Marks are set by nftables in `inet jittersim` (forward hook, priority -150)
for traffic from the test Wi-Fi (`wlan0`); downloads are marked again in the
`netdev jittersim_download` egress hook on `wlan0`. Every scope then used the
same two `prio` trees (`10:` root, band `10:1` fq_codel for unmarked traffic,
band `10:<mark+1>` netem per class, `fw` filters on the mark):

| Scope | Upload match (forward hook) | Download match | Upload queue | Download queue |
| --- | --- | --- | --- | --- |
| Device / group | `ip saddr <device>` | `ip daddr <device>` in the egress hook | `eth0` egress | `wlan0` egress |
| General / Everyone | `iifname wlan0 oifname eth0 meta nfproto ipv4` | forward marker then egress hook | `eth0` egress | `wlan0` egress |
| Everyone else | `iifname wlan0`, not saved, not protected | marker then egress hook (client MAC) | `eth0` egress | `wlan0` egress |
| Service quota | `ip saddr <device> ip daddr <destinations>` | egress hook | `eth0` egress | `wlan0` egress |
| Service list | `ip daddr @svc_<mark>` | forward marker then egress hook | `eth0` egress | `wlan0` egress |
| Browser service profile | browser proxy sockets with `SO_MARK` | proxy's client socket mark | `eth0` egress (Pi-originated) | `wlan0` egress |
| Outage / blocked | nft `drop` rules, no queue | nft `drop` | none | none |

Unmarked traffic (the Pi's ARP, DHCP, DNS, NTP, SSH, dashboard, unassigned
clients) always used band `10:1` and was never impaired.

On `eth0` egress `skb->data` starts at the Ethernet header, so netem's
corruption (`skb->data[random(skb_headlen)] ^= 1 << random(8)`) can flip a bit
in the destination MAC, source MAC, EtherType or IP header, and the network
card then adds a valid FCS. Duplicated and reordered frames reached the router
as they were.

## H5 confirmed by capture

`tests/kernel/test_upstream_safety.py` captures with a raw socket on the
router's side of the uplink veth. The same harsh device profile (upload delay
5 ms ± 2 ms, loss 5 %, reorder 30 %, duplicate 30 %, corrupt 30 %), 300 client
pings and 60 Pi pings:

| Layout | Bad frames at the router | Bogus source MACs | Duplicate copies | Order inversions | Pi's own pings intact |
| --- | --- | --- | --- | --- | --- |
| Old: netem with damage on `eth0` | 124 (86 ICMP checksum, 19 IPv4 header, 9 destination MAC, 5 source MAC, 5 EtherType) | 5 distinct | 36 | 3 | 60 of 60 |
| New: this change | 0 | 0 | 0 | 0 | 60 of 60 |

The outage-lab agent measured the `daily climb manual` profile with TCP, DNS
and ICMP traffic and found the same kinds of damage, router-side
`IpInHdrErrors`, and 8 to 14 bogus MACs learned by the router's bridge
(branch `test/outage-lab`, `docs/outage-lab-results-2026-10-09.md`). It also
found that upload corruption before forwarding without a further rule lets
packets with a damaged TCP/UDP checksum leave **without NAT** (conntrack marks
them invalid and NetworkManager's shared-mode rule still forwards them). This
change drops them (see below).

The lab proves what the Pi puts on the cable. It does not prove that this
hangs a Vodafone Station. See "Owner experiment".

## The new layout

| Setting | Upload (toward the router) | Download (toward the client) |
| --- | --- | --- |
| Delay, jitter, loss, loss bursts, rate | `eth0` egress, as before | `wlan0` egress |
| Corruption | `jittersim-ifb`, fed from `wlan0` ingress, before routing | `wlan0` egress |
| Duplication | not applied | `wlan0` egress |
| Reordering | not applied | `wlan0` egress |

- `src/jittersim/networking/renderer.py`: `upstream_safe_impairment` removes
  corruption, duplication and reordering from every upload netem on the uplink
  (also on live changes). Corrupting upload classes get a corruption-only netem
  on the ifb device. The intake on `wlan0` is `ingress` qdisc +
  `matchall action connmark continue` + one `fw ... action mirred egress
  redirect dev jittersim-ifb` filter per corrupting class, so only those
  classes pass the ifb device.
- tc ingress runs before nftables, so the class comes from the connection
  mark: the new `upload_class` forward chain (priority 100) stores
  `meta mark & 0xff` in `ct mark`; the browser proxy's marked client sockets do
  the same in the output chain. The forward chain first resets the mark of
  every test Wi-Fi packet so a class restored from an older plan never sticks.
  A connection's first packet has no class at ingress and is not corrupted.
- `iifname wlan0 oifname eth0 ct state invalid drop` in `upload_class`: a
  packet conntrack cannot follow never reaches the router without NAT.
- `src/jittersim/networking/applier.py`: creates and starts `jittersim-ifb`
  only when the plan has upload corruption; if `ip link add ... type ifb`
  fails or a command of the ifb path fails, it resets and applies the same plan
  without upload corruption (never the old placement) and stops trying the ifb
  path for the rest of the helper's life. `ApplyReport.upload_corruption_applied`
  says which. Reset removes the intake and the ifb device.
- Dashboard: the manual Apply message names the upload settings that were not
  applied; the profile form shows a note when upload duplication or reordering
  is set. Without a helper report the dashboard reads
  `/sys/class/net/jittersim-ifb` to know whether corruption ran.

### Why these tools

`tc` with `netem`, `ifb`, `mirred` and `connmark`, and nftables `ct mark` and
`ct state`, are the standard Linux building blocks for ingress shaping (the
same recipe OpenWrt's SQM and many QoS guides use) and ship in every
Raspberry Pi OS kernel as modules. No new daemon or custom packet code is
involved.

### Alternatives considered

- All upload shaping on the ifb device. Rejected: the forward counters would
  count only packets that survived the loss (the dashboard's upload counters
  and four kernel tests depend on offered load), and every connection's first
  packet would miss delay and loss, so a 100 % loss profile would leak first
  packets.
- Duplication and reordering on the ifb device. Rejected: the copies and the
  new order still leave through `eth0` as valid frames, which is exactly what
  the owner ruled out.
- Classify at ingress with tc `flower` filters instead of `connmark`. Rejected:
  it would duplicate the nftables rules (service-list sets, quotas, protected
  MACs, everyone else) in a second language.

### Corruption semantics now

A flipped bit in the Ethernet header at the ifb is ignored (the Pi already
parsed it). A flipped bit in the IP header makes the Pi drop the packet
(`ip_rcv` checks the header checksum). A flipped bit in TCP, UDP or ICMP makes
conntrack mark the packet invalid when it verifies the checksum (packets
without receive checksum offload, like the lab's veths) and the new rule drops
it. When the Wi-Fi driver reports the checksum as already verified, conntrack
skips the check, the packet is NATed and forwarded with its bad checksum and
the far end discards it. Either way the router only receives well-formed
Ethernet and IPv4 headers, and the effect for the test device is the loss of
that packet, as on a real link.

### Cost on a Pi 3B+

Not measured on the Pi. Delay, loss and rate cost the same as before. With
upload corruption active every `wlan0` ingress packet gets one conntrack lookup
(`connmark`) and a `fw` filter walk; packets of corrupting classes also take one
extra queue pass through the ifb device. That is a few microseconds per packet,
small next to the Pi's NAT and Wi-Fi driver cost. Without upload corruption
nothing changes.

## Known limits

- The Pi still sends its download-side damage to the test Wi-Fi clients
  (`wlan0` egress): a corrupted download frame can carry a wrong MAC address on
  the Wi-Fi. That never reaches the router.
- Pi-local replies to a shaped device (DNS, DHCP after the lease, SSH, port
  8443) are shaped on `wlan0` egress by the device's download class, as before.
- Upload rows of the Impact card show the profile's values even where upload
  reordering, duplication or corruption is not applied.
- The helper does not forward `upload_corruption_applied` yet (Panic-gated
  file); the dashboard reads sysfs instead. See the gated branch.

## Owner experiment on the real hardware

Do the dossier's capture checklist first. Then, with this release installed:

1. Before any trial: `sudo tc qdisc show dev eth0 | grep -E 'corrupt [1-9]|duplicate|reorder'`
   must print nothing, also while `daily climb manual` runs. `ip link show
   jittersim-ifb` exists only while a profile with upload corruption runs.
2. Run 1, two to three hours Pi-only with `daily climb manual` applied to the
   household (the profile that ran during both red-PHONE failures), with the
   capture recorders running and this extra recorder on the Pi, which sees
   frames after the queues, as the router receives them:
   `sudo tcpdump -i eth0 -Q out -n -e -w eth0-forged.pcap "not ether src $(cat /sys/class/net/eth0/address)"`.
   It must stay empty (no frame leaves with a source MAC that is not the Pi's).
   Before this change it fills within minutes under that profile.
3. If the router stays healthy, the corruption placement was the trigger (or
   at least a necessary part). If it still hangs, H5 is ruled out as the only
   cause; export the router event log before the power cycle and compare with a
   run without any profile.
