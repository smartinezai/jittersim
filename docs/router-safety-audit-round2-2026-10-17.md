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
  For corruption the strong evidence is still `tests/kernel/test_upstream_safety.py`:
  the new wire guard checks IPv4 header and ICMP checksums but not TCP or UDP
  payload checksums (on the lab's veths those are filled in after the
  corruption point).
- Three real gaps were found by a new router-side capture and are **fixed in
  this PR** (deploy files only, no Panic-pinned file). Severity on the owner's
  scale: 1 is S2, 2 and 3 are S3.
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
  Pi comes up on the router's Wi-Fi name at every boot without checking whether
  the router's own Wi-Fi is on. When the router Wi-Fi is on again both radios
  advertise the same name and password with two subnets (owner decision D1
  below).

## Fixes in this PR

| File | Change | Why |
| --- | --- | --- |
| `deploy/raspberry-pi/jittersim-control-firewall.nft` | New `postrouting` chain (priority 300, after NAT): `oifname uplink ip saddr 192.168.50.0/24 fib saddr . oif type != local drop` | One invariant for every cause: nothing leaves toward the router with a test Wi-Fi source, not even the Pi's own `192.168.50.1`. Only an address of the uplink itself passes, so the Pi's Ethernet traffic keeps working on a router LAN that also uses 192.168.50.0/24 |
| same | The file starts with `add table` and `delete table`, so loading it replaces the table in one transaction | An install or update reload never leaves a moment without the rules, and a file that fails to load keeps the old ones |
| same | `forward`: `iifname uplink oifname uplink drop` | The Pi is a gateway for its Wi-Fi only, never a router for the LAN |
| `deploy/raspberry-pi/jittersim-control-firewall.service` | `ExecStartPre` sets `net.ipv4.conf.all.arp_ignore=1`, `arp_announce=2` and `send_redirects=0` on every interface | No ARP answer for the Wi-Fi address on the LAN, the uplink address in ARP requests, no ICMP redirects (sent before the forward hook, so nft alone cannot stop them) |
| same | `Before=NetworkManager.service`; no separate `nft delete` steps and no `ExecStop`; `TimeoutStartSec=30` | The rules load before any connection starts. A restart (install, update) no longer deletes the table first, and shutdown never removes it while the Wi-Fi still runs. If nft hangs, the unit fails after 30 s: NetworkManager then starts and Ethernet comes up (the dashboard over Ethernet and SSH stay reachable once their own units start), but the test Wi-Fi stays off because `jittersim-ap.service` refuses to start without this table. That is the safe failure |
| `deploy/raspberry-pi/preflight.py`, `install.sh` | `nft --check -f -` of the rendered rules against the running kernel. The updater already runs the staged `preflight.py` before it stops any service; `install.sh` runs it again with `--firewall-only` right after installing nftables | The rules use `fib` for the first time. A kernel without `nft_fib_inet` or `nft_fib_ipv4` would fail to load the whole file; the dashboard, browser proxy and service watcher need this unit. The check stops the install or update with a clear message before anything is stopped or replaced |
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
router than distinct names asked (amplification); a copy of a numbered upload
datagram or echo request, or numbered datagrams out of order. Positive
controls: every traffic generator and the LAN probes must finish (a crash fails
the test), the Pi's ARP reply for its uplink address and its own ping reply must
be seen, UDP, ICMP and (where the profile does not drop it) TCP must reach the
router, the router must get at least one DNS question, and IPv6 forwarding must
stay off. In the scenarios without a JitterSim plan, Panic and Reset, and the
Wi-Fi restart, the drop counter of the new postrouting rule must rise: that
proves the untracked segments or the fresh flows without NAT were really sent
in the moment that matters. The transition scenarios send fresh flows inside
the gap (NetworkManager's NAT table removed, after a reset, during five
firewall reloads), not only around it.

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
| Management firewall reload while traffic runs (install, update), five reloads | 16 un-NATed frames (the old unit deleted the table first) | clean, the file replaces the table atomically |
| Router LAN on 192.168.50.0/24, router on .1 | **Pi answers ARP for 192.168.50.1** | clean |
| DHCP on eth0, also in the same-subnet case and with `dhcp-authoritative` | silent (no DHCPOFFER or DHCPNAK) | silent |

