# Documentation index

This page lists every file in `docs/` and the guides that live elsewhere in the
repository. It groups them by status so you can tell what to trust. Nothing was
moved or renamed to make this index. Checked against main at `add7c87` on
2026-10-09.

## What the status words mean

- **Current**: describes what the code does today or what is still open. It is
  kept up to date. When a current document and the source disagree, that is a
  bug in the document. The source wins.
- **Historical**: a dated record of a plan, a review or a test run. It was right
  for its date and is not updated. Do not follow it as instructions. Many
  records name branches, commits and checkouts that no longer exist.
- **Superseded**: replaced by a later document or by a code change. The table
  says what replaced it and where to see the evidence.

Plans and verification records use the date in their file name. A name with only
a month (for example `2026-09`) was written in that month.

## Current

### Guides and runbooks

| Document | Use it for |
| --- | --- |
| [README.md](../README.md) | What JitterSim does, the dashboard, the terminal commands, how to set up and how to run the checks |
| [deploy/raspberry-pi/README.md](../deploy/raspberry-pi/README.md) | Installing, updating, securing and recovering the Pi. It starts with a recovery quick reference |
| [pi-acceptance-checklist.md](pi-acceptance-checklist.md) | The owner's list of physical checks on a real Pi |
| [runbooks/dashboard-unreachable.md](runbooks/dashboard-unreachable.md) | The dashboard does not open. How to reach the Pi when only the test Wi-Fi is available |
| [runbooks/update-rollback-and-recovery-copy.md](runbooks/update-rollback-and-recovery-copy.md) | A failed update, the recovery copy and going back to the previous release |
| [runbooks/router-outage-pi-only-rollback.md](runbooks/router-outage-pi-only-rollback.md) | The router or the household Wi-Fi is down. Safe order of steps back from Pi-only Wi-Fi |
| [runbooks/key-rotation-and-trusted-keys.md](runbooks/key-rotation-and-trusted-keys.md) | Replacing, removing or repairing release signing keys on the Pi |
| [runbooks/restore-and-reimage.md](runbooks/restore-and-reimage.md) | Backing up, restoring the database and rebuilding the Pi on a new SD card |
| [architecture.md](architecture.md) | Which file owns which part of the dashboard source |
| [../DESIGN.md](../DESIGN.md) | The dark visual baseline of the dashboard |
| [releasing.md](releasing.md) | Cutting a release with `scripts/release.sh`, installing it and what the local verification record is |
| [releases/README.md](releases/README.md) | The rules and manual commands behind a release: reserve, build, approve and publish |
| [../CHANGELOG.md](../CHANGELOG.md) | The notes of every published release |
| [release-checklist-template.md](release-checklist-template.md) | The checklist to copy for each numbered release candidate |
| [measurements-2026-09.md](measurements-2026-09.md) | Measured effects of the profile presets (dated measurements, still cited by the README) |

### Backlog and status

| Document | Use it for |
| --- | --- |
| [planned-features.md](planned-features.md) | Requested work and its status |
| [planned-fixes.md](planned-fixes.md) | Known problems and what changed since the last tested candidate |
| [../ROADMAP.md](../ROADMAP.md) | Milestones and known limits |
| [feature-proposals-2026-10-09.md](feature-proposals-2026-10-09.md) | Thirty researched feature proposals with sizes and risks. They are proposals and not started work |

### Open investigations and reviews

| Document | Use it for |
| --- | --- |
| [outage-dossier-2026-10-04-2026-10-09.md](outage-dossier-2026-10-04-2026-10-09.md) | The three router failures during Pi-only Wi-Fi, ranked hypotheses and a read-only capture checklist |
| [router-incident-findings-2026-10-04.md](router-incident-findings-2026-10-04.md) | The Vodafone router outage of 4 October. The cause is unproven |
| [pi-only-datapath-audit-2026-10-09.md](pi-only-datapath-audit-2026-10-09.md) | Every way a device can end up on the Pi without working internet, and what was fixed |
| [update-path-audit-2026-10-09.md](update-path-audit-2026-10-09.md) | The update and release path mapped step by step |
| [research-router-pi-wifi-troubleshooting-2026-10-09.md](research-router-pi-wifi-troubleshooting-2026-10-09.md) | Ranked hypotheses and a safe experiment ladder for the outage |
| [open-source-gap-review-2026-10-09.md](open-source-gap-review-2026-10-09.md) | Which hand-built parts a free tool would do better |
| [open-source-adoption-2026-10-09.md](open-source-adoption-2026-10-09.md) | Free tools JitterSim could adopt (research) |

