# Router safety audit, round 2 (17 October 2026)

An adversarial audit of everything the Pi can put on the cable toward the
household router (`eth0`) and of everything that can make the router or the
household network misbehave, in every mode. Base: `origin/main` at `b12be90`
(includes PR 125, 133, 135, 140 and 142). Nothing on the real Pi or router was
contacted. Earlier records: `outage-dossier-2026-10-04-2026-10-09.md`,
`pi-only-datapath-audit-2026-10-09.md`, `outage-lab-results-2026-10-09.md`,
`upstream-safe-impairments-2026-10-09.md`, `carry-forward-panic-batch-2026-10-10.md`.

## Summary

- The upload impairment fix of PR 125/135 holds: with every impairment type,
  every scope, protected devices, Panic, Reset and a Wi-Fi restart, the router
  receives no damaged, foreign, copied or reordered frame (capture test below).
- Three real gaps were found by a new router-side capture and are **fixed in
  this PR** (deploy files only, no Panic-pinned file):
  1. **Un-NATed test Wi-Fi packets with no profile running.** TCP resets, FINs
     and SYN-ACKs that connection tracking does not follow left `eth0` with
     their `192.168.50.x` source: 16 of 16 in the lab. The renderer's
     `ct state invalid drop` exists only while a plan is applied; Reset, Panic
     and "profiles stopped" remove it, and NetworkManager's shared-mode rule
     (`ip saddr 192.168.50.0/24 iifname "wlan0" accept`) forwards them.
     Everyday triggers: a phone closing a connection the Pi forgot after a
     reboot, Panic clearing DNS connection state (`access.py`
     `_clear_dns_connections`), late segments after a connection closed. The
     same leak hit new flows while NetworkManager's NAT table was gone during
     a Wi-Fi restart (Recovery, the boot rename, a router Wi-Fi copy).
  2. **The Pi answered ARP on `eth0` for its Wi-Fi address `192.168.50.1`**
     (Linux default `arp_ignore=0`). With the router LAN on `192.168.50.0/24`
     and the router on `.1` (a common factory default, refused by the installer
     but possible after a router change) the Pi claims every household
     device's gateway. Reproduced in the lab.
  3. **The Pi routed the household LAN.** A packet for another LAN host sent to
     the Pi's MAC was forwarded back onto the LAN with an ICMP redirect to the
     sender.
- No defect needs a Panic-pinned file change. The fixes live in the
  management firewall, which is loaded in every mode.
- Biggest remaining risk is a design one, not a wire defect: since PR 140 the
  Pi comes up on the router's Wi-Fi name at every boot, so after a household
  power cut both radios advertise the same name and password with two subnets
  (owner decision D1 below).

## Fixes in this PR

| File | Change | Why |
| --- | --- | --- |
| `deploy/raspberry-pi/jittersim-control-firewall.nft` | New `postrouting` chain (priority 300, after NAT): `oifname uplink ip saddr 192.168.50.0/24 drop` | One invariant for every cause: nothing leaves toward the router with a test Wi-Fi source |
| same | `forward`: `iifname uplink oifname uplink drop` | The Pi is a gateway for its Wi-Fi only, never a router for the LAN |
| `deploy/raspberry-pi/jittersim-control-firewall.service` | `ExecStartPre` sets `net.ipv4.conf.all.arp_ignore=1`, `arp_announce=2` and `send_redirects=0` on every interface | No ARP answer for the Wi-Fi address on the LAN, the uplink address in ARP requests, no ICMP redirects (sent before the forward hook, so nft alone cannot stop them) |
| same | `Before=NetworkManager.service` | The rules load before any connection starts and are removed only after NetworkManager stopped the Wi-Fi at shutdown |
| `tests/kernel/test_router_wire_guard.py` | The router-side capture test (below) | Closes the gaps with tests, not prose |
| `tests/unit/test_deployment.py` | Static checks of the new rules and unit lines | Fast guard in the unit suite |

Install (`install.sh`) and update (`updates.py` `SERVICE_STEPS`) both copy this
unit and the rules file and restart the unit, so both paths deliver the fix.
The kernel settings are plain `/proc/sys` writes so they need no extra package.
Tools: nftables and the kernel's own ARP and redirect settings, the standard
Linux knobs for a multi-homed NAT gateway. No new code runs on the Pi.

## Router-side capture test

