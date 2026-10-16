# Planned features

This active list replaces the dated October2 status snapshot. The complete
original backlog and design proposals remain in [the historical archive](history/2026-10-02-backlog-and-ux/README.md).

Baseline: `origin/main` at `2f68772` (the merge of PR 140 on 16 October 2026), checked on
17 October 2026. Statuses come from the source and the merged pull requests on that commit.
Nothing here was run on a Pi, so physical acceptance remains outstanding. The 4 October accepted
main `5e0fed2` matched tested candidate 6fb864f (see
[final local verification](verification-final-local-release-2026-10-04.md)). Main has moved on
since: the changes of 9 to 16 October are in [planned fixes](planned-fixes.md). Hosted jobs remain
unavailable because of billing limits. Every pull request is gated by `scripts/local-ci.sh` on its
head commit ([local CI](local-ci.md)).

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
| Apply a profile to one device | Implemented for a reset network: the device starts alone and everything else stays paused (PR 96, 127, 131). Refused before saving while Everyone runs or Panic is latched | Let the device run its own profile while Everyone runs. It needs the general-profile renderer to leave the device out of the shared mark and Linux packet checks ([design addendum](device-apply-start-design-2026-10-09.md), "Not covered"). |
| Random ranges and patterns | Implemented with legacy relative selection preserved | All 40 kernel cases and native packet checks passed on the 4 October candidate. Physical client acceptance remains. |
| Erratic connections (Delivery A) | Reliability foundations implemented: independent outage choices, service-list behaviour, live queue updates that keep counters, fresh or repeatable patterns (PR 84). Upload corruption runs on an `ifb` device and never toward the router (PR 125, 135) | Deliveries B to F are not started: long delays, condition stages and pauses, connection faults and presets, nearly finished downloads, usage worsening. The limits, counters, restart and rollback gate and independent expiry after a dashboard crash remain ([plan](implementation-plan-erratic-connections-2026-10-03.md)). |
| Signed and online releases | Implemented. The Pi checks GitHub for a signed release with a read-only token, and the release and local CI tooling is merged (PR 108, 109, 110, 114, 116, 122) | Cut v0.2.0 (see below), complete signing trust and the physical release gates. Do not label unavailable hosted CI green. |
| Persistent outage logs | Merged with the dashboard token wait fix (PR 86, 91) and a read-only router health canary (PR 133). An independent Panic-consumer review approved the helper and access modules of that day as a bundle that `PANIC_APPROVED_SOURCES` still lists ([review](review-results-panic-consumers-outage-logging-2026-10-09.json)). The newest entry is the one for PR 136. A review is not approval of deployment | Actual disposable-system reboot proof and the owner's capture of router event times ([outage dossier](outage-dossier-2026-10-04-2026-10-09.md)). |
| Protected-device dashboard access | Merged (PR 92, landed with the combined Panic batch in PR 136). A protected device opens the dashboard with the admin token and no separate Allow this device step, the access list shows the reason, and an emergency device check sits on the Panic path ([plan](implementation-plan-protected-device-dashboard-access-2026-10-04.md)) | Physical check on the Pi with a protected phone. The helper socket still authorizes by group only ([design note](design-helper-action-authorization-2026-10-09.md), proposal only). |
| Pi Wi-Fi follows the router | Merged (PR 140). After a **Copy router Wi-Fi to Pi** that worked, the Pi records the router's Wi-Fi name and security (no password). Every boot renames a saved Wi-Fi with another name, such as `JitterSim-Recovery`, back to the router name, and a manual Wi-Fi change or a name reset stops the following (`src/jittersim/networking/wifi_follow.py`, [Pi guide](../deploy/raspberry-pi/README.md)) | Physical check on the Pi that a reboot returns from Recovery to the router name. `python -m jittersim.networking.wifi_follow recovery` holds Recovery across reboots when the owner wants that. |
| Panic hardening | Merged as one reviewed batch with one approved bundle (PR 136, which carried PR 103 and 104): a stop that survives a helper restart, a household release that opens the firewall first, a reserved helper slot, a bounded busy retry and one total Panic deadline ([review](review-results-gated-panic-batch-2026-10-10.json)). That review is not approval of deployment | The carry-forward items in PR 138 and the owner action after a rollback from the first upload-safe release ([carry-forward list](carry-forward-panic-batch-2026-10-10.md)). |
| Device speed test | Open in PR 121 | Review and local CI on its head commit. |
| Additional Website List modes | Not started. `website_mode` accepts only `unrestricted` and `allow_only` | Review scope and verify the proposed accounting behavior. The plan expects a helper change. |

## In flight on 17 October 2026

The open pull requests are listed with their state under Current remaining work in the
[roadmap](../ROADMAP.md): PR 111, 112, 117, 121, 138, 139, 141, 142, 143 and 146. PR 136
(the combined Panic batch), the PR 92, 103 and 104 it carried, and PR 140 (the Pi Wi-Fi follows the
router) were merged on 16 October. The first release, v0.2.0, is being prepared. `pyproject.toml` already says `0.2.0`, no
version is reserved in `releases/registry.json`, and the open pieces are PR 139 (release tooling
fixes from a real dry run), 111 and 112 (update notes and the Updates page) and 117 (the build
lock). The owner approves the release itself.

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