### Release records

| Document | Use it for |
| --- | --- |
| [releases/registry.json](releases/registry.json) | The live release registry (no version is reserved yet) |
| [releases/security-repair-plan-2026-10-03.md](releases/security-repair-plan-2026-10-03.md), [results](releases/security-repair-results-2026-10-03.md) and [verification](releases/verification-2026-10-03.md) | The 3 October release-system repairs and local verification. They are records of that work (historical) |

## Superseded

These stay in place so the reasoning is not lost. Do not use them as
instructions.

| Document | What it covered | Replaced by | Evidence |
| --- | --- | --- | --- |
| [implementation-plan-router-wifi-switch-2026-10-01.md](implementation-plan-router-wifi-switch-2026-10-01.md) | Router Wi-Fi switch | PR 87: router Wi-Fi writes are refused and the switch is read only | docs/planned-fixes.md; `src/jittersim/networking/helper.py` (`router-control-disabled`); Router Wi-Fi recovery safety in the Pi guide |
| [implementation-plan-router-toggle-2026-09.md](implementation-plan-router-toggle-2026-09.md) | Router Wi-Fi toggle repair | PR 87 | Same as above. The toggle no longer writes to the router |
| [implementation-plan-router-confirmation-repair-2026-10-01.md](implementation-plan-router-confirmation-repair-2026-10-01.md) | Router Wi-Fi confirmation repair | PR 87 | Same as above. It repaired the router-on action, which no longer exists |
| [implementation-plan-router-form-destination-2026-10-04.md](implementation-plan-router-form-destination-2026-10-04.md) | Older router form destination | PR 87 (in part) | It guarded router write forms. Every router write is now refused before contact |
| [implementation-plan-router-form-safety-2026-10-04.md](implementation-plan-router-form-safety-2026-10-04.md) | Older router Wi-Fi form safety | PR 87 (in part) | Same as above |
| [implementation-plan-wifi-security-recovery-2026-09.md](implementation-plan-wifi-security-recovery-2026-09.md) | Wi-Fi security and name recovery (WPA2, name `JitterSim`) | [WPA3 plan](implementation-plan-wpa3-2026-09.md) and [initial Wi-Fi setup](installer-wifi-password-plan.md) | `src/jittersim/networking/wifi.py` (`DEFAULT_WIFI_NAME = "TestNet"`, `configure(..., security="WPA3")`) |
| [wifi-security-recovery-validation-2026-09.md](wifi-security-recovery-validation-2026-09.md) | Validation of the WPA2 and `JitterSim` name recovery | [WPA3 plan](implementation-plan-wpa3-2026-09.md) and [initial Wi-Fi setup verification](installer-wifi-password-verification.md) | Same as above |
| [implementation-plan-floating-credentials-2026-09.md](implementation-plan-floating-credentials-2026-09.md) | Floating credential controls | [Dashboard redesign: sidebar navigation](implementation-plan-dashboard-redesign-2026-10-01.md) | The redesign plan removes the `credential-dock`. `src/jittersim/api/dashboard.html` has the sidebar credential buttons and no floating dock |
| [implementation-plan-wifi-recovery-panel-2026-09.md](implementation-plan-wifi-recovery-panel-2026-09.md) | Wi-Fi recovery in the floating quick-actions panel (superseded in part: the router security copy still applies) | [Dashboard redesign: sidebar navigation](implementation-plan-dashboard-redesign-2026-10-01.md) | **Reset Wi-Fi name** now sits on the Router and Wi-Fi page (`dashboard.html`) |
| [implementation-plan-mdns-dashboard-name-2026-09.md](implementation-plan-mdns-dashboard-name-2026-09.md) | Dashboard name `jittersim.local` | [Neutral sign-in page](implementation-plan-sign-in-2026-09.md) and the neutral name `netlab.local` | `src/jittersim/localname.py` (`DEFAULT_LOCAL_NAME = "netlab.local"`). Avahi publishing and the one-name rule still follow this plan |
| [implementation-plan-online-release-source-2026-10-03.md](implementation-plan-online-release-source-2026-10-03.md) | Online release source (design note) | [Releasing JitterSim](releases/README.md) | Its status line says `publish-release.sh` is not built. The script now exists in `deploy/raspberry-pi/` |

