# Pi-only mode data path audit (2026-10-09)

Scope: every way the JitterSim code and deploy configuration can leave a device
associated to the Pi access point without working internet, make the household
worse off after a copied-name cutover, or disturb the upstream router. Source:
`origin/main` at `20936e2`, plus the Pi revision that ran on 4 October
(`081b2ab`, per `docs/hardware-capacity-assessment-2026-10-04.md`) where it
differs. No Pi or router was contacted.

## Summary

The basic Pi data path (NetworkManager shared mode: DHCP, DNS forwarding, NAT
through `eth0`) works. The owner browsed through it on 4 October on the
separately named recovery network and, that morning, on the copied name after
toggling Wi-Fi. What fails is the hand-over between the router and the Pi under
one Wi-Fi name. The Pi serves a different subnet (`192.168.50.0/24`) than the
router (`192.168.0.0/24`), and two Pi-side defaults made a device that moves
between them keep an address from the other subnet:

1. The Pi's DHCP server (NetworkManager's dnsmasq) was not authoritative. A
   device that joins the copied name and asks again for its router address gets
   no answer at all, so it waits and may keep `192.168.0.x`. This is proven in
   dnsmasq's source and with the real dnsmasq 2.90 below. **Fixed.**
2. Pi leases lasted one hour. A device that moves back to the router under the
   same name keeps `192.168.50.x` until it rebinds at 7/8 of the lease, which is
   up to about 52 minutes. **Fixed** (5 minutes).

These two explain "associated but cannot browse" and "router Wi-Fi back on did
not restore browsing" well, if the devices did not do a full DHCP discovery.
They do not explain the red PHONE light, the router's `AP-DISABLED` entries or
the need for a router restart. Apart from profiles, nothing in the Pi code sends
traffic that could disturb the router. With a corrupting profile it does: upload
corruption runs on `eth0` and puts frames with flipped MAC addresses, EtherTypes
and IP headers (valid FCS) on the router's LAN port (measured, see "Impaired
upload traffic on the router side"). That is the only Pi-side mechanism found
that fits the red-PHONE failures.

## Ranked defects

Ranked by how well each explains the 4 October symptoms. "Confirmed" means
shown by code or by a real-tool test here. "Needs a test" means it needs the
real devices.

