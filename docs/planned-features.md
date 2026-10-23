# Planned features

This active list replaces the dated October2 status snapshot. The complete
original backlog and design proposals remain in [the historical archive](history/2026-10-02-backlog-and-ux/README.md).

Baseline: `origin/main` at `f290ea9d` (the merge of PR 171 on 19 October 2026), checked on
19 October 2026. Statuses come from the source and the merged pull requests on that commit.
Nothing here was run on a Pi, so physical acceptance remains outstanding. The 4 October accepted
main `5e0fed2` matched tested candidate 6fb864f (see
[final local verification](verification-final-local-release-2026-10-04.md)). Main has moved on
since: the changes of 9 to 19 October are in [planned fixes](planned-fixes.md) and the
[changelog](../CHANGELOG.md). Hosted jobs remain unavailable because of billing limits. Every pull
request is gated by `scripts/local-ci.sh` on its head commit ([local CI](local-ci.md)).

| Requested work | Current source status | Remaining work |
| --- | --- | --- |
| Neutral sign-in and defaults | Delivered to main | Verify final installed package. Existing Wi-Fi names are preserved. |
| Homepage bounce | Reviewed live HTTPS pairing and controlled extension support are implemented | Verify real test-device identity, normal extension trust and physical recovery. DNS cannot redirect page paths. |
| Real phone acceptance | Outstanding | Use safe recovery and actual affected/protected clients. Verify shared limits, privacy and reconnection. |
| Chrome dashboard access | Source includes trust and reconnect assistance | Establish the actual browser failure. Earlier permission and VPN guesses are unproven. |
| Router Wi-Fi controls | Deliberate read-only checks and the copy to the Pi are implemented. Router Wi-Fi writes are refused since PR 87 | Turn router Wi-Fi on or off with the router's own button or page. The physical outage cause is unproven. |
| Remote CLI and official deployment | Delivered to main. `scripts/release.sh` cuts, checks, signs and publishes a release, and `sudo jittersim update` or `jittersim remote update now` updates the Pi in one step (PR 108, 110, 122) | Trust the release key and verify recovery on the Pi. The first release, v0.2.0, is not cut yet (no tag, no GitHub release). |
| Integration | Delivered to main after every configured local counterpart passed | Verify the installed release separately. |
| Remembered credential renewal | Delivered to main | Verify real installed-browser behavior separately from fixture coverage. |
| Phone dashboard layout | Safety bar, navigation and fixed help are implemented | Real phones, Safari and test/management network acceptance remain. |
| Profile and group controls | Saved choices, unfinished-form links and bulk groups are implemented ("Profile for this group", PR 84) | Keep bounded queue and stale-setting checks. |
| Apply a profile to one device | Implemented. After a reset network the device starts alone and everything else stays paused (PR 96, 127, 131). While Everyone runs, a device runs its own profile beside it and Everyone keeps running for every other device (PR 149). Refused before saving while Everyone is saved but not running, while what runs is older than the saved settings, or while Panic is latched ([design addendum](device-apply-start-design-2026-10-09.md)) | Physical check on the Pi with two phones, one on Everyone and one on its own profile. |
| Random ranges and patterns | Implemented with legacy relative selection preserved | All 40 kernel cases and native packet checks passed on the 4 October candidate. Physical client acceptance remains. |
| Erratic connections (Delivery A) | Reliability foundations implemented: independent outage choices, service-list behaviour, live queue updates that keep counters, fresh or repeatable patterns (PR 84). Upload corruption runs on an `ifb` device and never toward the router (PR 125, 135) | Deliveries B to F are not started: long delays, condition stages and pauses, connection faults and presets, nearly finished downloads, usage worsening. The limits, counters, restart and rollback gate and independent expiry after a dashboard crash remain ([plan](implementation-plan-erratic-connections-2026-10-03.md)). |
| Signed and online releases | Implemented. The Pi checks GitHub for a signed release with a read-only token, and the release and local CI tooling is merged (PR 108, 109, 110, 114, 116, 122, 139). The Updates page names the staged release (PR 112). The update status carries the signed release notes, a check a person asks for only checks and stages, and a development-label build that stopped can be retried (PR 111) | Cut v0.2.0 (see below), complete signing trust and the physical release gates. Do not label unavailable hosted CI green. |
| Persistent outage logs | Merged with the dashboard token wait fix (PR 86, 91) and a read-only router health canary (PR 133). An independent Panic-consumer review approved the helper and access modules of that day as a bundle that `PANIC_APPROVED_SOURCES` still lists ([review](review-results-panic-consumers-outage-logging-2026-10-09.json)). The newest entry is the one for PR 138. A review is not approval of deployment | Actual disposable-system reboot proof and the owner's capture of router event times ([outage dossier](outage-dossier-2026-10-04-2026-10-09.md)). Since PR 142 an install or update keeps the journal on disk (`90-jittersim-persistent.conf`), which a reboot on the Pi has to confirm. |
| Protected-device dashboard access | Merged (PR 92, landed with the combined Panic batch in PR 136). A protected device opens the dashboard with the admin token and no separate Allow this device step, the access list shows the reason, and an emergency device check sits on the Panic path ([plan](implementation-plan-protected-device-dashboard-access-2026-10-04.md)) | Physical check on the Pi with a protected phone. The helper socket still authorizes by group only ([design note](design-helper-action-authorization-2026-10-09.md), proposal only). |
| Pi Wi-Fi follows the router | Merged (PR 140). After a **Copy router Wi-Fi to Pi** that worked, the Pi records the router's Wi-Fi name and security (no password). Every boot renames a saved Wi-Fi with another name, such as `JitterSim-Recovery`, back to the router name, and a manual Wi-Fi change or a name reset stops the following (`src/jittersim/networking/wifi_follow.py`, [Pi guide](../deploy/raspberry-pi/README.md)) | Physical check on the Pi that a reboot returns from Recovery to the router name. `python -m jittersim.networking.wifi_follow recovery` holds Recovery across reboots when the owner wants that. |
| Panic hardening | Merged as one reviewed batch with one approved bundle (PR 136, which carried PR 103 and 104): a stop that survives a helper restart, a household release that opens the firewall first, a reserved helper slot, a bounded busy retry and one total Panic deadline ([review](review-results-gated-panic-batch-2026-10-10.json)). PR 138 (18 October) added the carry-forward fixes with a second approved bundle: a stalled stop-record write no longer holds Panic, dashboard Panic waits a bounded time for the scheduler lock, a rollback keeps the current `dashboard-access.json` and cleans up the upload intake ([review results](review-results-carry-forward-2026-10-16.md)). PR 168 removes what an interrupted apply left when the helper restarts, and asks for Panic if that removal fails. Those reviews are not approval of deployment | Two owner decisions (block a rollback that cannot remove the upload intake, and whether a rollback keeps the protected device list), the review notes O1 and O2, the same stall class in the household release and the owner action after a rollback from the first upload-safe release ([carry-forward list](carry-forward-panic-batch-2026-10-10.md)). |
| Device speed test and connection check | **Test speed** is merged (PR 121, review follow-up PR 165). **Check connection** is merged (PR 161, review follow-up PR 186): the Pi sends probes and compares loss, round trip, jitter, duplicate answers and out-of-order answers with the applied download profile. PR 190 corrected the loss guidance (about 5% or more can be told from a healthy link with 100 probes) ([plan](implementation-plan-connection-check-2026-10-17.md)) | Physical check with a real phone. Check connection does not judge upload shaping or a speed cap. |
| Follow private Wi-Fi address changes | Merged (PR 151): the **Address changes** card detects a saved device that returns under a new private address and carries its profile and name over (Ask, Automatic or Off), with a history and Undo for 7 days ([plan](implementation-plan-follow-address-changes-2026-10-17.md)). Client-id and vendor class are not used because they need a helper change (Panic-pinned) | Physical check with a real phone that rotates its address. |
| Router safety audit, round 2 | Merged (PR 150, 157): a router-side capture found three things the Pi sent onto the household LAN, mostly with no profile running (TCP resets and similar packets without NAT, ARP answers for its Wi-Fi address on the uplink, and a forwarded packet with an ICMP redirect). The management firewall now drops them, and an install or update checks the rules first ([audit](router-safety-audit-round2-2026-10-17.md)) | The read-only checks on the real Pi and the owner decisions of the audit (dual access point after every boot, download damage in Pi-only mode, a runtime same-subnet refusal). |
| Two Wi-Fis with one name | Merged (PR 160, 169): the boot step looks once at the Wi-Fi names on the air and keeps `JitterSim-Recovery` for that boot when the router name is on the air from another BSSID. The look does not depend on the Pi's language. The test Wi-Fi refuses to start when the Ethernet address is inside `192.168.50.0/24` | A physical check on the Pi that includes a cold boot with the router Wi-Fi on and shows `boot_scan: seen`, not `failed`. |
| Audits and test depth | Merged: a security audit of main with four fixes (PR 159, [report](security-audit-2026-10-18.md)), a soak harness and three resource fixes (PR 164, [audit](soak-and-resource-audit-2026-10-18.md)), mutation testing (PR 166, [report](mutation-testing-2026-10-18.md)), property-based codec tests (PR 167) and route tables for the dashboard's reads (PR 155) and writes (PR 163). PR 170 (draft) finishes the security audit follow-ups | Owner decisions in the audit reports and review of the draft pull requests. |
| Dashboard accessibility and GUI improvements | Partly delivered: a skip link and unique regions (PR 88), in-page questions with focus return (PR 141), one announcer and quiet polled lists (PR 146) and the QA sweep fixes (PR 152) | Per-page titles and headings, form error text, a data-scaled traffic axis with a text alternative and the other slices of the GUI improvement plan (that plan is not committed to the repository, see the [backlog audit](backlog-audit-2026-10-16.md)). |
| Pi boot robustness | Delivered to main (PR 142): the `.local` name is announced (Avahi setting corrected), the name service survives an early hangup and restarts, the journal stays on disk and the boot noise is gone | Physical check on the Pi after an update and a reboot: `netlab.local` resolves and `journalctl --list-boots` shows more than one boot. |
| Additional Website List modes | Not started. `website_mode` accepts only `unrestricted` and `allow_only` | Review scope and verify the proposed accounting behavior. The plan expects a helper change. |
| Mark the device in use in the Devices tab | Delivered to main (PR 181): the Connected clients row of the device you browse with over the Pi's Wi-Fi says **This device**. The match uses the Wi-Fi address the Pi verified for the request, so Ethernet and loopback requests show no label ([plan](implementation-plan-devices-this-device-2026-10-23.md)) | Physical check with a real phone on the test Wi-Fi, including one with a private Wi-Fi address. |
| Speed test from the dashboard alone | Requested on 23 October 2026, not started. Today the **Test speed** button of PR 121 asks the device to reach an outside server (Cloudflare), and **Check connection** (PR 161) measures from the Pi | The dashboard serves the test itself: the browsing device downloads and uploads generated bytes from and to the Pi and the dashboard shows the throughput it measured through the shaped path. Any new route that sends or accepts large bodies is a security and denial-of-service risk, so it needs the admin token, a size limit, a rate limit and one run per device at a time (see the [speed test plan](implementation-plan-device-speed-test-2026-10-09.md) and the [security audit](security-audit-2026-10-18.md)). |
| Bigger global block list | Requested on 23 October 2026, not started. The global block list holds at most 100 names, which is too few | Raise the limit to a few thousand names without making the nft sets or the dashboard slow. Measure first, and keep the validation and the Panic path safe. |
| Quick button to reset the Pi Wi-Fi name | Delivered to main (PR 182): a **Reset Wi-Fi name** button in the sidebar, on every page, with the confirm dialog of PR 141 | Physical check on the Pi: press it from a phone on the test Wi-Fi and see the page wait, name the network to join and show the result after the phone rejoins. |
| Protected devices not affected by the global block list | Requested on 23 October 2026. Checked on 23 October: protected devices skip profile shaping but are **not** exempt from the global block list (the firewall drop comes before the protected return and the household DNS redirect reaches every device) | Make the smallest fail-safe change: a wrong exemption only un-blocks, protection never moves automatically and nothing delays Panic. A protected device that opted into DNS ad blocking keeps only that. The DNS part needs the Panic-pinned `access.py`, so it waits until after v0.2.0 (an independent review and one new `PANIC_APPROVED_SOURCES` entry). |
| Dashboard redesign with components | Requested on 23 October 2026, not started and not before v0.2.0 is released | Port the dashboard to components for a smoother, more interactive and more responsive interface. The recommended path in the [UI overhaul proposal](ui-overhaul-proposal-2026-10-09.md) is Preact (about 6 KB gzipped), built with esbuild on the Mac only, the built file committed so the Pi never needs Node, and the hashed CSP kept. Start with the Devices page and keep every id and label so the browser journeys still pass, then go page by page. Not while feature pull requests still edit `dashboard.js`. React (about 70 KB gzipped) was rejected, and Angular, Svelte, Lit and Alpine fit poorly. Open question for the owner: what exactly feels choppy today? |

