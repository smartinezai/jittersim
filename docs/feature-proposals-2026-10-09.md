# Feature proposals (9 October 2026)

Thirty proposals from a read of comparable tools and practices. They extend
[the planned features page](planned-features.md), which keeps the status of
requested work. An item marked Delivered is done. Nothing else here is started
except phase 1 of item 1. Write an implementation plan before changing code and
keep each item bounded.

How to read an entry. **Size** is S (days), M (about a week) or L (more).
**Household risk** is the chance a mistake disturbs the home network. **Helper**
says whether the privileged helper or its unit changes. "Helper: yes" means the
change needs an independent Panic review before release, like the outage
logging work in [planned fixes](planned-fixes.md). Numbers in brackets point to
the Sources section. Items are ordered by value against risk.

## By theme

| Theme | Items |
| --- | --- |
| Realism of conditions | 21, 22, 25, 28, 29, 30 |
| Safety and recovery | 2, 4, 8, 13, 17 |
| Observability and measurement | 1, 5, 6, 15, 18, 23 |
| Scenario library | 3, 19 |
| User interface | 12, 24 |
| Devices and identity | 11 |
| Reliability and operations | 7, 14, 20 |
| Developer experience | 9, 10 |
| Security hardening | 16, 26, 27 |

## Proposals

1. **Per-device speed test** (observability). A button per device opens a bounded window while you run a public speed test on that device (default speed.cloudflare.com), then shows the speed it really got through its profile, with history and a before and after view.
   Phase 1 (M, being built on `feat/device-speed-test`): the Pi records the download and upload rate from the queue counters the Impact check reads (not the pre-queue traffic chart) and stores it with the profile active at that moment.
   Phase 2 (M-L, owner decision): a Pi-served page runs Cloudflare's open-source speedtest library and posts results back with a short-lived per-device code [32]. It needs a new listener on the test network, a CSP connect-src entry and rate limits. It also gives loaded latency and jitter. Packet loss needs a TURN server you supply.
   Household risk low (phase 1) and low to medium (phase 2). Helper: no. Depends on: Impact check.

2. **Panic and Normal access from the Mac command line** (safety). Stop everything from the Mac terminal without SSH or a browser.
   Why: the Mac commands have `profiles stop-all` but no Panic or Normal access (README). A known fast abort is the first rule of safe experiments [36] and a tool that stays on without a sign is a classic trap [6].
   Size S. Household risk low. Helper: no change, but it is a new Panic entry point so ask for the Panic review. Depends on: Mac command line (delivered).

3. **Cite and extend the profile presets** (scenario library). Each preset says what it is based on and what to expect, and a few missing ones are added (slow 3G as Chrome DevTools defines it, DSL, an 802.11ac-class Wi-Fi).
   Why: the nine dashboard presets (Good 4G to Offline) are labelled rough approximations. The same name carries different numbers in different tools. EDGE is 473 kbit/s in the Android emulator [7] and 250 kbit/s with 300 ms in Comcast [4]. 3G is 1.6 Mbit/s with 300 ms in WebPageTest [16]. Record the source beside each preset.
   Size S. Household risk low. Helper: no. Depends on: nothing. A "high latency DNS" preset [6] waits for the planned DNS delay.

4. **A lease the helper enforces itself** (safety). If the dashboard crashes or its scheduler stops, the Pi clears profile effects by itself, so a test can never leave the network degraded.
   Why: the README says the scheduler must keep running and independent expiry is still planned. Chaos Mesh warns that faults cannot be recovered once the controller loses its daemon [11]. Pi-hole and AdGuard Home both end a pause by themselves [17][18].
   Size M. Household risk low (it only removes effects, a false expiry ends a test early). Helper: yes. Depends on: README independent expiry, erratic plan delivery A.

