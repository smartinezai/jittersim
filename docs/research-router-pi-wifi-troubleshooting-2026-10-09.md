# Router and Pi Wi-Fi troubleshooting research

Written 9 October 2026. Online research only. No Pi or router was contacted and
no tests were run. Every claim below is marked as one of:

- **Repo fact**: recorded in the incident notes in this repository.
- **Reported**: a user report on a forum or blog. Not confirmed by a vendor.
- **Documented**: stated on an official or maintainer page.
- **Speculation**: my reasoning. Not shown by any source.

## Short answer

The 4 October outage has no proven cause. After reading the notes and dozens of
sources, the most likely story is that **the Vodafone router has a known
habit of dropping its own Wi-Fi and restarting** and that the Pi cutover
happened to overlap with it. The second most likely piece is that **clients
were left in a split state** (joined to one access point, holding an address
from the other network). The Pi is a weaker suspect for the router's trouble
because the Pi uses NAT and shows the router only one wired device.

Three facts shape everything else:

1. Router log lines `AP-DISABLED` and `CLIENTTOOL ... KickAssocDevices` show up
   in many Vodafone forum threads from people who have no Pi. See
   [Router hypothesis](#1-router-bug-or-instability-not-caused-by-the-pi).
2. The Pi puts clients on its own subnet and hides them behind NAT. The router
   never sees those clients, only the Pi's Ethernet address.
3. The notes record that router clients also failed with the Pi unplugged and
   only a router restart fixed it. A Pi-only cause cannot explain that.

## What the notes already establish (Repo facts)

- Pi 3 Model B+, 1 GB RAM. Ethernet uplink `eth0` on the router LAN
  (192.168.0.x). Test AP on `wlan0`, 192.168.50.1/24, NetworkManager shared
  mode (DHCP pool .100 to .199), IPv6 off on the AP.
- Router: Arris TG3442DE (Vodafone Station) per the code comment in
  `src/jittersim/networking/router.py`. The router firmware version is not
  recorded anywhere in the notes.
- The cutover copied the router name and password (WPA2, 2.4 GHz). Router
  Wi-Fi was then turned off with the physical button. Two devices joined but
  could not browse. Router Wi-Fi back on did not help straight away. The PHONE
  light later went red. A router restart fixed service.
- Router uptime at 22:43 was 2 h 25 min, so a router restart happened near
  20:18. The event log had `AP-DISABLED` at 22:24:25, 22:29:40 and 22:29:51.
  Router Wi-Fi was not turned off by the Pi trial at that time.
- A `CLIENTTOOL` entry set `KickAssocDevices` on `AccessPoint.2` at 01:14:58
  on 4 October, after a router recovery reboot. Sender not identified.
- The owner turned router MAC filtering on and later off while
  troubleshooting. No time recorded.
- Pre-reset Pi logs are gone. No outage-time router status was saved.

## Ranked hypotheses

Ranking is my judgement of fit with the evidence, not a probability.

### 1. Router bug or instability, not caused by the Pi

**Rank 1. Plausible. Not confirmed.**

For:

- Reported: many Vodafone Station users see `AP-DISABLED` and
  `INTERFACE-DISABLED` in the router log, with Wi-Fi vanishing for minutes and
  sometimes a full router restart, often in the evening [V9]. One thread says
  the router restarts every 15 to 120 minutes, mostly in the evening.
- Reported: the same users see `Set Device.WiFi.AccessPoint.N.X_CISCO_COM_KickAssocDevices
  to true from CLIENTTOOL` on both access points. One forum poster logged it
  every 20 to 40 minutes, each time with a loss of LAN and internet on the
  router, and no workaround held [V5, V6, V7, V8]. Some users see it with Wi-Fi
  switched off [V7]. This matches our log entries line for line.
- Reported: TG3442DE owners describe Wi-Fi that stays visible but refuses
  connections at night until the router is restarted [V10]. That is close to
  "associated but cannot browse until restart".
- Reported: a solid red PHONE light is a telephony line fault, often "not
  registered" in the router page [V1 to V4]. Phone registration belongs to the
  cable side. A Pi on the LAN cannot change it. A router restart or a cable
  plant problem can.
- Repo fact: the router restarted near 20:18 and later logged `AP-DISABLED`
  while the Pi was not switching router Wi-Fi off.
- Repo fact: the failure persisted with the Pi unplugged.

Against:

- The timing lines up with the cutover trial. That may be coincidence or may
  be the trigger.
- Forum reports are mostly older firmware and other models (TG344DE, Arris
  AR01.04.046.12, CGA4233). No source ties these log lines to the exact
  TG3442DE firmware here.
- No vendor explains `CLIENTTOOL`. The name suggests "kick associated clients".
  That reading is **Speculation** in every source I found. The RDK data model
  confirms only that `X_CISCO_COM_` marks a vendor extension [V17].

What would settle it: a router-only soak with the Pi off, watching for
`AP-DISABLED` and `KickAssocDevices` with no JitterSim action. See the ladder.

### 2. Clients left in a split state after the shared-name cutover

**Rank 2. Plausible for symptom one only.**

For:

- Repo fact: the router network is 192.168.0.x and the Pi network is
  192.168.50.x. The Air first showed 192.168.0.222 after choosing the copied
  name, later 192.168.50.160. A client holding an address from one network and
  talking through the other access point shows "Wi-Fi connected, no internet".
- Reported: forum threads describe exactly this when a second access point
  hands out a different subnet, and clients keep a stale lease until Wi-Fi is
  toggled [S: Netgear thread].
- Documented: clients, not access points, decide which BSSID to join and when
  to roam. Same name plus same password is normal for one network, but clients
  can stick to a weak or wrong BSSID [Ruckus, CBT Nuggets, OpenWrt roaming].
- Documented: Apple says to run one DHCP server and one NAT. Two networks with
  the same name break that rule by design [Apple 102766].
- Reported: for some Mac versions mixed security between two same-name access
  points broke roaming until both matched [MacRumors, MetaGeek].
- Repo fact: the notes themselves say a shared name does not show which access
  point a client joined.

Against:

- It does not explain the red PHONE light or the need to restart the router.
- Router Wi-Fi was off during the test, so there was no second BSSID to stick
  to. Clients should have joined the Pi. The first failure then points to the
  Pi path (hypothesis 4) or to a lease or ARP leftover on the client.
- Apple does not document what a client does with an old lease when it
  re-joins a same-name network on a different BSSID. That part is
  **Speculation**.

### 3. JitterSim wrote to the router, or logged in, and disturbed it

**Rank 3. Open question. Partly mitigated.**

For:

- Repo fact: the pre-guard fallback could forward an unrelated hidden control
  such as `KickAssocDevices` together with radio changes. Reproduced on a local
  fixture only.
- Repo fact: login and logout are stateful router operations. No log separates
  login, read, logout and settings write.
- Speculation: some router firmwares allow one admin session at a time. A
  Pi login could kick the owner's browser session. No source confirms this for
  the TG3442DE.

Against:

- The same `KickAssocDevices` entries appear for people with no JitterSim
  (hypothesis 1).
- Repo fact: the write guard exists in the recovery source and router writes
  are refused by default. There is no transcript tying a JitterSim request to
  an entry.

### 4. Pi path was broken at outage time (DHCP, DNS, NAT or forwarding)

**Rank 4. Fits "associated but cannot browse". Cannot explain the router.**

For:

- Reported in forum threads: "connected, no internet" on a NetworkManager
  shared access point is usually missing dnsmasq, a wrong default route, port 53
  held by another service, or missing forwarding or NAT [Pi-hole forum, Pi bookworm AP thread].
- Repo fact: the first cutover test accepted browsing only on the Air, then
  later failures were intermittent and dashboard verification failed
  repeatedly.

Against:

- Repo fact: the separate-name recovery SSID later worked on two devices
  (owner-reported, not packet-level).
- Cannot explain the router symptoms with the Pi unplugged.

### 5. Router-side client steering or radio interplay with a same-name AP

**Rank 5. Speculation.**

- Reported: one forum log shows `client steer attempt ... link quality (RSSI)`
  next to the kick entries. That hints the kick tool is part of Vodafone's
  band or client steering. If so, a same-name rogue radio on 2.4 GHz could
  interact with it. No source says so.
- Repo fact: the Pi copied only 2.4 GHz. The router also runs 5 GHz. KPMac was
  seen on the router's 5 GHz radio. Apple says use one name for all bands.
  Clients that were on 5 GHz would see a weaker, slower alternative.
- The notes do not record the router's 5 GHz security mode. If it was not plain
  WPA2, the same name carried different security on two radios, which is a
  flagged mismatch in some tools [MetaGeek].

### 6. The Pi polluted the router LAN (ARP, DHCP, RA, multicast, loop)

**Rank 6. Low, but cheap to rule out.**

- Repo design: routed and NAT, not bridged. IPv6 off on the AP. Deployment
  preflight rejects overlapping subnets. Static review found no bridge, uplink
  DHCP or route-loop defect.
- Documented elsewhere: rogue DHCP servers and address clashes cause
  "connected, no internet" on clients [Rogue DHCP, FreedomBox]. A cable modem
  that restarts can also fall back to handing out its own address [FreedomBox].
- No source I found says a stray ARP, DHCP, RA or mDNS packet crashes a Compal,
  Arris or Technicolor router or turns its PHONE light red. I looked and found
  nothing. **Absence of evidence, not evidence of absence.**
- A 24-hour passive capture on `eth0` (below) shows exactly what the router
  hears from the Pi.

### 7. MAC filtering made it worse

**Rank 7. Plausible side issue.**

- Repo fact: the owner enabled and later disabled MAC filtering.
- Documented: Apple devices use a different private Wi-Fi address per network
  by default [Apple 102509]. Apple also says to keep MAC filtering off
  [Apple 102766].
- Speculation: a filter built from hardware addresses blocks devices that use
  private addresses. That could look like "clients cannot join after the Pi was
  unplugged". The notes do not record when the filter was on.

### 8. Pi hardware, power or Wi-Fi firmware

**Rank 8. A reliability risk, not an outage explanation.**

- Reported: the Broadcom chip in Pi boards often limits AP mode to about 8
  clients, and hostapd settings do not lift it [RaspAP 462 (read), OpenWrt
  forum]. Maintainer: "probably the brcmfmac driver", firmware source is closed.
- Documented: on a Pi 5 with the same Wi-Fi chip (BCM43455), a firmware crash happens
  with STA plus AP mode on Trixie. Not our setup (AP only, Pi 3B+) [linux
  7092 (read)].
- Reported: Pi 4 AP crashes after OS upgrades. `iw ... power_save off` made no
  difference in that report [firmware 1463].
- Documented: Pi 3B+ soft temperature limit is 60 C and drops the clock from
  1.4 to 1.2 GHz. Repo fact: flag `0x80000` was set, meaning it happened once.
  Not a current limit.
- Documented: Pi 3B+ Ethernet tops out near 300 Mbit/s (USB 2.0 shared with
  Wi-Fi traffic) [repo capacity note].

## The red PHONE light

- Reported: solid red means a telephony line fault. Slow red blink means it is
  registering [V1]. Unconfirmed forum answers also link it to "not registered"
  or provisioning after a restart and to cable-side faults [V2 to V4].
- Speculation: if the router restarted around 20:18 and phone registration
  failed or lagged, red PHONE would be a symptom of the restart, not of the Pi.
- Check on the router page: phone status text, DOCSIS status (locked channels,
  signal levels, T3 and T4 timeouts) and the router event log. Also check
  Vodafone's outage page for the evening of 4 October.
- No source links a LAN device or an extra access point to this light.

## Questions from the brief, answered briefly

**Duplicate name, same password.** Allowed and standard for one network with
several access points. Clients choose. They can stick, flip, or keep old
addresses. Fast roaming (802.11k, v, r) and band steering are access point
features that only help when both access points belong to one controlled system.
A Pi and a Vodafone router do not share keys, neighbour lists or a DHCP server.
Treat them as two networks wearing one name.

**PMF and WPA2/WPA3 mixes.** NetworkManager: PMF default is "optional"; modes
`wpa-psk` (WPA2 and WPA3 mixed) and `sae` (WPA3 only) differ [NM docs (read)].
In transition mode some older drivers fail or get deauthenticated when they
misread the optional flag [OpenWrt 802.11w thread, AVM, purple.ai (vendor
blog)]. Plain WPA2 without PMF also lets anyone forge deauth frames
[arXiv 2602.23513]. Our copy was WPA2 on 2.4 GHz, so a PMF or SAE mismatch is
unlikely unless the router's 5 GHz radio used WPA2/WPA3 (not recorded). SAE
hash-to-element and cached PMK issues: I found no source relevant to this
incident. Skip until a client log shows an SAE failure.

**Deauth storms.** A router that kicks clients sends genuine deauth frames. A
protected-management-frame setting does not stop those. The only way to tell a
router kick from a forged frame is an air capture or the router's own log.

**Vodafone Station behaviours.** Reported: no Wi-Fi schedule on the TG3442DE
(the notes agree: Time schedule is off) [V11]. Wi-Fi can be switched only in
the web page or with the button [V11]. Guest and Homespot features exist; the
Homespot service runs a separate public SSID and was named in one thread as a
possible link to kick entries, then retracted by the same poster [V5, V13].
Automatic firmware updates arrive after the router has been online for a while
[V10]. Duplicate IP and DHCP clashes with the router are reported when users
add a second device with DHCP [V14].

**Pi as AP and router.** See hypothesis 8. For tc and nftables throughput I
found no benchmark for a Pi 3B+ at all. Pi 4 routing reports with OpenWrt:
roughly 500 to 725 Mbit/s one way with light rules, about 645 with SQM, and
much worse in bidirectional tests [OpenWrt Pi 4 threads]. One core handles most
packet work. No hardware NAT offload. Pi 5 headroom is larger but I found no
clean same-setup comparison. **netem cost is unmeasured here.** Measure it on
the Pi itself.

**USB Wi-Fi adapters.** For a second radio or better AP: MediaTek MT7612U
(mature, in-kernel since Linux 4.19, short range reports) or MT7921AU (Wi-Fi 6,
AP support in kernel, some hostapd 2.11 failures reported on a Zero 2 W) [morrownr
USB-WiFi, OpenWrt tables]. Avoid Realtek chips for AP mode. Always set a fixed
channel. Check chip with `lsusb` before buying. None of this was tested here.

## What to measure before blaming hardware

Run each with router-only first, then Pi-forwarded, same two clients and spots:

- CPU per core and softirq share (`mpstat -P ALL 5`), packet drops
  (`/proc/net/softnet_stat`), qdisc stats (`tc -s qdisc`), conntrack count.
- Memory and swap; temperature; `vcgencmd get_throttled`. Bits: `0x1` under
  voltage now, `0x4` throttled now, `0x8` soft limit now, `0x10000`,
  `0x40000`, `0x80000` mean it happened since boot [Pi OS docs (read)].
- Wi-Fi: `iw dev wlan0 station dump` (signal, retries, failed transmits),
  `dmesg -T | grep brcmfmac` for firmware halts and `-52` errors.
- `iperf3 --bidir` with the server on a wired machine [NetBeez], plus a
  latency-under-load test (flent or a browser bufferbloat page).
- Compare with the profile caps. A 5 Mbit/s cap is far below any hardware limit.

## Safe experiment ladder

Rules for every rung: router Wi-Fi stays on. No copied name. No router setting
changes by JitterSim. Profiles paused. Write the start time and your clock
source. Stop and go back to router-only if any household device loses internet.
The Mac needs no Ethernet: the Pi's `eth0` is on the router LAN, so SSH works
from the Mac over router Wi-Fi.

**Rung 0. Save evidence (no changes).** Router: firmware version, uptime, PHONE
and Internet lights, phone status, DOCSIS status page, full event log with the
router's displayed time. Screenshot them. Pi: time sync state, `uname -a`,
brcmfmac firmware line from `dmesg`, `get_throttled`, temperature, PSU rating
and cable. Note the owner's clock versus the Pi clock.

**Rung 1. Router-only soak, Pi powered off, 48 to 72 hours.** Re-export the
router log every morning. Count `AP-DISABLED`, `KickAssocDevices` and restarts.
If they appear with no Pi, hypothesis 1 is strong and the Pi is cleared of those
events. If they do not, go on.

**Rung 2. Pi on Ethernet, Wi-Fi radio disabled, 24 hours.** Same router
counts. Tests the Pi's mere presence as a wired LAN client. Add the passive
`eth0` capture below.

**Rung 3. Pi on with the separate recovery name and no clients, 24 hours.**
Radio on, no clients. Same counts. Tests whether the Pi's radio and DHCP
service disturb the router.

**Rung 4. One or two spare test devices on the recovery name.** The Galaxy
and the Air are enough. Check lease, gateway, DNS and a browsing test. Add
simultaneous download and upload. Record the measurements list above. Keep this
at off-peak times.

**Rung 5. Same-name lab that never touches the router.** A phone hotspot with
a name like `TestNet` acts as access point A. The Pi serves `TestNet` with the
same password as access point B. Walk one test client between them. Watch its
address, gateway and browsing, and whether it keeps a stale lease. This
reproduces hypothesis 2 with no household risk. A second USB radio on the Pi
(two Pi-only access points with the same name on different subnets) is a
fallback if no hotspot is available.

**Rung 6. Decide the cutover design** only after rungs 1 to 5. See the safer
design idea below. Any trial then needs a rollback that does not rely on
affected clients and a person physically near the router.

## Safer cutover design idea (Speculation, untested)

Do not copy the router name and turn router Wi-Fi off. Instead, **rename the
router's Wi-Fi by hand** (owner only, in the router page) to a new name, and
give the Pi the **old** name and password. Devices that cannot be moved by hand
rejoin the old name, which only the Pi now broadcasts. There is never a
duplicate name, and router Wi-Fi stays on under the new name as the instant
fallback. Open points: Pi has only 2.4 GHz here, so 5 GHz devices drop to
2.4 GHz; every household device would then load the Pi 3B+; and the router
rename is a router write that the owner must do themselves.

## Passive capture and logging

All commands below are general knowledge or from the cited pages. None were run.

**On the Pi (no monitor mode needed).** The Pi is the access point, so
`tcpdump -i wlan0` sees client traffic already decrypted.

Wired side, shows what the router hears from the Pi (switch-limited to
broadcast, multicast and Pi traffic):

```sh
sudo tcpdump -i eth0 -n -s 128 -C 20 -W 10 \
  -w /var/tmp/jittersim-eth0.pcap \
  'arp or (udp and (port 67 or port 68)) or icmp6 or (udp port 5353) or (udp port 1900)'
```

Wi-Fi side, client ARP, DHCP and router advertisements:

```sh
sudo tcpdump -i wlan0 -n -s 128 -C 20 -W 10 \
  -w /var/tmp/jittersim-wlan0.pcap \
  'arp or (udp and (port 67 or port 68)) or (icmp6 and ip6[40] == 134)'
```

`-C 20 -W 10` keeps at most ten 20 MB files, so the SD card cannot fill. The
router-advertisement filter assumes no IPv6 extension headers [Freifunk tcpdump
page, oneuptime BPF guide].

**Deauth and disassoc frames** are management frames. `tcpdump -i wlan0` does
not show them. Use the Pi's own events instead: `iw event -t` and the
NetworkManager and hostapd lines in `journalctl` carry join, leave and reason
codes. To see frames in the air you need monitor mode on a second radio. The
Pi's built-in chip is poor at this: Nexmon users report captures that stop
after minutes or show only beacons, and a USB adapter with a known driver worked
[Wireshark Q&A 24850]. Filter: `type mgt subtype deauth or type mgt subtype disassoc`.

**On the Mac (no Ethernet).** Wireless Diagnostics has a Sniffer
(Option-click the Wi-Fi icon, open Wireless Diagnostics, Window, Sniffer).
It captures one channel and width, and **takes the Mac's own Wi-Fi offline**
while it runs [AccessAgility, Cisco]. Keep SSH sessions on another device and
run it for minutes only. Output is a `.wcap` or pcap file in `/private/var/tmp`
or the desktop. It needs admin rights.

**What the Pi should log persistently** (the repo already plans persistent
outage logging; this is the list to keep it small):

- Journald persistent storage with a size cap; kernel ring buffer; the
  NetworkManager and helper units.
- A 10 second sampler to a rotating file: `get_throttled`, `measure_temp`,
  `ip -s link`, `iw dev wlan0 station dump` (count, signal, retries), conntrack
  count, `tc -s qdisc` drops, `/proc/net/softnet_stat`.
- DHCP lease file changes, nftables counters, uplink reachability of the
  router gateway (one ping a second to 192.168.0.1 with timestamps).
- Boot ID and monotonic time on each line so a reboot is obvious.

**Correlating with router logs.** The router shows its own clock and client
join events with MAC addresses. Make sync marks: at a noted second, connect a
phone to the router Wi-Fi and, a minute later, ping the router gateway from the
Pi. Both land in the router log and the Pi log. Write the offset down. Export
the router log soon after any event, because it is short. Always save the export
time as well.

## What I could not establish

- What `CLIENTTOOL` is and who calls it. Vodafone does not say.
- Whether the TG3442DE firmware in this house has the same bug as the reports.
- Whether the PHONE light and the Wi-Fi events share one cause.
- What clients do with an old lease on a same-name different-BSSID join. No
  Apple page covers it.
- Any link from Pi ARP, DHCP, RA or multicast traffic to a router crash.
- Pi 3B+ tc or nftables benchmark numbers.
- The hostapd option page (`git.w1.fi`) blocked my fetch, so I made no claim
  from it. If anyone retries Pi AP tuning, read that page for `ieee80211w`,
  `sae_pwe`, `max_num_sta` and the roaming options.
- Vodafone forum pages blocked full fetches (HTTP 403). Those claims come from
  search result summaries and are marked in the source list.

## Appendix: other ideas

Tools and tests:

- `iperf3 --bidir` and flent for load plus latency [NetBeez]. Use a wired
  server so only the Wi-Fi link is under test.
- Apple's Network Link Conditioner is fragile across macOS releases [Apple
  developer forum]. A Pi-side netem profile is more reliable.