## Historical

Each plan below was written before a change and describes that change as
planned. The source and the current guides above describe the result. The plans
are grouped by theme.

### Router and Wi-Fi

| Plan | Date |
| --- | --- |
| [Combine the router Wi-Fi copy buttons](implementation-plan-combined-router-wifi-copy-2026-10-03.md) | 2026-10-03 |
| [Household access point readiness](implementation-plan-household-access-point-2026-09.md) | 2026-09 |
| [Follow manual router checks in Wi-Fi recovery acceptance](implementation-plan-manual-router-browser-2026-10-04.md) | 2026-10-04 |
| [Vodafone router outage investigation](implementation-plan-router-incident-2026-10-04.md) | 2026-10-04 |
| [Persistence mode for router outages](implementation-plan-router-outage-persistence-2026-09.md) | 2026-09 |
| [Router Wi-Fi import compatibility](implementation-plan-router-wifi-import-2026-09.md) | 2026-09 |
| [Router Wi-Fi recovery repair](implementation-plan-router-wifi-recovery-2026-10-04.md) | 2026-10-04 |
| [WPA3 hotspot upgrade](implementation-plan-wpa3-2026-09.md) | 2026-09 |

### Updates, releases and installation

| Plan | Date |
| --- | --- |
| [Align the complete browser journey with update recovery](implementation-plan-browser-update-contract-2026-10-03.md) | 2026-10-03 |
| [Preserve certificate identity through install and rollback](implementation-plan-ca-recovery-2026-10-03.md) | 2026-10-03 |
| [Check staged updates on dashboard open](implementation-plan-dashboard-auto-update-check-2026-09.md) | 2026-09 |
| [Deployment audit and upgrade repair](implementation-plan-deployment-audit-2026-09-30.md) | 2026-09-30 |
| [Legacy certificate upgrade](implementation-plan-legacy-certificate-upgrade-2026-10-04.md) | 2026-10-04 |
| [Finish update status and page refresh recovery](implementation-plan-online-polling-combined-2026-10-03.md) | 2026-10-03 |
| [Repair the final Linux failures](implementation-plan-release-regressions-2026-10-04.md) | 2026-10-04 |
| [Remaining release repairs](implementation-plan-remaining-release-repairs-2026-10-03.md) | 2026-10-03 |
| [Retry a retained failed update](implementation-plan-retained-update-retry-2026-10-03.md) | 2026-10-03 |
| [Keep signed release labels consistent](implementation-plan-signed-version-labels-2026-10-03.md) | 2026-10-03 |
| [Stable releases for JitterSim](implementation-plan-stable-releases-2026-10-03.md) | 2026-10-03 |
| [Repair the update path before the combined release](implementation-plan-update-path-repairs-2026-10-03.md) | 2026-10-03 |
| [Prepare the update before stopping services](implementation-plan-update-preparation-2026-10-03.md) | 2026-10-03 |

### Access rules, DNS, names and protected devices

| Plan | Date |
| --- | --- |
| [Device access, blocked sites and field help](implementation-plan-access-controls-and-help-2026-09.md) | 2026-09 |
| [Compose protected-device ads and connection patterns](implementation-plan-adblocking-pattern-composition-2026-10-04.md) | 2026-10-04 |
| [Confirm observed-site profile restrictions](implementation-plan-allowlist-confirmation-2026-10-03.md) | 2026-10-03 |
| [Saved dashboard access from the Pi Wi-Fi](implementation-plan-dashboard-from-pi-wifi-2026-09.md) | 2026-09 |
| [Device exemptions and connected-client controls](implementation-plan-device-management-2026-09.md) | 2026-09 |
| [Verify the connection-error feature before homepage bounce](implementation-plan-dns-error-acceptance-2026-10-02.md) | 2026-10-02 |
| [DNS failures and headless operation](implementation-plan-dns-failure-and-headless-operations-2026-09.md) | 2026-09 |
| [A profile for everyone else](implementation-plan-everyone-else-2026-09.md) | 2026-09 |
| [Reliable local transport for the extension browser journey](implementation-plan-extension-loopback-transport-2026-10-03.md) | 2026-10-03 |
| [Homepage bounce and connection-error acceptance](implementation-plan-homepage-bounce-2026-10-02.md) | 2026-10-02 |
| [Publish the reviewed homepage reference and DNS acceptance](implementation-plan-homepage-delivery-2026-10-02.md) | 2026-10-02 |
| [Live homepage pairing implementation](implementation-plan-live-homepage-pairing-2026-10-03.md) | 2026-10-03 |
| [Preserve omitted access-rule sections](implementation-plan-partial-access-rules-2026-10-02.md) | 2026-10-02 |
| [Optional ad blocking for protected devices](implementation-plan-protected-device-ad-blocking-2026-10-03.md) | 2026-10-03 |
| [A neutral sign-in page](implementation-plan-sign-in-2026-09.md) | 2026-09 |