5. **Run report with device notes** (observability). One page and a Markdown download show which profile ran on which device and when, the Impact readings, speed test results and notes such as phone model, OS and app build.
   Why: UniFi keeps per-client history and exports CSV or PDF [21]. Chaos practice compares the steady state before and after a run [36] and Litmus runs checks at start and end [12].
   Size M. Household risk low. Helper: no. Depends on: item 1, the activity log, the traffic chart interaction plan for the timeline.

6. **Uplink baseline probe** (observability). The Pi checks the router, DNS and a public host from its own address outside the test queues and shows whether the internet itself is healthy, so a bad result is not blamed on the profile.
   Why: the October router outage cause is still unconfirmed (planned fixes). Litmus checks health before and after a fault [12]. Teams advises a sample every 10 minutes and the 90th percentile [29].
   Size S-M. Household risk low. Helper: no (DNS and TCP probes, no raw ICMP). Depends on: persistent outage logs (merged), Diagnostics.

7. **Pi health banner and hardware watchdog** (reliability). Warn when under-voltage, throttling or heat could distort a test or the access point. A frozen Pi reboots by itself with profiles stopped.
   Why: the Pi reports low voltage, throttling and soft temperature limit now and since boot [22]. The hardware capacity note saw a past temperature flag. The hardware watchdog (about 16 s at most) does nothing until systemd `RuntimeWatchdogSec` is set [23].
   Size S-M. Household risk low (a watchdog restart drops test clients, try it on a spare Pi first). Helper: no if the dashboard user can read `vcgencmd`, else one read-only helper call. Depends on: the Pi reboot behaviour plan.

8. **Confirm or revert for Pi Wi-Fi changes** (safety). After copying the router Wi-Fi to the Pi or renaming the test Wi-Fi, the dashboard asks you to confirm within about 90 seconds. Without a reply the Pi restores its previous Wi-Fi settings.
   Why: OpenWrt does this for changes that can lock you out [19]. The 4 October trial relied on a hand-set rollback timer and ended with a router reset.
   Size M. Household risk low (it adds a way back). Helper: yes. Depends on: Copy router Wi-Fi to Pi, the Wi-Fi recovery panel, the Pi-only access point rollout plan.

9. **Test recipes for mobile apps** (developer experience). One short page per case: offline at launch, loss in the middle of an upload, Wi-Fi drop and rejoin, captive portal, IPv6-only. Each names the profile, what to watch and what a pass looks like.
   Why: iOS apps use waitsForConnectivity for the lift and Wi-Fi to cellular cases [28]. Apple's own conditioner ships 100% loss and high latency DNS profiles [6].
   Size S (docs only). Household risk none. Helper: no. Depends on: nothing. Later cases link to items 21, 22, 28 and 29.
   Delivered on 2026-10-23 in PR 176: [test recipes](test-recipes.md). Captive portal and IPv6-only are not included because JitterSim has neither, and the Wi-Fi drop and rejoin case says it is not available yet.

10. **Scenario files and a run command** (developer experience). Keep conditions in the app's repository and replay them identically with `jittersim run slow-upload.json --wait`, with a `--dry-run` that prints what would change.
   Why: Toxiproxy tests set faults from code and reset them afterwards [2]. Comcast offers `--dry-run` and `--stop` [4]. Facebook's ATC kept shared profiles such as 2G rural and No Connectivity [13].
   Size M. Household risk low. Helper: no. Depends on: existing JSON export and import, the Mac command line, erratic plan stages for timelines.

11. **New-device notice** (devices and identity). When an unknown device joins while the general profile is on, a banner says it is now affected and offers Keep or Protect.
   Why: the README says the general profile also reaches clients that connect later, and a protected device that randomises its address returns as new.
   Size S. Household risk low. Helper: no. Depends on: the archived "Keep a device protected when its Wi-Fi address changes" idea, which needs the same banner.

12. **Notifications** (user interface). A phone alert when profiles have been on for hours, when Panic fires or when the Pi runs hot. Messages carry no device names or addresses.
   Why: Apple's conditioner shows nothing when it is left on [6]. ntfy publishes with one HTTP request and can be self-hosted [39].
   Size S-M. Household risk low (outbound only, address set by the admin). Helper: no. Depends on: item 7.