- Compare router-only, Pi-forwarded and Pi-forwarded-with-profile on the same
  clients and places.

Features for JitterSim:

- Show which access point each client is on and its address and gateway in the
  dashboard. The incident notes had to infer this from the Pi's own station
  list.
- A pre-cutover checklist page that refuses the copy unless a timed rollback,
  a fresh Pi time sync and a router status snapshot exist.
- A Pi watchdog that returns to the recovery name if the router gateway is
  unreachable for a set time. The tested timer did not survive a reboot.
- A recorded "last router event" panel fed from a manually exported log, with no
  new router requests.
- Report `vcgencmd get_throttled` and `brcmfmac` firmware halts on the status
  page so Pi power or Wi-Fi faults show up before they look like network faults.
- A client canary: one always-on test device on each radio that logs lease,
  gateway and DNS every minute.

## Sources

[R] means I read the page in full through a fetch. [S] means I saw it only in
search result summaries, so check it before relying on it. [Blocked] means the
fetch returned 403 and the claim comes from the search summary.

Vodafone and Arris router:

- [V1] [S, Blocked] https://forum.vodafone.de/t5/Community-Blog/Was-bedeuten-die-LEDs-Deiner-Vodafone-Station/ba-p/2695430
- [V2] [S] https://forum.vodafone.de/t5/St%C3%B6rungen-im-Kabel-Netz/Seit-einer-Woche-kein-Festnetz-rote-Phone-LED-blinkt-Router-sagt/m-p/3270723/highlight/true
- [V3] [S] https://forum.vodafone.de/t5/Kabel-Tarife-Rechnung/Telefonnummer-nicht-registriert/td-p/3208109
- [V4] [S] https://www.antwort.net/a/technologie/vodafone-station-telefon-leuchtet-rot--was-bedeutet-das.html
- [V5] [R] https://www.vodafonekabelforum.de/viewtopic.php?t=45077&start=10 (CLIENTTOOL and KickAssocDevices on an Arris box, no Pi)
- [V6] [S, Blocked] https://forum.vodafone.de/t5/Archiv-Internet-Ger%C3%A4te/TG344DE-WLAN-immer-wieder-weg/td-p/2181460/highlight/true
- [V7] [S, Blocked] https://forum.vodafone.de/t5/Archiv-Internet-Ger%C3%A4te/KickAssocDevices-VodafoneStation-startet-neu/m-p/2555459/highlight/true
- [V8] [S, Blocked] https://forum.vodafone.de/t5/Ger%C3%A4te/Vodafone-Station-KickAssoc/td-p/3219890
- [V9] [S, Blocked] https://forum.vodafone.de/t5/Ger%C3%A4te/Vodafone-station-WLAN-schaltet-sich-st%C3%A4ndig-selbstst%C3%A4ndig-aus/td-p/2818556 ,
  https://forum.vodafone.de/t5/St%C3%B6rungen-im-Kabel-Netz/Router-startet-sich-vor-allem-Abends-st%C3%A4ndig-neu-AP-DISABLED/td-p/3189303 ,
  https://forum.vodafone.de/t5/Ger%C3%A4te/Vodafone-Station-Deaktiviert-WLAN-und-LAN-automatisch/td-p/3079782 ,
  https://forum.vodafone.de/t5/Ger%C3%A4te/Vodafone-Station-Wifi-6-WLAN-schaltet-sich-immer-wieder-aus/td-p/3014150
