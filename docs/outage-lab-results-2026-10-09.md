# Outage lab results: the Pi-only cutover in network namespaces (2026-10-09)

This reproduces the Pi-only mode (router Wi-Fi off, household devices on the Pi's
copied network name) on one Linux kernel and measures each failure mode named in
the outage dossier (`docs/outage-dossier-2026-10-04-2026-10-09.md`) and the
source gap audit. No real Pi or router was contacted.

Code: `tests/kernel/outage_lab.py` (topology and probes), `tests/kernel/test_outage_lab.py`
(16 scenarios), `tests/kernel/Dockerfile.outage-lab`, `tests/kernel/run-outage-lab.sh`,
`tests/kernel/README.md`. Base: main `12f4613`. Lab run: 16 tests, 498 s, OK with 3
expected failures (documented defects in Panic-gated files).

## Topology

```
INET 198.51.100.0/24: dnsmasq answers www/video/news.example.org, http.server serves a page
  |
ROUTER: bridge br-lan 192.168.0.1, dnsmasq DHCP (authoritative by default) + DNS proxy,
        nft masquerade to INET, conntrack event log, fault chain
  |-- HOUSE: a wired household device (dhclient)
  |-- PI eth0: dhclient lease from the router (192.168.0.219 in the run)
  |       PI wlan0 192.168.50.1/24: NetworkManager shared mode reproduced from NM's source
  |       (exact dnsmasq command line, `nm-shared-wlan0` nft table), JitterSim control
  |       firewall, the real NetworkApplier, HelperService, AccessManager and Unbound
  |-- AIR: br-rwifi (router radio) and br-piap (Pi radio)
             CLIENT and OTHER_CLIENT (dhclient). Roaming moves the station port
             between the two radios and drops the client's carrier like a real roam.
```

Tools (all mature, chosen so the lab tests behaviour, not a re-implementation):
dnsmasq 2.90 (what NetworkManager runs for shared mode and what many home routers
run), ISC dhclient 4.4 (stock DHCP client state machine: INIT-REBOOT, renew, rebind,
expiry), tcpdump 4.99 and scapy 2.6 (capture and raw frame reading), nftables
1.0.6 and conntrack (the Pi's own datapath), hostapd 2.10 (controller-only mode for
the admission socket), Unbound 1.17, Python http.server, iproute2 `nstat` and
`bridge` for the router's counters. Only the glue is new code.

"Browsing" in every scenario: the client resolves `www.example.org` with the
resolver its DHCP lease gave it AND fetches the page through the router.

Run: `tests/kernel/run-outage-lab.sh "$PWD/outage-lab-output"` (one privileged
container, image `jittersim-lab-outage`).

## Results