## In flight on 19 October 2026

Merged since 17 October: PR 112 (named install button, notes and recovery copy), 117 (wheel build
lock), 138 (carry-forward fixes of the Panic batch), 139 (release tooling fixes from a real dry run
of v0.2.0), 141 and 146 (in-page questions, one announcer), 142 (Pi boot robustness), 143
(stability pass), 147 and 148 (dead code and unused prototypes removed), 152 (QA sweep), 154
(release test image), 111 (signed notes in the update status, check-only manual checks), 121
(device speed test), 149 (a device runs its own profile beside Everyone), 150 and 157 (router
safety audit round 2 and its follow-ups), 151 (address changes), 155 (read route table), 159
(security audit), 160 and 169 (boot Wi-Fi look), 162 (QA leftovers and flaky tests), 164 (soak
harness and resource fixes), 163 (write route table), 165 (speed test follow-ups), 166 (mutation
testing), 167 (property tests) and 168 (interrupted apply cleanup). PR 161 and 170 were drafts
on that day and have merged since (PR 170 on 19 October, PR 161 on 23 October). The open pull
requests today are on GitHub, and the [roadmap](../ROADMAP.md) lists the ones it follows.

The first release, v0.2.0, is being prepared. `pyproject.toml` already says `0.2.0`, no version is
reserved in `releases/registry.json`, and no tag or GitHub release exists. The tooling is merged.
What is left is the owner's: the one-time root install of current `main` on the Pi, the read-only
GitHub token, a Mac connection to the dashboard, the run of `scripts/release.sh 0.2.0` and the
physical acceptance of the exact package (see the [owner runbook](release-runbook-2026-10-16.md)
and the [draft of the release notes](release-notes-0.2.0-draft.md)). With PR 111 merged, the
dashboard can show the release notes of a staged release.

## Further ideas

The complete earlier ideas are retained in the archive. Their old planned or
finished labels are not current evidence. Recheck the owning source before
starting any one of them. Useful follow-ups include measuring from phones,
traffic labels, durable outage evidence, deliberate scheduling after recovery
and an automatic spare-Pi acceptance run. Keep each new feature bounded and
write its implementation plan before changing code.

The researched proposals of 9 October 2026 (thirty items with size, household
risk and helper impact) are in [feature proposals](feature-proposals-2026-10-09.md).

The dashboard remains dark. The archived interface prototype is optional and
is not a description of the delivered interface. The refreshed look of PR 115
keeps the dark-only baseline.

## Evidence

Use [the coordinated verification notes](verification-coordinated-candidate-2026-10-04.md)
and [the physical checklist](pi-acceptance-checklist.md). Fresh GitHub checks and
actual main identity must be recorded at publication. Unrun hardware checks
must not be inferred from browser fixtures or a reachable management address.

See [final local verification](verification-final-local-release-2026-10-04.md).