- [V10] [S, Blocked] https://forum.vodafone.de/t5/Ger%C3%A4te/WLAN-Probleme-mit-Vodafone-Station/m-p/3115102/highlight/true
- [V11] [S] https://www.dslweb.de/vodafone-station-stromverbrauch-optimieren.php
- [V12] [S] https://forum.vodafone.de/t5/Archiv-Internet-Ger%C3%A4te/Vodafone-Station-Gast-WLAN-Problem/td-p/2170945
- [V13] [S] https://www.giga.de/tipp/vodafone-homespot-hotspot-deaktivieren-so-gehts/
- [V14] [S] https://forum.vodafone.de/t5/Archiv-Internet-Ger%C3%A4te/Vodafone-Station-IP-Probleme-DHCP-LAN-Ger%C3%A4te/m-p/2522252/highlight/true ,
  https://discuss.freedombox.org/t/solved-rogue-dhcp-server-gives-freedombox-bogus-ip/2270 ,
  https://en.wikipedia.org/wiki/Rogue_DHCP
- [V15] [S] https://www.antwort.net/a/technologie/vodafone-station-startet-staendig-neu--ursachen-und-hilfestellung.html
- [V16] [S] https://www.vodafone.de/featured/service-hilfe/vodafone-station-firmware-update/
- [V17] [S] https://wiki.rdkcentral.com/doxygen/rdkv-opensourced/db/dfe/group___t_r69___h_o_s_t_i_f___w_i_f_i.html