### Profiles, patterns and the engine

| Plan | Date |
| --- | --- |
| [Repair dashboard device-verification contention](implementation-plan-dashboard-verification-contention-2026-10-04.md) | 2026-10-04 |
| [Erratic connections and application interruption plan](implementation-plan-erratic-connections-2026-10-03.md) | 2026-10-03 |
| [Apply a saved profile to the whole test network](implementation-plan-general-profile-2026-10-01.md) | 2026-10-01 |
| [Restore deliberate general-profile starts after Panic](implementation-plan-general-profile-resume-2026-10-03.md) | 2026-10-03 |
| [Safer recovery and visibility](implementation-plan-hardening-2026-09.md) | 2026-09 |
| [Release the helper lock before replying](implementation-plan-helper-response-lock-2026-10-03.md) | 2026-10-03 |
| [Lab usability, safety and realism](implementation-plan-lab-improvements-2026-09.md) | 2026-09 |
| [Preserve evidence for JitterSim outages](implementation-plan-outage-logging-2026-10-04.md) | 2026-10-04 |
| [Keep service lists in general profile compilation](implementation-plan-pr50-general-integration-2026-10-03.md) | 2026-10-03 |
| [Queue statistics and household access repairs](implementation-plan-pr72-network-regressions-2026-10-03.md) | 2026-10-03 |
| [Profile update repair](implementation-plan-profile-update-failure-2026-10-04.md) | 2026-10-04 |
| [Bound unfinished browser DNS lookups](implementation-plan-proxy-dns-bounds-2026-10-03.md) | 2026-10-03 |
| [Random value ranges and accurate previews](implementation-plan-random-value-ranges-2026-10-03.md) | 2026-10-03 |
| [Randomized website analytics](implementation-plan-random-website-analytics-2026-09.md) | 2026-09 |
| [Handle expired dashboard requests consistently](implementation-plan-request-timeout-compatibility-2026-10-03.md) | 2026-10-03 |
| [Keep website lookups within one process limit](implementation-plan-resolver-limit-2026-10-03.md) | 2026-10-03 |
| [Runtime review fixes: failed Apply, activity log and scenario expiry](implementation-plan-runtime-review-fixes-2026-09.md) | 2026-09 |
| [Random variation and random outages for service lists](implementation-plan-service-list-variation-2026-10-02.md) | 2026-10-02 |
| [Service profiles, Pi readiness and remote control](implementation-plan-service-profiles-and-pi-readiness-2026-09.md) | 2026-09 |

### Dashboard screens and devices