`tests/kernel/test_router_wire_guard.py` runs in the normal kernel image
(`scripts/local-ci.sh kernel`). Topology: `tests/kernel/lab.py` namespaces (router,
Pi, two Wi-Fi clients) plus NetworkManager's shared-mode nftables table and
dnsmasq command line with the installer's `dnsmasq-shared.d` (from
`outage_lab.py`), the shipped management firewall and its unit's kernel
settings, a DNS server on the router, and the real `NetworkApplier`.

During each scenario the clients send UDP, TCP, ICMP, 30 repeated and 10
distinct DNS questions, mDNS, SSDP, LLMNR, NetBIOS, limited and subnet
broadcasts, multicast with TTL 8, IPv6 toward the Pi, and TCP RST, FIN,
SYN-ACK and RST-ACK segments for no known connection. The router side sends
ARP for the Pi's uplink, Wi-Fi and client addresses, a DHCP DISCOVER, a DHCP
REQUEST for a Pi lease (`192.168.50.150`), an IPv6 router solicitation, a
DHCPv6 SOLICIT, NetBIOS and mDNS questions, and a packet for another LAN host
addressed to the Pi's MAC.

The test fails on: a source MAC other than the Pi's; a unicast destination
other than the router or an odd group address; an EtherType other than IPv4,
ARP, IPv6; a damaged IPv4 header or length; a damaged ICMP message; an IPv4
source other than the Pi's uplink address (test Wi-Fi addresses reported
separately); an IPv6 source that is not the Pi's; ARP from the Pi for any
address but its uplink address, or for a target outside the uplink network; a
DHCP or DHCPv6 server reply; an IPv6 router advertisement; an ICMP or ICMPv6
redirect; an IGMP query; mDNS, SSDP, LLMNR or NetBIOS (the lab runs no Avahi,
so any such frame is reflected); any other IPv4 broadcast or multicast; a
byte-identical copy of a forwarded frame within 1 s; more than 10 broadcast or
multicast frames or 10 ARP frames in any second; more DNS questions to the
router than distinct names asked (amplification). Positive controls: the Pi's
ARP reply for its uplink address must be seen, forwarded traffic must arrive
(except in blocking scenarios), and IPv6 forwarding must stay off.

| Scenario | Before this PR | After |
| --- | --- | --- |
| No profile running (also after Reset and Panic) | **16 un-NATed 192.168.50.10 frames**, 2 ARP answers for 192.168.50.1, 2 forwarded LAN packets, 1 ICMP redirect | clean |
| Device profile: delay, jitter, loss, burst loss, duplication, reordering, corruption, rate, blackhole (100 % loss), every setting | ARP for 192.168.50.1, forwarded LAN packet, redirect (no damage, copies or leaks: PR 125 holds) | clean |
| Everyone: duplication, reordering, corruption, every setting | same as above | clean |
| Outage for Everyone | same | clean |
| Everyone else with a protected device; everyone else disconnected | same | clean |
| Protected devices under a profile for Everyone | same | clean |
| Blocked website, website allowlist, service list, service quota, disconnected device | same | clean |
| Panic or Reset while traffic runs (tables and queues removed mid-flow) | not measured separately (same causes) | clean |
| Wi-Fi restart while traffic runs (NAT table gone 1.5 s, link down and up) | leak of new flows (cause 1) | clean |
| Management firewall restart while traffic runs (install, update) | 16 un-NATed frames, as without profiles | clean |
| Router LAN on 192.168.50.0/24, router on .1 | **Pi answers ARP for 192.168.50.1** | clean |
| DHCP on eth0, also in the same-subnet case and with `dhcp-authoritative` | silent (no DHCPOFFER or DHCPNAK) | silent |

Lab runs on 17 October (colima, kernel image built from this branch): before
the fix 22 failures in 13 tests; after the fix 13 tests in 503 s, all pass. The
same-subnet "before" run removed the unit's kernel settings only.

What the lab cannot emulate: real Wi-Fi association and roaming between two
radios, NetworkManager itself (its effects are reproduced from its source),
Avahi on `eth0` (checked by `jittersim.mdnsname` tests and outage lab
scenario 7), the router's firmware, Pi 3B+ timing and checksum offload on the
Pi's Wi-Fi driver.

## Matrix: wire behaviour by mode

Modes: **P** profile active (any impairment, any scope including Everyone,
everyone else, service rules, quotas), **S** profiles stopped, Reset or Panic,
**R** Recovery or a Wi-Fi rename or copy (AP restart), **B** boot and shutdown,
**F** Pi on the router's Wi-Fi name (PR 140 follow).