Duplicate names, roaming, security:

- [R] https://support.apple.com/en-us/102766 (Apple: router settings; one DHCP, one NAT, same name on all bands, MAC filtering off)
- [R] https://support.apple.com/en-us/102509 (Apple: private Wi-Fi address)
- [R] https://networkmanager.dev/docs/api/latest/settings-802-11-wireless-security.html
- [S] https://community.ruckuswireless.com/t5/Access-Points-Indoor-and-Outdoor/R300-R500-WAP-installed-have-the-same-SSID-How-can-I-get-mobile/m-p/14338
- [S] https://www.cbtnuggets.com/blog/technology/networking/wireless-roaming
- [S] https://openwrt.org/_export/xhtml/docs/guide-user/network/wifi/roaming
- [S] https://forums.macrumors.com/threads/wifi-roaming-issues-two-aps-with-same-ssid.1208584/
- [S] https://community.metageek.com/t/security-mismatch/226
- [S] https://documentation.meraki.com/MR/Other_Topics/PMKID_Vulnerability_FAQ_-_WPA%2F%2FWPA2-PSK_and_802.11r
- [S] https://community.netgear.com/discussions/en-home-routers-nighthawk-wifi-ax6/rax120-fw1-2-8-40---some-devices-connected-to-wifi-but-no-internet/2269135/replies/2270163
- [S] https://forum.openwrt.org/t/debugging-issues-with-802-11w-while-trying-to-enable-wpa2-wpa3-mixed-mode/53234
- [S] https://service.avm.de/help/en/FRITZ-Box-7583/019/hilfe_howto_wpa3_fehlerbehebung
- [S] https://arxiv.org/pdf/2602.23513
- [S] https://www.purple.ai/en-gb/guides/wpa3-transition-mode-connection-failures (vendor blog)