| Plan | Date |
| --- | --- |
| [Connected device details](implementation-plan-connected-device-details-2026-09.md) | 2026-09 |
| [Clear dashboard action feedback](implementation-plan-dashboard-feedback-2026-09.md) | 2026-09 |
| [Dashboard follow-ups: stable device identity and visible network state](implementation-plan-dashboard-followups-2026-09.md) | 2026-09 |
| [Clear private results and repair phone controls](implementation-plan-dashboard-private-and-phone-repairs-2026-10-03.md) | 2026-10-03 |
| [Complete the dashboard quick wins](implementation-plan-dashboard-quick-wins-repair-2026-10-03.md) | 2026-10-03 |
| [Dashboard redesign: sidebar navigation](implementation-plan-dashboard-redesign-2026-10-01.md) | 2026-10-01 |
| [Device address review repairs](implementation-plan-device-address-review-repairs-2026-10-03.md) | 2026-10-03 |
| [Choose a saved profile when assigning a device](implementation-plan-device-profile-picker-2026-10-04.md) | 2026-10-04 |
| [Device names, traffic clarity, profile list, sidebar status and impact check](implementation-plan-device-visibility-2026-10-01.md) | 2026-10-01 |
| [Device workflows and repeatable tests](implementation-plan-device-workflows-2026-09.md) | 2026-09 |
| [Make raw diagnostics reachable from the keyboard](implementation-plan-diagnostics-keyboard-access-2026-10-03.md) | 2026-10-03 |
| [Diagnostics keyboard request check](implementation-plan-diagnostics-preview-check-2026-10-04.md) | 2026-10-04 |
| [Finish the coordinated dashboard changes](implementation-plan-final-dashboard-composition-2026-10-04.md) | 2026-10-04 |
| [Make device groups understandable](implementation-plan-group-device-controls-2026-10-04.md) | 2026-10-04 |
| [Dashboard GUI baseline implementation plan](implementation-plan-gui-baseline-2026-10-03.md) | 2026-10-03 |
| [Live traffic graph](implementation-plan-live-traffic-chart-2026-09.md) | 2026-09 |
| [Reset network tooltip positioning](implementation-plan-reset-tooltip-2026-10-04.md) | 2026-10-04 |
| [Preserve service-list focus during status refresh](implementation-plan-service-list-refresh-focus-2026-10-03.md) | 2026-10-03 |
| [Skipped saved-device status repair](implementation-plan-skipped-device-status-2026-10-03.md) | 2026-10-03 |
| [Clearer traffic lines and inspection](implementation-plan-traffic-chart-interaction-2026-10-03.md) | 2026-10-03 |
| [Live traffic speed reference lines](implementation-plan-traffic-rate-reference-lines-2026-09.md) | 2026-09 |
| [Find unfinished forms](implementation-plan-unfinished-form-navigation-2026-10-03.md) | 2026-10-03 |

### Tests, CI and project upkeep

| Plan | Date |
| --- | --- |
| [Use seconds for the polling test clock](implementation-plan-browser-clock-units-2026-10-03.md) | 2026-10-03 |
| [Correct remaining browser recovery fixtures](implementation-plan-browser-recovery-contract-2026-10-03.md) | 2026-10-03 |
| [Wait for private diagnostics without string evaluation](implementation-plan-dashboard-csp-test-wait-2026-10-03.md) | 2026-10-03 |
| [Reconcile the earlier backlog and interface proposals](implementation-plan-documentation-reconciliation-2026-10-03.md) | 2026-10-03 |
| [JitterSim full verification](implementation-plan-full-verification-2026-10-01.md) | 2026-10-01 |
| [Correct the final kernel acceptance contracts](implementation-plan-kernel-contract-checks-2026-10-04.md) | 2026-10-04 |
| [Allow the complete Linux release checks to finish](implementation-plan-linux-release-deadline-2026-10-03.md) | 2026-10-03 |
| [JitterSim maintenance and feature sequence](implementation-plan-maintainability-2026-10-03.md) | 2026-10-03 |
| [Verify refusal without a polling timing assumption](implementation-plan-refused-client-test-budget-2026-10-03.md) | 2026-10-03 |
| [SSH key rejection test](implementation-plan-ssh-key-rejection-test-2026-10-04.md) | 2026-10-04 |
| [Repair the two Stack A review blockers](implementation-plan-stack-a-review-repairs-2026-10-03.md) | 2026-10-03 |

## Historical verification, review and handoff records

These describe one test run, review or handoff. Their branches, checkouts and
commit hashes are those of their day.