| # | Wire behaviour toward the router | P | S | R | B | F | Evidence | Verdict | Residual risk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Damaged frames (netem corruption on eth0) | ok | ok | ok | ok | ok | `renderer.py:93-104` `upstream_safe_impairment`; wire guard all P scenarios; `test_upstream_safety.py` | safe | Rollback to a release before PR 125 (carry-forward doc: reboot after rollback) |
| 2 | Duplicated or reordered uploads | ok | ok | ok | ok | ok | `renderer.py:104`, `test_upstream_safety.py` order and copy checks, guard copy check | safe | none |
| 3 | Non-Pi source MAC | ok | ok | ok | ok | ok | no bridge anywhere; guard source MAC check | safe | none |
| 4 | Test Wi-Fi source IP without NAT | ok | **was leaking** | **was leaking** | **gap at shutdown** | ok | guard "no profile", "Wi-Fi restart"; fix: control firewall `postrouting` | fixed | A rollback to an older release removes the rule until it is reinstalled |
| 5 | Pi forwards for other LAN hosts, ICMP redirects | was open | was open | was open | was open | was open | guard hairpin probe; fix: uplink-to-uplink drop, `send_redirects=0` | fixed | none |
| 6 | ARP: sender address not the uplink address | was open | was open | was open | was open | was open | guard ARP probe for 192.168.50.1; same-subnet test; fix: `arp_ignore=1`, `arp_announce=2` | fixed | Settings live until reboot after a rollback to an older release, then return to the default |
| 7 | Gratuitous ARP on eth0 | ok | ok | ok | ok | ok | NetworkManager announces only on the interface it configures (wlan0 for the AP); guard saw none | safe | none |
| 8 | DHCP server on eth0 | ok | ok | ok | ok | ok | dnsmasq `--listen-address=192.168.50.1 --bind-interfaces`, no context for eth0; guard DISCOVER and REQUEST probes, also same-subnet with `dhcp-authoritative` | safe | none |
| 9 | 192.168.50.0/24 equal to the router LAN | n/a | n/a | n/a | n/a | n/a | `preflight.py:68` refuses at install; same-subnet test now clean on the wire | contained | The Pi's own routing breaks in that case (two connected routes); no runtime refusal (D3) |
| 10 | IPv6: RA, DHCPv6, forwarded IPv6 | ok | ok | ok | ok | ok | AP `ipv6.method disabled` (`install.sh:222`), forwarding never enabled; guard RS and SOLICIT probes, IPv6 from a client | safe | The Pi itself is an ordinary IPv6 host on eth0 (RS, NS, MLD) |
| 11 | mDNS, SSDP, NetBIOS, LLMNR reflection or loops | ok | ok | ok | ok | ok | Avahi `enable-reflector=no` (`mdnsname.py:120-130`), no bridge, no multicast routing; guard chatter from both clients | safe | Avahi publishes the dashboard name on eth0 (one name, Avahi's own rate) |
| 12 | IGMP or multicast routing | ok | ok | ok | ok | ok | no mrouted, igmpproxy or smcroute; guard multicast with TTL 8 | safe | none |
| 13 | MTU, MSS, fragments | ok | ok | ok | ok | ok | both links 1500, no tunnel on the Pi; ICMP errors are RELATED and pass NM's rule; conntrack refragments to the original sizes | safe | The router's DS-Lite MTU clamping is the router's job, same as without the Pi |
| 14 | Conntrack table toward the router | bounded | bounded | bounded | bounded | bounded | outage lab scenario 6; the router sees one host (the Pi) | acceptable | In Pi-only mode the whole household is one router host: a per-host session cap on the router (unknown for the TG3442DE) could be hit (H1) |
| 15 | DNS rate to the router | ok | ok | ok | ok | ok | dnsmasq caches (guard: no amplification); Unbound forwards only to the Pi's dnsmasq (`access.py:146`), no recursion | safe | Equal to the household's own query rate in Pi-only mode |
| 16 | Router web UI logins | ok | ok | ok | ok | ok | manual only (`dashboard.js` `pollRouterWifi` bound to a click); one shared budget, at most one login per 15 s, 30 s after a failure (`router_operations.py:21-22`); writes refused in the helper (`helper.py:1592-1595`) | safe | A direct helper call (root) bypasses the dashboard budget |
| 17 | Router health checks | ok | ok | ok | ok | ok | gateway ping every 5 s (`helper.py:174`), canary one ping and one DNS question per minute (`router_canary.py:33`) | safe | none |
| 18 | DHCP renewals of the Pi | ok | ok | ok | ok | ok | NetworkManager's client renews at half the router lease | safe | none |
| 19 | Update checks, NTP | ok | ok | ok | ok | ok | update check once a day (`updates.py:2627`), helper looks every 15 min whether it is due; NTP is systemd's | safe | none |
| 20 | Download damage reaching household devices | **reaches shaped unprotected Wi-Fi devices** | ok | ok | ok | worse | `upstream-safe-impairments-2026-10-09.md` "Known limits": corrupted download frames can carry a flipped destination MAC on the Wi-Fi | by design | In Pi-only mode with a profile for Everyone, household devices receive damaged frames, some as Wi-Fi broadcast. Never reaches the router |
| 21 | Two radios with one name (dual AP) | n/a | n/a | n/a | **every boot** | yes | `wifi_follow.py:224-253` `prepare_boot` renames to the router name, never checks whether the router radio is on | design risk | D1 |

## Prioritized fix list

1. Done here: the four management firewall and kernel setting changes above (S1 wire defects 4, 5, 6).
2. **D1, owner decision (high).** Dual AP after every boot. A household power
   cut brings the router Wi-Fi back on by default and the Pi back on the router
   name. Devices then roam between two subnets under one name ("connected but
   cannot browse", `pi-only-datapath-audit-2026-10-09.md` item 1). Proposal:
   in `wifi_follow boot` (runs before the AP starts, `wlan0` still idle), run
   one passive `nmcli -t -f SSID,BSSID device wifi list --rescan yes ifname wlan0`
   and, when the router name is on the air from another BSSID, keep or switch
   to `JitterSim-Recovery` and log why. This never contacts the router. It
   changes what PR 140 does, so it is not implemented here.
3. **D2, owner decision (medium).** Download corruption in Pi-only mode reaches
   household devices on the Wi-Fi. Option: refuse corruption and duplication
   for the Everyone profile while the Pi uses the router name.
4. **D3 (low).** A runtime refusal to open the Wi-Fi when `eth0` holds an
   address in `192.168.50.0/24` (`jittersim-ap.service` `ExecStartPre`). The
   wire is already clean in that case (test above), so this only protects the
   Pi's own routing.
5. Low: make the management firewall reload atomic (`add table` and
   `delete table` inside the rules file) so an update restart has no gap of a
   few milliseconds. The restart test passes today because the gap is short.
6. Carried over, unchanged: the rollback cleanup of the upload intake
   (`carry-forward-panic-batch-2026-10-10.md`).

No proposed change needs a Panic-pinned file (`panic_guard.py`,
`networking/helper.py`, `networking/access.py`), so there is no patch file.
`access.py` `_clear_dns_connections` (Panic and rule changes) was a trigger for
leak 4; the firewall invariant covers it without touching that file.

## Read-only checks for the real hardware

Run on the Pi over Ethernet or SSH from the router network, after this release
is installed. Nothing here changes the Pi or contacts the router's web page.

1. Settings and rules are in place:
   `cat /proc/sys/net/ipv4/conf/all/arp_ignore /proc/sys/net/ipv4/conf/all/arp_announce`
   (expect `1` and `2`), `grep . /proc/sys/net/ipv4/conf/*/send_redirects`
   (all `0`), `sudo nft list chain inet jittersim_control postrouting`.
2. Nothing leaves with a test Wi-Fi address, a foreign MAC or a server answer
   (run for the whole Pi-only trial; each must stay empty):
   `PI_MAC=$(cat /sys/class/net/eth0/address)`
   `sudo tcpdump -i eth0 -Q out -n -e -c 50 "src net 192.168.50.0/24 or not ether src $PI_MAC or udp src port 67 or udp src port 547 or (icmp6 and ip6[40] == 134) or (icmp and icmp[0] == 5)"`
3. ARP answers only for the uplink address:
   `sudo tcpdump -i eth0 -Q out -n -e "arp and not arp src host $(ip -4 -o addr show dev eth0 | awk '{print $4}' | cut -d/ -f1)"`
   must stay empty.
4. The drop counters show how often the invariant fired (a number above zero
   means it stopped a leak):
   `sudo nft list ruleset | grep -E "never send a test Wi-Fi address|never route the household LAN"`.
5. Dual AP (D1): before switching the router Wi-Fi off, from any Linux device
   or the Pi before its AP starts, `nmcli -f SSID,BSSID,CHAN device wifi list`
   shows whether the router name comes from two BSSIDs.
6. Router side, only with the owner's agreement and only reading: the router
   event log export and uptime before and after a trial, as in the dossier.

## Gate

See the pull request for the local gate results on the exact head.