13. **Household recovery card** (safety). A printable one-page card to keep next to the router: Pi power, the router Wi-Fi button, the dashboard address, `sudo jittersim normal-access` and what normal looks like.
   Why: the 4 October outage needed a router reset and the recovery steps were spread over several documents.
   Size S (docs only). Household risk none. Helper: no. Depends on: the Pi acceptance checklist and the router outage notes.
   Delivered on 2026-10-23 in PR 176: [household recovery card](household-recovery-card.md).

14. **Pull a backup to the Mac** (reliability). `jittersim backup pull` saves the settings backup to the Mac on demand or on a schedule, and a restore drill proves it works.
   Why: a brown-out or power cut can still damage a Pi's card, so keep an image backup and a spare card [24]. The dashboard only offers a manual download today. A read-only root is a separate spike, because saved data would need its own writable partition [24].
   Size S. Household risk low. Helper: no. Depends on: the existing backup download, Mac command line.
   Delivered on 23 October 2026, see [the plan](implementation-plan-backup-pull-2026-10-23.md). The command runs once each time. A schedule is a cron or launchd line in the user guide. The database file has no restore command, only manual steps in a runbook.

15. **Prometheus metrics** (observability). A read-only page for Grafana: set against actual loss and speed, queue wait, Pi temperature and throttle flags over days.
   Why: the text format is a plain HTTP page [40] and the Impact check already reads the counters. Token protected and reachable only on the management network.
   Size S-M. Household risk low. Helper: no. Depends on: item 7 for the Pi numbers.

16. **Scoped API tokens** (security hardening). A token that can only switch profiles on named devices, for scripts and CI. It cannot touch the router, Wi-Fi, updates or access rules.
   Why: today one admin token does everything. Toxiproxy has the same single token [2]. Tailscale auth keys expire in 1 to 90 days [20].
   Size M. Household risk low to medium (keep the admin token path unchanged so the owner cannot be locked out). Helper: no, but Panic must work with every token so ask for the Panic review. Depends on: Mac command line, item 10.

17. **Physical Panic button and status light** (safety). A hold-to-stop button and a light on the Pi: green normal, amber profiles on, red Panic. Recovery and state need no phone.
   Why: gpiozero supports debounce and a hold time for a button [38]. Apple's conditioner gives no on-state sign [6].
   Size S-M. Household risk low. Helper: yes (a new Panic caller). Needs a button and LED on the GPIO header. Depends on: nothing.

18. **Use-case verdicts** (observability). Plain verdicts such as fine for browsing, poor for a video call, bad for a game, from measured latency, loss and speed. Apple's responsiveness score (round trips per minute under load) is one more number worth showing [31].
   Why: Cloudflare's AIM rates streaming, gaming and calls from latency, loss, speed and loaded latency [32]. Teams targets are round trip under 60 ms, average loss under 0.5% and jitter under 3 ms [29]. Zoom community posts name 150 ms, 2% and 40 ms [30]. The E-model turns delay and loss into a call score [33]. WebRTC stats expose jitter and lost packets for a call-style test [34].
   Size M. Household risk low. Helper: no. Depends on: item 1 phase 2 and the archived "Measuring from more devices" idea.

19. **Timeline scenario pack** (scenario library). Ready-made scenarios that change over time: a commute (good, tunnel, hand-over, good), a lift, a crowded stadium.
   Why: Mahimahi shows time-varying cellular links are the useful test [15]. The existing Train tunnel preset is static.
   Size M. Household risk medium (live queue changes). Helper: no new change beyond the stage engine. Depends on: erratic plan delivery C.