Lab runs on 17 October (colima, kernel image built from this branch): before
the fix 22 failures in 13 tests; after the fix all 13 tests pass. The
same-subnet "before" run removed the unit's kernel settings only. One later run
caught a byte-identical client frame under download duplication for everyone
(a client's own duplicate ACK, not a Pi copy); the copy check now looks only at
frames the client sends once (numbered datagrams, echo requests).

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
| 4 | Test Wi-Fi source IP without NAT | ok | **was leaking** | **was leaking** | **gap at reload and shutdown** | ok | guard "no profile", "Wi-Fi restart"; fix: control firewall `postrouting` | fixed | A rollback to an older release removes the rule until it is reinstalled. A flow that started without NAT during a Wi-Fi restart stays dropped until it is idle (seconds to minutes), the client then opens a new one |
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

1. Done here: the management firewall and kernel setting changes above (wire
   defects 4 (S2), 5 and 6 (S3)).
2. **D1, owner decision (high).** Renaming to the router name at every boot
   without a check. A power cut does not normally switch router Wi-Fi back on
   (the router keeps its setting), but the owner switching it on again, the
   WLAN button, an ISP firmware push or a factory reset all do. Then both
   radios carry one name and devices roam between two subnets ("connected but
   cannot browse", `pi-only-datapath-audit-2026-10-09.md` item 1). Proposal:
   in `wifi_follow boot` (runs before the AP starts, `wlan0` still idle), run
   one `nmcli -t -f SSID,BSSID device wifi list --rescan yes ifname wlan0` and,
   when the router name is on the air from another BSSID, keep or switch to
   `JitterSim-Recovery` and log why. This scan is active (the Pi sends Wi-Fi
   probe requests that the router's radio answers), but it never logs in to
   or changes the router. It changes what PR 140 does, so it is not
   implemented here.
3. **D2, owner decision (medium).** Download corruption in Pi-only mode reaches
   household devices on the Wi-Fi. Option: refuse corruption and duplication
   for the Everyone profile while the Pi uses the router name.
4. **D3 (low).** A runtime refusal to open the Wi-Fi when `eth0` holds an
   address in `192.168.50.0/24` (`jittersim-ap.service` `ExecStartPre`). The
   wire is already clean in that case (test above), so this only protects the
   Pi's own routing.
5. Done here as well: the management firewall reload is atomic and the unit
   no longer removes the table on stop (found by the review).
6. Carried over, unchanged: the rollback cleanup of the upload intake
   (`carry-forward-panic-batch-2026-10-10.md`).

No proposed change needs a Panic-pinned file (`panic_guard.py`,
`networking/helper.py`, `networking/access.py`), so there is no patch file.
`access.py` `_clear_dns_connections` (Panic and rule changes) was a trigger for
leak 4; the firewall invariant covers it without touching that file.

## Read-only checks for the real hardware

Run on the Pi over Ethernet or SSH from the router network, after this release
is installed. Nothing here changes the Pi or contacts the router's web page.

Every `tcpdump` here uses `-p` (no promiscuous mode), so it sees only the
Pi's own frames.

0. The kernel can load the rules (the installer checks this too):
   `modinfo nft_fib_inet nft_fib_ipv4` lists both modules, and
   `sudo nft --check -f /etc/jittersim/jittersim-control-firewall.nft` (the
   rendered copy; `--check` loads nothing) prints nothing and exits 0.
1. Settings and rules are in place:
   `cat /proc/sys/net/ipv4/conf/all/arp_ignore /proc/sys/net/ipv4/conf/all/arp_announce`
   (expect `1` and `2`), `grep . /proc/sys/net/ipv4/conf/*/send_redirects`
   (all `0`), `sudo nft list chain inet jittersim_control postrouting`.
2. Nothing leaves with a test Wi-Fi address, a foreign MAC or a server answer
   (run for the whole Pi-only trial; each must stay empty):
   `PI_MAC=$(cat /sys/class/net/eth0/address)`
   `sudo tcpdump -p -i eth0 -Q out -n -e -c 50 "src net 192.168.50.0/24 or not ether src $PI_MAC or udp src port 67 or udp src port 547 or (icmp6 and ip6[40] == 134) or (icmp and icmp[0] == 5)"`
3. ARP answers only for the uplink address:
   `UPLINK=$(ip -4 -o addr show dev eth0 scope global | awk '{print $4}' | cut -d/ -f1 | head -1)`
   `sudo tcpdump -p -i eth0 -Q out -n -e "arp and not arp src host $UPLINK and not arp src host 0.0.0.0"`
   must stay empty. ARP probes with sender `0.0.0.0` are expected noise:
   NetworkManager sends them when it checks a DHCP address for conflicts.
4. The drop counters show how often the invariant fired (a number above zero
   means it stopped a leak):
   `sudo nft list ruleset | grep -E "never send a test Wi-Fi address|never route the household LAN"`.
5. Dual AP (D1): before switching the router Wi-Fi off, from any Linux device
   or the Pi before its AP starts, `nmcli -f SSID,BSSID,CHAN device wifi list`
   shows whether the router name comes from two BSSIDs.
6. Router side, only with the owner's agreement and only reading: the router
   event log export and uptime before and after a trial, as in the dossier.

## Reviews

One full-diff review by Sol (GPT-6.1) ran on `87f7466` before the coordinator
stopped all OpenAI use; none ran after. It found five issues, all fixed in the
fix round: the postrouting drop also hit the Pi's own traffic on a same-subnet
router LAN (fixed with `fib saddr type != local`, and the same-subnet test
requires the Pi's ping reply); the unit still deleted the table before loading it
(atomic file, no `ExecStop`); the transition tests could finish their traffic
before the transition (fresh flows now run inside the gap and the drop counter
must rise); a crashed generator could pass silently (generators must finish
with a completion line); the capture did not check order (numbered datagrams
are now checked). The author then did a read-only Claude pass.

An independent Opus review of `2423694` found no blocker and asked for one more
round, done here: `fib saddr . oif type != local` so the Pi's own Wi-Fi address
192.168.50.1 cannot leave without NAT either (new lab case); `TimeoutStartSec=30`
on the firewall unit and its failure mode written down; an `nft --check`
preflight of the rendered rules in the installer and the updater, because `fib`
is new in the shipped rules and the lab kernel is not the Pi kernel; the
capture now stops only after every generator finished and fails if it reached
its own deadline; and the documentation corrections in this file (corruption
evidence, the D1 triggers and the active scan, `tcpdump -p`, the check-3
filter, severities).

## Gate

See the pull request for the local gate results on the exact head.

## Follow-up: D1 and D3

PR 160 implements D1 and D3 as the owner agreed. `wifi_follow boot` waits up to 10 s for
`wlan0` to leave `unavailable`, reads the cached Wi-Fi list, then runs at most one active
rescan (20 s budget in total). When the router name is on the air from a BSSID that is not
the Pi's own, the Pi keeps or takes `JitterSim-Recovery` for that boot and the router name
is not used as a fallback. Every failure of the look keeps the behaviour of PR 140, and a
running access point keeps its name. `wifi_follow status` shows the decision, the scan
result and the reason. For D3 `jittersim-ap.service` runs `wifi_follow uplink-check` last
before the start: only its exit status 3 (an uplink address inside `192.168.50.0/24`)
stops the start. The physical check still to do: a cold boot with the router Wi-Fi on must
show `boot_scan: seen`, and one with the router Wi-Fi off must give the router name.