Raspberry Pi as AP and router:

- [R] https://github.com/raspberrypi/linux/issues/7092
- [R] https://github.com/RaspAP/raspap-webgui/issues/462
- [S] https://github.com/raspberrypi/firmware/issues/1463
- [S] https://forum.openwrt.org/t/remove-limit-of-8-devices-on-wifi-network-on-raspberry-pi/128734
- [R] https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- [R] https://www.raspberrypi.com/documentation/computers/os.html
- https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/ (cited by the repo's capacity note, not re-fetched)
- [S] https://discourse.pi-hole.net/t/creating-an-access-point-using-bookworm/74446
- [S] https://forum.openwrt.org/t/rpi4-routing-performance-numbers/53996
- [S] https://forum.openwrt.org/t/a-few-sqm-benchmarks/71479
- [S] https://forum.openwrt.org/t/slow-bidirectional-routing-speed-on-raspberry-pi-4/181242
- [S] https://forum.openwrt.org/t/raspberry-pi-5-performance-with-snort/181136
- [S] https://forum.digikey.com/t/raspberry-pi-5-vs-other-models-network-test/53456
- [S] https://forum.openwrt.org/t/raspberry-pi-3-b-network-speeds-usb-to-ethernet/139699
- [S] https://github.com/morrownr/7612u
- [S] https://forum.openwrt.org/t/mt7612u-usb-wifi-which-driver-s/246802
- [S] https://github.com/morrownr/USB-WiFi/discussions/358
- [S] https://github.com/morrownr/USB-WiFi/issues/384

Capture and testing:

- [S] https://support.accessagility.com/hc/wireless-packet-capture-pcap-pcapng-macos
- [S] https://www.cisco.com/c/en/us/support/docs/wireless-mobility/wireless-mobility/217042-collect-packet-captures-over-the-air-on.html
- [S] https://mrncciew.com/2025/11/06/wi-fi-pcap-with-mac-os/
- [S] https://hannover.freifunk.net/wiki/Maschinenraum/TcpdumpFilter
- [S] https://oneuptime.com/blog/post/2026-03-20-wireshark-ipv6-bpf-capture-filters/markdown
- [S] https://ftc-docs.firstinspires.org/en/latest/control_system_troubleshooting/wireshark_packet_capture/wireshark-packet-capture.html
- [S] https://ask.wireshark.org/question/24850/rpi-4-wlan0-monitor-mode-does-not-output-any-packets/
- [S] https://netbeez.net/blog/linux-for-network-engineers-iperf3-bidirectional-test/
- [S] https://netbeez.net/blog/flent/
- [S] https://developer.apple.com/forums/thread/690358
- [Blocked, not used for claims] https://git.w1.fi/cgit/hostap/plain/hostapd/hostapd.conf