20. **Capacity self-test** (reliability). A command that measures how much combined traffic this Pi can shape and forward before CPU or heat limits show. It runs in a network namespace with virtual interfaces, only while profiles are off.
   Why: the hardware capacity note says loaded capacity was never measured and 300 Mbit/s is a specification, not a result. Mahimahi shows the namespace approach [15].
   Size M. Household risk low to medium (the Pi is busy while it runs). Helper: yes. Depends on: item 7.

21. **Restricted network switches** (realism). Two switches: block QUIC (UDP 443) and allow only web ports and DNS. See whether an app falls back or breaks on strict office and hotel networks.
   Why: Teams needs UDP and says firewalls and VPNs break its media [29]. The erratic plan lists UDP interruptions only as later work. This is the smallest slice.
   Size S-M. Household risk low to medium (chosen devices only). Helper: yes. Depends on: access rules, protected devices.

22. **Wi-Fi drop and rejoin** (realism). One button removes a chosen test device from the Wi-Fi, optionally with a new address, to test how an app handles a network change.
   Why: apps fail in practice when the connection flips between Wi-Fi and cellular or waits for a network [28]. `hostapd_cli deauthenticate` and `iw station del` exist, but the Pi runs a NetworkManager hotspot so check what works there [37].
   Size M. Household risk low to medium (one device, never a protected one). Helper: yes. Depends on: devices list, protected devices.

23. **Single-device packet capture** (observability). Record up to 60 seconds of one test device's packets (headers only by default) as a file for Wireshark to see resets, retries and DNS. Never a protected device.
   Why: Charles and Proxyman show HTTP only through a proxy and a trusted certificate [8][9]. mitmproxy needs WireGuard or a changed gateway for phones [10]. The Pi is already on the path.
   Size M. Household risk low (read only, but a Pi 3 is slow). Helper: yes, a new privileged read. Depends on: the protected device exclusion rules.

24. **"This device" card** (user interface). Open the dashboard on the phone under test and one card applies a profile to this phone only, with a timer and a large Panic button. Hidden for protected phones.
   Why: the owner tests with a phone in hand and dashboard traffic is not shaped, so the page keeps working (README).
   Size S-M. Household risk low. Helper: no. Depends on: the phone layout (delivered), protected device dashboard access (PR 92).

25. **Queue behaviour choice** (realism). Per profile, choose a shallow, normal or deep buffer and plain tail drop or fq_codel, to see latency under load and how a well-managed link feels.
   Why: bufferbloat is latency added under load and fq_codel or CAKE fix it [35]. Chaos Mesh suggests a queue limit of at least twice rate times latency [11]. netem combines poorly with other queues [1].
   Size M. Household risk medium. Helper: yes. Depends on: the archived "Queue limit" idea, erratic plan delivery A.

26. **Sandbox the helper unit** (security hardening). Limit what a flaw in the privileged helper could do.
   Why: the dashboard, proxy, DNS, mDNS, ad-blocking and service-watch units already use systemd sandboxing options. `jittersim-helper.service` uses none. Keep only the capabilities it needs.
   Size M. Household risk medium (a strict unit that fails would stop Panic, so test on a spare Pi and keep restart on failure). Helper: yes. Depends on: nothing.

27. **Release supply chain** (security hardening). Keep the release signing key on a hardware key and audit dependencies in CI.
   Why: releases are signed with `ssh-keygen -Y` and an ed25519 key. OpenSSH 8.2 added FIDO key types [41]. A first spike must confirm signing and verifying work with them on the Mac and the Pi. `pip-audit` checks hash-pinned lock files against advisories [42].
   Size S spike then M. Household risk low. Helper: only if the updater's verify path changes. Depends on: the stable releases plan.

28. **Captive portal mode** (realism). The test Wi-Fi shows a sign-in page until a phone accepts it, like hotel Wi-Fi.
   Why: phones probe a fixed address and expect a fixed answer. Any other answer opens a sign-in sheet [27]. RFC 8908 gives newer clients a JSON API [27].
   Size L. Household risk medium (per-device redirect rules, never for protected devices or the dashboard). Helper: yes. Depends on: access rules, protected devices.