| Document | Date |
| --- | --- |
| [Device management checks](device-management-validation-2026-09.md) | 2026-09 |
| [JitterSim verification on 1 October 2026](verification-results-2026-10-01.md) | 2026-10-01 |
| [Coordinated JitterSim verification on 3 October 2026](verification-coordinated-release-2026-10-03.md) | 2026-10-03 |
| [Coordinated candidate before full release verification](verification-coordinated-candidate-2026-10-04.md) | 2026-10-04 |
| [Final local release verification](verification-final-local-release-2026-10-04.md) (accepted main `5e0fed2`) | 2026-10-04 |
| [Dashboard quick wins verification](verification-dashboard-quick-wins-2026-10-03.md) | 2026-10-03 |
| [Device address repair verification](verification-device-address-repair-2026-10-03.md) | 2026-10-03 |
| [Dashboard GUI baseline verification](verification-gui-baseline-2026-10-03.md) | 2026-10-03 |
| [Homepage private code lifetime correction](verification-homepage-private-ui-2026-10-03.md) | 2026-10-03 |
| [Live homepage verification](verification-live-homepage-2026-10-03.md) | 2026-10-03 |
| [Observed-site confirmation verification](verification-allowlist-confirmation-2026-10-03.md) | 2026-10-03 |
| [Update status and polling recovery verification](verification-online-polling-recovery-2026-10-03.md) | 2026-10-03 |
| [Retained update retry verification](verification-retained-update-retry-2026-10-03.md) | 2026-10-03 |
| [Update preparation and recovery checks](update-preparation-verification-2026-10-03.md) | 2026-10-03 |
| [Expired dashboard request compatibility](verification-request-timeout-compatibility-2026-10-04.md) | 2026-10-04 |
| [Reset network tooltip verification](verification-reset-tooltip-2026-10-04.md) | 2026-10-04 |
| [Service-list refresh focus verification](verification-service-list-refresh-focus-2026-10-03.md) | 2026-10-03 |
| [Skipped saved-device status verification](verification-skipped-device-status-2026-10-03.md) | 2026-10-03 |
| [Device profile dropdown verification](device-profile-picker-verification-2026-10-04.md) | 2026-10-04 |
| [Dashboard concurrency verification](pr70-concurrency-verification-2026-10-03.md) | 2026-10-03 |
| [Initial Wi-Fi setup verification](installer-wifi-password-verification.md) | 2026-10-03 |
| [Wi-Fi password repair verification](wifi-password-security-verification.md) | 2026-10-03 |
| [Wi-Fi reconnect verification](wifi-reconnect-verification.md) | 2026-10-03 |
| [Remaining foundation review](foundation-review-2026-10-03.md) | 2026-10-03 |
| [Live homepage pairing plan review](review-live-homepage-pairing-2026-10-03.md) | 2026-10-03 |
| [Live homepage release handoff](live-homepage-release-handoff-2026-10-03.md) | 2026-10-03 |
| [Device group controls handoff](group-device-controls-handoff-2026-10-04.md) | 2026-10-04 |
| [Current Pi instructions and acceptance limits](documentation-reconciliation-2026-10-03.md) (the 3 October reconciliation record) | 2026-10-03 |

## Historical plans with older file names

| Document | Date |
| --- | --- |
| [Dashboard concurrency audit and repair plan](pr70-source-audit-plan-2026-10-03.md) | 2026-10-03 |
| [Apply a profile to its selected device: design addendum](device-apply-start-design-2026-10-09.md) | 2026-10-09 |
| [Save the initial Wi-Fi password privately](installer-wifi-password-plan.md) | 2026-10-03 |
| [Keep Wi-Fi passwords out of process arguments](wifi-password-security-plan.md) | 2026-10-03 |
| [Wi-Fi reconnect repair plan](wifi-reconnect-repair-plan.md) | 2026-10 |

## Historical machine-readable evidence

These files record command receipts, source hashes and review results. They were
left exactly as written, which includes the paths and tool locations of the
machine that produced them.