| # | Where | Trigger | Consequence (how the owner sees it) | Status | Explains 4 Oct |
|---|-------|---------|-------------------------------------|--------|----------------|
| 1 | Design: `networking/wifi.py:197-224` copies only the name and security; `deploy/raspberry-pi/install.sh:213-215` gives the AP its own NAT subnet | Router and Pi broadcast the same name, or a device moves between them | A device that roams between the two radios usually keeps its IP address (a roam is not a new network to the device). It is "connected" but its address and gateway belong to the other subnet: associated but cannot browse. The 4 October morning record shows it: the Air reported `192.168.0.222` after selecting the copied name while both radios were on. | Confirmed by design and by the morning record. Not fixable without bridge mode. | High while both radios are on. The recorded 22:05 trial did not turn router Wi-Fi off |
| 2 | NetworkManager's dnsmasq for the AP (no `--dhcp-authoritative`), dnsmasq `src/rfc2131.c` INIT-REBOOT branch: `if (!lease && !option_bool(OPT_AUTHORITATIVE)) return 0;` before the "wrong network" NAK | A device joins the copied name and asks again for its router lease (INIT-REBOOT DHCPREQUEST for `192.168.0.x`) | dnsmasq stays silent. The device retries. RFC 2131 3.2 lets it keep the old address until that lease expires. Associated but cannot browse, for seconds or longer depending on the device | **Confirmed** with dnsmasq 2.90 (`tests/kernel/test_shared_dhcp.py`). **Fixed** in this PR | High when router Wi-Fi was off and devices rejoined without toggling Wi-Fi |
| 3 | `install.sh:208-216`: no `ipv4.shared-dhcp-lease-time`, so NetworkManager's 3600 s | A device on the Pi moves back to the router under the same name (router Wi-Fi back on, Pi AP still up, or the AP renamed by a rollback) | It keeps `192.168.50.x` with gateway `192.168.50.1`. Renewal goes to the Pi and fails. Only the rebind at 7/8 of the lease reaches the router. Up to about 52 minutes without internet on the router | Lease length confirmed. The router's answer to a foreign INIT-REBOOT needs a test. **Fixed** (300 s) | Medium-high: "router Wi-Fi back on did not immediately restore browsing" and "router clients could not connect after the Pi was unplugged" |
| 4 | Per-network private Wi-Fi addresses (iOS, macOS, Android, Windows) against MAC-keyed state: protected devices `networking/renderer.py:291-300`, household MAC rules `networking/access.py:219-265`, saved dashboard devices `networking/dashboard_access.py` | The Pi takes the router's name, so each device uses the private MAC it uses with the router, not the one saved on TestNet | Protected devices lose protection. A MAC allowlist or "everyone else" profile hits them. The dashboard asks for the device again. The 4 October morning record shows the Air with a new MAC (`d6:ac:49:...`) that had to be protected again | Confirmed by the record. Not fixed (needs a re-identification step in the cutover flow) | Low: rules were off and profiles stopped on 4 October |
| 5 | `networking/access.py:444-477, 556-581` (Panic-gated) | Panic or Normal access while hostapd admission, conntrack or the nft load fails | The release rolls back to the old restrictive rules. If that also fails, the transition guard stays and every Pi Wi-Fi device loses forwarding: in Pi-only mode the whole household | Confirmed by code and unit tests. **Fix on branch `fix/pi-only-datapath-gated`**, waiting for review | Low: Normal access returned success before the trial |
| 6 | `networking/access.py:603-624` (Panic-gated) | Household DNS rules on and a listed device gets a new address | The 15 s address refresh runs a full transaction with the transition guard: all forwarding stops while Unbound restarts (seconds, up to about 35 s with probes). Repeats on every address change | Confirmed by code. **Fix on branch `fix/pi-only-datapath-gated`** | None on 4 October (rules paused). Candidate for "erratic connections" |
| 7 | `networking/access.py:280-292` | Household DNS rules on | Every Pi Wi-Fi device's DNS is redirected to Unbound, not only the restricted devices. If Unbound cannot start, the whole household loses DNS (fail closed by design) | Confirmed by code. Not changed: it is a policy decision (see "What remains") | None on 4 October |
| 8 | `networking/applier.py` `_root_queue` | Normal access, Panic or the router-outage reset while `wlan0` is missing (Wi-Fi off, firmware crash) | The reset fails and Panic reports "not fully restored". Seen on 4 October ("failed while inspecting the absent wlan0 root queue") | Confirmed. **Fixed** in this PR | Recovery reporting only |
| 9 | Hardware: Pi 3B+ onboard Wi-Fi (CYW43455, fullmac firmware) | More than about 7-8 household devices | Further devices cannot join. Community reports, no official figure ([OpenWrt forum](https://forum.openwrt.org/t/remove-limit-of-8-devices-on-wifi-network-on-raspberry-pi/128734), [DietPi forum](https://dietpi.com/forum/t/wifi-hotspot-number-of-clients-limited/18401), [RaspAP issue 462](https://github.com/RaspAP/raspap-webgui/issues/462)) | Needs a test on the Pi | None (two devices) but decisive for a full household |
| 10 | `install.sh:181, 212` (`ap-isolation yes`), `jittersim-control-firewall.nft:39-47` | Pi-only mode | Household devices cannot see each other (AirPlay, Chromecast, Wi-Fi printers) or reach the router's network (wired printers, NAS, router page). No IPv6 for the household (`ipv6.method disabled`). AP band left to NetworkManager (2.4 GHz on 4 October) | Confirmed by configuration. Intended for a test network, a regression for a household | None |

## Fixed in this PR

- `src/jittersim/localname.py`: `install_config` also writes
  `/etc/NetworkManager/dnsmasq-shared.d/jittersim-shared-dhcp.conf` with
  `dhcp-authoritative`, and reports a change so `install.sh` tells the owner to
  restart the Wi-Fi. This is a setting of the mature DHCP server already in use,
  not new code. The dashboard update path calls the same function
  (`updates.py prepare_dashboard_identity`), so both install paths deliver it. It
  takes effect when the test Wi-Fi next starts (reboot, `sudo nmcli connection up
  jittersim-ap`, or the next Wi-Fi copy, which restarts the connection).
- `deploy/raspberry-pi/install.sh`: `ipv4.shared-dhcp-lease-time 300` and the
  lease time in the before/after comparison. Only `install.sh` changes the AP
  profile; a dashboard update does not.
- `src/jittersim/networking/applier.py`: a reset treats "Cannot find device" on
  the root-queue inspection as "no project queue". Other errors still fail.
- Tests: `tests/kernel/test_shared_dhcp.py` runs the real dnsmasq with
  NetworkManager 1.50's shared-mode command line in network namespaces and sends
  a real INIT-REBOOT for a router address: no answer without the setting,
  DHCPNAK with it, then a normal offer; a lost Pi lease is confirmed (ACK).
  `tests/kernel/Dockerfile` installs `dnsmasq-base`. Unit tests in
  `test_local_name.py`, `test_wifi_installation.py` and `test_applier.py`.

## Waiting for review (Panic-gated)

Branch `fix/pi-only-datapath-gated`, `src/jittersim/networking/access.py` only:

- A release (household rules off) never rolls back to the rules it removes.
  hostapd admission and DNS conntrack failures are logged; a refused admission
  change is still reported as a failure after forwarding is open. If the
  switched-off rule set cannot load (`nft --check` or the load fails), the access
  tables and the transition guard are deleted instead (checked with real nft).
- `refresh_addresses` applies without the transition guard (`apply(..., guard=False)`).
- Four new unit tests; `tests/kernel/test_access.py` passes (18 tests) with the
  change. The Panic hash gate tests fail until a reviewed bundle is added, as
  expected.

## Impaired upload traffic on the router side (hypothesis H5)

Added after the outage dossier (`docs/outage-dossier-2026-10-04-2026-10-09.md`)
showed both red-PHONE failures while `daily climb manual` (upload 6 % corruption,
7 % duplication, reordering; download 2 % corruption, 3 % duplication) was
active, and none during Pi-only runs without impairments. Ranked by how directly
each point bears on the router.

| # | Finding | Evidence | Status |
|---|---------|----------|--------|
| H5-1 | **Upload impairments run on the Ethernet port toward the router.** Every upload queue of every scope is a netem child of the `eth0` root: device and group assignments (forward mark rules, `renderer.py:376-380`), general profile / Everyone (`renderer.py:333-339`), everyone else (`renderer.py:180-186`), service quotas and lists (`renderer.py:416-420, 452-454`). `render_command_plan` sends all upload queues to `topology.uplink_interface` (`renderer.py:596-602`). Live changes do the same (`render_live_queue_changes`, `renderer.py:633`). Download queues sit on `wlan0` egress. | The emitted rules for `daily climb manual` as the general profile: `tc qdisc replace dev eth0 parent 10:2 handle 51: netem delay 40ms 20ms reorder 10% duplicate 7% corrupt 6% rate 10gbit` with `tc filter ... dev eth0 ... handle 1 fw flowid 10:2`, and `nft ... forward iifname "wlan0" oifname "eth0" meta nfproto ipv4 meta mark set 1` (rendered from `main` with `render_command_plan`) | Confirmed |
| H5-2 | **netem corruption reaches the Ethernet header on egress, so the router receives frames with a valid FCS and a flipped bit in the destination MAC, source MAC or EtherType, or an IP header with a bad checksum.** netem flips one bit at a random offset of `skb_headlen` (`net/sched/sch_netem.c`, `skb->data[get_random_u32_below(skb_headlen(skb))] ^= 1<<...`), and on egress `skb->data` starts at the Ethernet header. The NIC adds a correct FCS afterwards, so the router cannot discard these as damaged. | Real kernel (6.8) with JitterSim's upload shape on a veth "uplink", 600 marked UDP frames at 100 % corruption: 79 wrong destination MAC (some with the multicast bit set), 53 wrong source MAC (33 distinct bogus addresses, single-bit neighbours of the Pi's MAC, one with the multicast bit), 23 wrong EtherType, 200 bad IPv4 header checksum. At 6 % corruption plus 7 % duplication: 640 marked frames with 2 bogus source MACs, 3 wrong destination MACs, 1 wrong EtherType, 8 bad IP checksums. Script: `tests/kernel/netem_uplink_probe.py` | Confirmed |
| H5-3 | **What the router learns.** A bogus source MAC is learned by the router's LAN switch as a new host on the Pi's port (up to 48 single-bit variants of the Pi's MAC, more with duplication). A destination MAC that no longer matches the router is flooded as unknown unicast or, with the multicast bit, as multicast to all its ports. A bad IPv4 checksum is dropped by a standards-compliant IP stack, so ARP and routing tables should not learn bogus IP addresses from these frames (ARP itself is never corrupted, see H5-4). Whether the Vodafone Station's switch, host table or hardware packet processor misbehave on such frames is not known. | Reasoning from the measured frames. The router's reaction needs a test on the router (only with the owner's agreement) | Needs a test |
| H5-4 | **Pi-originated traffic is not impaired.** The `eth0` root is `prio ... priomap 0 ...` (`renderer.py:464-500`): every packet without a matching `fw` mark goes to band `10:1` (`fq_codel`). Marks are set only in the forward hook for traffic from `wlan0`. ARP, DHCP, DNS (dnsmasq to the router), mDNS, NTP, SSH and the dashboard are unmarked. The one exception is the browser proxy: its upstream sockets carry the client's mark (`browser_proxy.py:211`), so they are impaired like the client's own traffic. | Same experiment: 95 and 49 unmarked frames, none changed | Confirmed |
| H5-5 | **Scope multiplies it.** A general profile or Everyone profile marks all IPv4 uploads of every unprotected device. In Pi-only mode that is the whole household, and every corrupted frame reaches the router. On 4 October the profile was "global" in event A. | Dossier timeline and the general-profile rules above | Confirmed by configuration |

Safer designs, in order of preference:

1. Never corrupt (or duplicate) frames on the uplink. Apply upload impairments on
   `wlan0` ingress through an `ifb` device (`tc filter add dev wlan0 ingress ...
   action mirred egress redirect dev ifb0`, netem on `ifb0`). At ingress
   `skb->data` starts at the IP header, so a flipped bit lands in the IP packet,
   and the Pi's own IP stack drops a bad IP header before forwarding. The router
   then only receives well-formed Ethernet frames carrying valid IP headers
   (payload damage still fails the TCP/UDP checksum at the far end, which is the
   realistic effect). Reordering, duplication, delay and loss keep their effect.
2. Until then, keep corruption to the download side (`wlan0` egress) and refuse
   `corrupt_percent > 0` on upload queues, or map upload corruption to loss
   (a corrupted frame on a real link fails its FCS and is lost).
3. Keep impairments to mark-matched classes only (as today) and never put
   unmarked or Pi-local traffic into a netem class. The browser proxy's marked
   upstream sockets would move to the ifb path with design 1.

Not implemented here: designs 1 and 2 change what a profile does, so they need
the owner's decision. The dossier's rule stands meanwhile: no corruption,
duplication, reordering or loss on the household.

## Router disturbance without profiles: checked, nothing found

- DHCP: only NetworkManager's dnsmasq, bound to `192.168.50.1` with
  `--bind-interfaces`. Nothing serves DHCP on `eth0`.
- IPv6: the AP has `ipv6.method disabled`; no router advertisements or DHCPv6
  from the Pi. Unbound has `do-ip6: no`.
- mDNS: Avahi publishes nothing by itself, the reflector is off
  (`mdnsname.py:108-119`); one name record per network.
- No SSDP, UPnP, LLDP or ARP proxy code. The gateway probe is one ICMP echo every
  5 s (`helper.py:117`, `applier.py:376-410`).
- Router logins only on explicit dashboard actions (status, password check,
  preview and copy); router Wi-Fi writes are refused since PR 87.
- NAT: the router sees the household as one host (`192.168.0.17`). Conntrack use
  on the 1 GB Pi was not measured.

## What remains

- Item 1 needs a decision. Options: keep the routed design and always switch the
  router Wi-Fi off before the Pi takes the name (and back on only after the Pi
  gave it up), or bridge `wlan0` into the router LAN so devices keep one subnet
  (shaping then needs bridge-aware rules). A cutover that turns router Wi-Fi off
  while devices keep their association is the case items 2 and 3 mitigate.
- Item 4: the cutover flow should list devices by their router-network MAC (the
  router's client list or the Pi's station list after the switch) and
  re-protect the owner's devices before profiles start.
- Item 7: decide whether household DNS rules should redirect only the listed
  devices so an Unbound failure cannot take everyone's DNS.
- Gap audit items 1, 3 (Panic lock wait), 4 and 6 remain (helper.py, gated).

## What to capture on the Pi during the next attempt

Run over Ethernet or the router network, read-only, before and during the
trial. Note the clock of each source.

1. `nmcli -g ipv4.shared-dhcp-lease-time,802-11-wireless.band,802-11-wireless.ssid connection show jittersim-ap`
   and `ls /etc/NetworkManager/dnsmasq-shared.d/` (expect `jittersim-shared-dhcp.conf`).
2. `ps -o args= -C dnsmasq` (the running command line, to confirm `--conf-dir`).
3. `sudo journalctl -u NetworkManager --since -15min | grep -i dnsmasq` with
   `NM_DNSMASQ_DEBUG` not needed: dnsmasq logs DHCPNAK "wrong network" and
   DHCPACK lines to the journal by itself.
4. `iw dev wlan0 station dump | grep -c Station`, `cat /var/lib/NetworkManager/dnsmasq-wlan0.leases`.
5. `sudo nft list ruleset | grep -c jittersim_access_transition` (expect 0),
   `sysctl net.ipv4.ip_forward`, `ip -4 route`, `sudo conntrack -C`.
6. On each affected device at the moment it "cannot browse": its IP address,
   gateway and DNS server (iPhone: Settings > Wi-Fi > (i); Mac: `ipconfig getpacket en0`;
   Android: Wi-Fi details). A `192.168.0.x` address on the Pi or a
   `192.168.50.x` address on the router confirms items 1-3.
   Before any trial: `sudo tc qdisc show dev eth0 | grep -E 'corrupt|duplicate|reorder|loss'`
   must print nothing (no impairment toward the router).
7. Whether router Wi-Fi was on or off at that moment, and whether the device
   toggled Wi-Fi.
8. Router: WAN mode (dual stack or DS-Lite), Phone and Internet LEDs, uptime and
   the event log export with its clock, before and after.