29. **IPv6-only mode with NAT64** (realism). Switch the test Wi-Fi to IPv6-only with NAT64 and DNS64 to check that an app is IPv6-ready.
   Why: Apple has required IPv6-only compatibility since 2016 [25]. The test Wi-Fi is IPv4 only today. Jool or TAYGA with unbound DNS64 can do it [26] but no guide for a Pi was found.
   Size L. Household risk medium to high (every client on that Wi-Fi is affected, restore on Reset). Helper: yes. Depends on: an owner decision about a second SSID, a spare Pi first.

30. **Trace replay** (realism). Replay a recorded real-world speed and delay trace on one device.
   Why: Mahimahi's link shell replays packet delivery traces of cellular links [15]. netem `slot` and delay distributions only approximate bursty links [1].
   Size L. Household risk medium. Helper: yes. Depends on: erratic plan deliveries A and C, the file format of item 10.

## Already planned, not repeated

- Clustered loss, long-tail delay, changing stages, pause then release, connection lottery, late interruption, service presets for ChatGPT, Claude, Teams and Google, usage-driven worsening, nearly finished downloads, failing new connections only, DNS delay or drop, large-packet failure, UDP interruptions and group incidents: see the erratic connections plan (untracked in `docs/`, deliveries A to F and "Later planned features").
- Stable releases and numbered packages: stable releases plan. Dashboard foundations, accessibility and chart: GUI improvement plan and traffic chart plan. Website modes: website modes plan.
- Command palette, one state stream and undo toasts: the archived UX overhaul plan.
- Archived ideas that still look open and need a source check: queue limit, wait for the clock, router login throttle, Wi-Fi password in the process list, measuring from more devices, keep a device protected when its address changes, automatic spare-Pi acceptance run.
- Independent expiry for temporary scenarios (README) is the base of item 4.

## How JitterSim compares

Per-device shaping with a web page and saved profiles is what Facebook's ATC did [13] and phone conditioners do on the device [6][7]. Toxiproxy and Pumba add connection faults and timed runs [2][3] and Chaos Mesh adds targets and recovery rules [11]. Proxyman and Charles add per-host throttling [8][9] and Clumsy adds lag, drop, throttle, reorder and tamper with packet filters [5]. Facebook's 2G Tuesdays made slow-network testing a staff habit but was opt-in [14]. JitterSim already covers shaping, service lists and Panic. The main gaps are measuring what the device got (items 1, 5, 18), conditions beyond packet shaping (21, 22, 28, 29) and recovery that does not depend on the dashboard (4, 8, 17).

## Not proposed

- HTTPS interception to read or change page content. The erratic plan keeps the privacy boundary.
- Weak Wi-Fi signal emulation. The Pi radio cannot do it faithfully and no source was found.
- A light theme. The owner decided the dashboard stays dark.

## Sources

Read on 9 October 2026. Several pages were read through search summaries only. The raw notes list which.