| File | Records |
| --- | --- |
| [test-results/full-verification-2026-10-01.json](test-results/full-verification-2026-10-01.json) | The 1 October full verification run |
| [verification-final-local-release-2026-10-04.json](verification-final-local-release-2026-10-04.json) | Receipts for the final local release verification |
| [verification-results-coordinated-release-2026-10-03.json](verification-results-coordinated-release-2026-10-03.json) | Results of the coordinated release verification |
| [verification-results-dashboard-quick-wins-2026-10-03.json](verification-results-dashboard-quick-wins-2026-10-03.json) | Results of the dashboard quick wins checks |
| [verification-results-homepage-delivery-2026-10-02.json](verification-results-homepage-delivery-2026-10-02.json) | Results of the homepage delivery checks |
| [verification-results-homepage-private-ui-2026-10-03.json](verification-results-homepage-private-ui-2026-10-03.json) | Results of the homepage private code checks |
| [verification-results-homepage-reference-review-2026-10-02.json](verification-results-homepage-reference-review-2026-10-02.json) | Results of the homepage reference review |
| [verification-results-live-homepage-2026-10-03.json](verification-results-live-homepage-2026-10-03.json) | Results of the live homepage checks |
| [verification-results-resolver-repair-2026-10-03.json](verification-results-resolver-repair-2026-10-03.json) | Results of the resolver repair checks |
| [verification-diagnostics-preview-check-2026-10-04.json](verification-diagnostics-preview-check-2026-10-04.json) | The diagnostics keyboard preview check |
| [verification-manual-router-browser-2026-10-04.json](verification-manual-router-browser-2026-10-04.json) | The browser run for manual router checks |
| [verification-outage-logging-2026-10-04.json](verification-outage-logging-2026-10-04.json) | The outage logging checks |
| [verification-release-regressions-2026-10-04.json](verification-release-regressions-2026-10-04.json) | The final Linux regression repair run |
| [verification-router-wifi-recovery-2026-10-04.json](verification-router-wifi-recovery-2026-10-04.json) | The router Wi-Fi recovery repair checks |
| [homepage-private-ui-before-2026-10-03.json](homepage-private-ui-before-2026-10-03.json), [after](homepage-private-ui-after-2026-10-03.json) and [installed](homepage-private-ui-installed-2026-10-03.json) | Homepage private code state before the repair, after it and on the installed build |
| [homepage-chrome-trust-failure-2026-10-03.txt](homepage-chrome-trust-failure-2026-10-03.txt) | A recorded Chrome trust failure |
| [review-results-hardening-2026-10-03.json](review-results-hardening-2026-10-03.json) | Independent review of the hardening batch |
| [review-results-adblocking-pattern-consumers-2026-10-04.json](review-results-adblocking-pattern-consumers-2026-10-04.json) | Independent review of the ad blocking and pattern consumers |
| [review-results-final-panic-consumers-2026-10-04.json](review-results-final-panic-consumers-2026-10-04.json) | Independent review of the Panic consumers |
| [review-results-router-recovery-consumers-2026-10-05.json](review-results-router-recovery-consumers-2026-10-05.json) | Independent review of the router recovery consumers |
| [review-results-router-recovery-optin-removal-2026-10-05.json](review-results-router-recovery-optin-removal-2026-10-05.json) | Independent review of the router recovery opt-in removal |
| [review-results-panic-consumers-outage-logging-2026-10-09.json](review-results-panic-consumers-outage-logging-2026-10-09.json) | Independent review of the Panic consumers for outage logging |
| [router-incident-receipts-2026-10-04.json](router-incident-receipts-2026-10-04.json) | Receipts for the router incident findings |

## Historical archive

[history/2026-10-02-backlog-and-ux/](history/2026-10-02-backlog-and-ux/README.md)
keeps the exact documents of PR 54 and PR 75: the 2 October backlog
([planned features](history/2026-10-02-backlog-and-ux/planned-features.md),
[the version before reconciliation](history/2026-10-02-backlog-and-ux/planned-features-accepted-main-before-reconciliation.md)
and [planned fixes](history/2026-10-02-backlog-and-ux/planned-fixes.md)), the
[dashboard UX audit](history/2026-10-02-backlog-and-ux/ux-audit-2026-10-02.md),
the [overhaul plan](history/2026-10-02-backlog-and-ux/ux-overhaul-plan-2026-10-02.md),
the [interface prototype](history/2026-10-02-backlog-and-ux/ux-prototype-2026-10-02.html)
and a [handoff](history/2026-10-02-backlog-and-ux/handoff-chatgpt-2026-10-02.md).
The three backlog files are superseded by [planned-features.md](planned-features.md)
and [planned-fixes.md](planned-fixes.md). The README of the archive says so.
[IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) in the repository root is
the first plan (September 2026) and is historical too.

## Screenshots

The README uses these five images. They show sample data. The two overview
images were retaken on 2026-10-09 with the refreshed look (see
[ui-overhaul-proposal-2026-10-09.md](ui-overhaul-proposal-2026-10-09.md)). The
other three show the earlier look. The
[staged update screenshot](screenshots/staged-update-check.png) also predates the
current **Updates** page wording (**Install update**).

- [experience-overview-desktop.png](screenshots/experience-overview-desktop.png)
- [experience-overview-phone.png](screenshots/experience-overview-phone.png)
- [dns-failure-controls.png](screenshots/dns-failure-controls.png)
- [router-outage-persistence.png](screenshots/router-outage-persistence.png)
- [staged-update-check.png](screenshots/staged-update-check.png)

Icons in the dashboard come from Lucide. Its licence is in [third-party-notices.md](third-party-notices.md).