| # | Scenario | Result | Measurement |
|---|---|---|---|
| H5 | `daily climb manual` upload (6 % corrupt, 7 % duplicate, 10 % reorder) on eth0, device / general / service scope | **Damaged frames reach the router** in all three scopes | per ~10 s: 9/8/8 forged source MACs (one-bit flips of the Pi's MAC, one with the multicast bit set), 12/10/19 frames to unknown unicast MACs, 1/6/3 unknown ethertypes, 48/30/38 broken IPv4 header checksums, 10/7/5 forged source IPs, 106/71/90 broken TCP checksums. Router counted IpInHdrErrors 49/31/39; its bridge learned 10/8/8 MACs on the Pi port (1 at baseline) |
| H5 | same profile with corrupt 0 % | Clean | 0 damaged frames, 113 byte-identical duplicates (well formed) |
| H5 | proposed: upload netem on an ifb fed from wlan0 ingress | No L2/IP header damage, but **45 un-NATed frames** (source 192.168.50.x, bad L4 checksum) leak onto the household LAN | conntrack marks them INVALID, NAT skips them, NM's forward rule accepts them |
| H5 | proposed ifb + `ct state invalid drop` | Clean | 0 damaged or un-NATed frames; impairment still effective (ping loss 4 %, slower upload) |
| H5 | Pi-local frames (ARP, DHCP client, DNS forwarder, mDNS) in every layout | Never damaged or duplicated | only fw-marked forwarded traffic reaches the netem bands |
| 1 | Clean Pi-only mode | Pass | 3.1 s from join to browsing (fresh DHCP). Router WAN fault: browsing fails (DNS cached, HTTP fails) and returns 0.03 s after the fault clears |
| 2a | Roam router -> Pi with the router lease, NM shared dnsmasq as installed before this PR | **No browsing for 14.1 s** | the client's INIT-REBOOT DHCPREQUEST for 192.168.0.150 is silently ignored five times; only dhclient's 10 s `reboot` timeout moves it to DISCOVER |
| 2b | Same roam with `dhcp-authoritative` (this PR) | Pass | 3.2 s: immediate `DHCPNAK ... wrong network`, then DISCOVER |
| 2c | Roam back Pi -> router (rollback), router authoritative | Pass | 3.2 s (router NAKs the 192.168.50.x lease) |
| 2d | Same, router not authoritative | **No browsing for 15.3 s** | matches "router Wi-Fi back on did not immediately restore browsing" if the real router ignores foreign INIT-REBOOT requests (unknown) |
| 2e | Client that does not re-run DHCP after the roam (router lease 120 s) | **No browsing for 109 s** | recovers only when its stale lease runs out; real router leases are hours, so such a device stays broken for hours |
| 3a | Rules on, Unbound crashes, systemd restarts it after RestartSec=2 | Outage for every Wi-Fi client | 2.8 s, also for the device without rules |
| 3b | Rule change, resolver restart fails once | Old rules restored | no outage seen at 0.2 s sampling in the lab (transaction 0.1 s; longer on a Pi) |
| 3c | Rule change, restart always fails | **Test Wi-Fi closed** (transition guard left) | until Panic; Panic reopened it in 0.06 s |
| 3d | Rules on, Unbound dead and not restartable | **No DNS for every Wi-Fi client**, also those without rules | until Panic (health only reports "Filtered DNS is unavailable") |
| 4a | Household MAC rules + experiment, hostapd control socket wedged (SIGSTOP) | **Panic fails, test Wi-Fi closed** | Panic 2.05 s -> `access_disabled: false`, guard table left, no browsing; Start refused ("Retry Panic"); Reset ok but guard stays; Panic retry fails; after hostapd recovers Panic reopens at once (closed 8.2 s in the lab, indefinitely if hostapd stays wedged) |
| 5 | Background address refresh while another client streams | **Every Wi-Fi client pauses** for the resolver restart | gap 0.15 s with an instant restart, 1.65 s with a 1.5 s restart (a Pi is slower). A ruled device on a new lease is cut off completely until the refresh runs (up to 15 s) |
| 6 | Connection tracking | Measured | 6 entries per page load (DNS + TCP), 2 per uncached lookup plain, 3 with the filtered resolver. Table full (emulated cap of 300 Wi-Fi flows, 350 UDP flows from one device): **every client's new connections fail for 31 s** (the UDP unreplied timeout). Unbound RSS 13 MiB, NM dnsmasq 4 MiB |
| 7 | What the router sees from the Pi (no impairment) | Clean | no DHCP server replies, no IPv6 router advertisements, no LLDP, no foreign source MACs or IPs on eth0 in any phase. Broadcast/multicast 0.49 /s with JitterSim's restricted Avahi (IGMP, MLD, router solicitations, DHCP client) vs 1.62 /s with stock Avahi (mDNS announcements). The Pi does not answer DHCP or DNS from the household LAN. Shaping one device (200 ms RTT) left the other client at 0.08 ms and the household device at 0.05 ms, 0 % loss |
| 8 | Copy router Wi-Fi (fake TG3442DE API), cutover, rollback timer | Pass | preview and sync each log in and log out once (2 logins, 2 logouts); one AP down/up; routes and JitterSim tables unchanged; a household device on the router radio saw no outage; cutover 6.0 s with this PR's DHCP fix; rollback to the router 1.3 s |

## Which scenarios reproduce a "no browsing" state

- Stale lease after the copied-name roam: 14-15 s per roam in each direction when
  the DHCP server ignores the foreign request (Pi before this PR, router unknown),
  hours for a device that does not re-run DHCP.
- Filtered DNS failures (3c, 3d) and Panic with a wedged hostapd (4a): until Panic
  succeeds, which can be never while hostapd stays wedged.
- Address refresh (5): seconds for everyone, up to about 15 s for the moved device.
- Full conntrack table (6): 30 s per burst of UDP flows.
- None of these produces anything on the router cable that a router should mind.
  The only scenario that does is H5 (corruption on eth0), and it matches the
  dossier's observation that both red-PHONE failures happened with
  `daily climb manual` active.

## Defects found

1. **Upload corruption is applied to whole Ethernet frames on the router cable**
   (`src/jittersim/networking/renderer.py:601` puts upload queues on
   `topology.uplink_interface`; `renderer.py:128` always states `corrupt`). netem
   flips a bit anywhere from the MAC header on, after NAT. Result: forged source
   MACs, unknown destinations, broken ethertypes and IP headers on the household
   LAN (H5 rows). Not fixed here: the Pi datapath fixer implements it. Measured
   safe designs: (a) no upload corruption on eth0 (corrupt 0 % is clean), or
   (b) upload netem on an ifb from wlan0 ingress **plus** `iifname "wlan0" ct state
   invalid drop`, because (b) alone leaks un-NATed 192.168.50.x packets.
2. **NM shared-mode dnsmasq is not authoritative** (fixed here): NetworkManager
   never passes `--dhcp-authoritative` (nm-dnsmasq-manager.c), so a roaming
   client's request for its router lease is ignored until the client gives up.
   `src/jittersim/localname.py:35-36,223` now writes `jittersim-dhcp.conf`
   (`dhcp-authoritative`) next to the dashboard name record in
   `/etc/NetworkManager/dnsmasq-shared.d`, through the same `install_config` that
   the installer and the dashboard update run. Only the Pi serves
   192.168.50.0/24 and dnsmasq binds only 192.168.50.1 (scenario 7 confirms it
   never answers on eth0), so this cannot affect the router LAN. Test first:
   `tests/unit/test_local_name.py::...answer_foreign_leases_at_once`; lab 2a/2b.
   The installer reports the change and asks for `sudo nmcli connection up
   jittersim-ap`; until then the running dnsmasq keeps the old behaviour.
3. **Panic's household release can leave the test Wi-Fi closed** (gap audit 2,
   `src/jittersim/networking/access.py:557` guard, `:462` admission, `:564-579`
   rollback; gated). Reproduced with a wedged hostapd: strict expected failure
   `test_4b_panic_keeps_the_test_wifi_open_when_hostapd_is_wedged`.