1. tc-netem man page: https://man7.org/linux/man-pages/man8/tc-netem.8.html
2. Toxiproxy: https://github.com/Shopify/toxiproxy
3. Pumba: https://github.com/alexei-led/pumba
4. Comcast: https://github.com/tylertreat/comcast
5. Clumsy: https://jagt.github.io/clumsy/
6. Network Link Conditioner: https://www.nshipster.com/network-link-conditioner/ and https://developer.apple.com/forums/thread/706251
7. Android emulator command line: https://developer.android.com/studio/run/emulator-commandline
8. Charles throttling: https://www.charlesproxy.com/documentation/proxying/throttling/
9. Proxyman network conditions: https://docs.proxyman.io/advanced-features/network-throttling
10. mitmproxy modes: https://docs.mitmproxy.org/stable/concepts/modes/
11. Chaos Mesh network chaos: https://chaos-mesh.org/docs/simulate-network-chaos-on-kubernetes/
12. LitmusChaos probes: https://docs.litmuschaos.io/docs/concepts/probes
13. Augmented Traffic Control: https://github.com/facebookarchive/augmented-traffic-control
14. Facebook 2G Tuesdays: https://www.nbcnews.com/tech/social-media/facebooks-2g-tuesdays-slow-down-internet-employees-n452466
15. Mahimahi: https://github.com/ravinet/mahimahi and https://dyn.manpages.debian.org/buster/mahimahi/mm-link.1.en.html
16. Preset numbers differ by tool: https://www.debugbear.com/blog/chrome-devtools-network-throttling and https://forums.webpagetest.org/t/updating-3g-profile/8028
17. Pi-hole: https://docs.pi-hole.net/api/ and https://docs.pi-hole.net/main/pihole-command/
18. AdGuard Home: https://github.com/AdguardTeam/AdGuardHome/wiki/Configuration
19. OpenWrt apply and rollback: https://forum.openwrt.org/t/disabling-auto-rollback/16315
20. Tailscale auth keys and key expiry: https://tailscale.com/docs/features/access-control/auth-keys.md and https://tailscale.com/docs/features/access-control/key-expiry
21. UniFi client history: https://www.unihosted.com/blog/how-to-see-device-history-in-unifi-controller (third party)
22. Pi throttle flags: https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/os/graphics-utilities.adoc
23. Pi hardware watchdog: https://dyn.manpages.debian.org/buster/systemd/system.conf.d.5.en.html and https://elixir.bootlin.com/linux/v3.18.34/source/drivers/watchdog/bcm2835_wdt.c
24. Read-only Pi and backups: https://learn.adafruit.com/read-only-raspberry-pi
25. Apple IPv6 support: https://developer.apple.com/support/ipv6/
26. NAT64 with Jool or TAYGA: https://openwrt.org/docs/guide-user/network/ipv6/nat64
27. Captive portals: https://www.rfc-editor.org/rfc/rfc8908.html and https://isc.sans.edu/forums/diary/28234/
28. waitsForConnectivity: https://useyourloaf.com/blog/urlsession-waiting-for-connectivity/
29. Teams network: https://learn.microsoft.com/en-us/microsoftteams/prepare-network and https://learn.microsoft.com/en-us/previous-versions/skypeforbusiness/optimizing-your-network/media-quality-and-network-connectivity-performance (archived targets)
30. Zoom thresholds (community post about Zoom Phone): https://community.zoom.com/t5/Zoom-Meetings/Zoom-Network-Monitoring/m-p/69544
31. Apple responsiveness: https://support.apple.com/en-ca/101942 and https://datatracker.ietf.org/doc/draft-ietf-ippm-responsiveness/08/ (a draft)
32. Cloudflare speedtest and AIM: https://github.com/cloudflare/speedtest and https://developers.cloudflare.com/speed/aim/
33. ITU-T G.107 E-model: https://www.itu.int/dms_pubrec/itu-t/rec/g/T-REC-G.107-200503-S!!TOC-HTM-E.htm
34. WebRTC inbound stats: https://developer.mozilla.org/en-US/docs/Web/API/RTCInboundRtpStreamStats
35. Bufferbloat, fq_codel and CAKE: https://speedtesthq.com/guides/diagnostics/test-for-bufferbloat and https://upd.dev/kernel/linux/commit/046f6fd5daef
36. Principles of chaos engineering: https://principlesofchaos.org/
37. Removing a Wi-Fi client: https://forum.openwrt.org/viewtopic.php?id=62543
38. gpiozero Button: https://gpiozero.readthedocs.io/en/stable/api_input.html
39. ntfy publishing: https://docs.ntfy.sh/publish/
40. Prometheus text format: https://prometheus.io/docs/instrumenting/exposition_formats/
41. OpenSSH 8.2 FIDO keys: https://www.openssh.com/txt/release-8.2
42. pip-audit: https://github.com/pypa/pip-audit