4. **The address refresh pauses every Wi-Fi client** (gap audit 3,
   `access.py:603-624` calls `apply()`, which installs the guard at `:557` for the
   whole resolver restart; gated). Strict expected failure
   `test_5b_an_address_refresh_does_not_pause_unrelated_clients`.
5. **A dead filtered resolver takes DNS from devices without rules**
   (`access.py:283` redirects port 53 of every test Wi-Fi client; gated). Strict
   expected failure `test_3e_a_dead_filtered_resolver_leaves_devices_without_rules_resolving`.
6. Low: the Pi answers ARP for its eth0 address on wlan0 (Linux default
   `arp_ignore=0`), so a device with a stale 192.168.0.x address reaches the Pi but
   never the router (scenario 7). Harmless for the router; noted for diagnosis.
7. By design, worth knowing for Pi-only mode: the control firewall drops test
   Wi-Fi traffic to 192.168.0.0/16 (`deploy/raspberry-pi/jittersim-control-firewall.nft:42`),
   so devices moved to the Pi cannot reach the router's web page, printers or
   casting targets on the household LAN.

## What the lab cannot prove about the real router

- That a Vodafone Station TG3442DE hangs, or turns PHONE red, on the H5 frames.
  The lab proves the Pi emits them; the router's switch, packet processor and
  firmware are not modelled (the lab router is a Linux bridge and stack, which
  counted and dropped them). Vodafone forum threads report TG3442DE crashes and
  restarts triggered by a particular device joining the LAN
  ([forum.vodafone.de](https://forum.vodafone.de/t5/Archiv-Internet-Ger%C3%A4te/Problems-with-Arris-TG3442DE-router/m-p/2389691/highlight/true)),
  which makes a device-triggered hang plausible but is not evidence for this case.
  Settle with the dossier's hardware A/B test: same Pi-only setup, corruption
  profile vs delay-only.
- Whether the real router's DHCP server answers a foreign INIT-REBOOT with NAK (2c)
  or ignores it (2d), and its lease time (2e).
- Real client timers: ISC dhclient waits 10 s in INIT-REBOOT; iOS, macOS and
  Android use their own timers and may probe the old gateway first.
- Wi-Fi itself (association, band steering, BSSID caching, PMF/SAE), the router's
  MAC filter and `AP-DISABLED` events, and the telephone side of the router.
- Pi 3B+ speed and memory: the lab's Unbound restart is instant; `nf_conntrack_max`
  is global and read-only inside a namespace, so the full table was emulated with an
  nftables `ct count` cap. On the Pi, read `/proc/sys/net/netfilter/nf_conntrack_max`
  (the kernel sizes it from RAM: roughly 7,000 to 15,000 on 1 GB).
