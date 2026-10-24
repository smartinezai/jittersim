# Dashboard Operate redesign handoff

This is an isolated draft aggregate. No main merge, deployment, release action,
owner signing key, real Pi/router contact, Claude, reset or paid API was used.

## Delivered scope

The desktop control column is replaced by full-width page links and a compact
profile/Start/Reset row. Panic and plain read-only router status remain in a
persistent safety bar. Controls opens secondary router and credential tools.
Compact widths/heights retain the phone sheet and locally scrolling page links.
Navigation hands focus to the selected page; resize and credential close preserve
a visible focus target. Opening Controls suppresses an overlapping toast.

The aggregate reuses reviewed compact buttons, router labels, version details,
profile reset/unassign, notifications and discovery collapse/New markers. Saved
observation results refresh automatically; scanning remains on demand. It adds no
framework or runtime dependency and preserves confirmations, explicit phone Allow
consent, protected/controlling-device semantics and the two permanent announcers.

## Identity and ownership

- Branch: codex/dashboard-operate-redesign, in task-12/redesign-worktree.
- Starting main base: `79ef2bac`; provisional worktree started at `e5513b47`.
- Reviewed inputs preserved as ancestors: private UI `63c1d84e` and queue `066d011b`.
- Frozen production source: df1816dd5926015a6f01c597dfe69515e21b25d2.
- Assembled page SHA-256: 7265984cf2176a5c11f86e7e1bd50fef64f604a40e84241d92d0429d51960ce1.
- [Source identity and measured records](source-identity.json). Later commits
  contain only tests, documentation, review evidence and the approved byte record.
- Main/release integration remains the coordinator's. After publication/freeze
  clearance, PR 209 remains separate, followed by one finished redesign aggregate;
  contained PRs 232/233/234/236 must not be merged again separately. Chart 415 / PR 237
  remains excluded from this frozen redesign. Held PR 175 / patch I and pinned helpers remain
  untouched. The owner changed the final allowance reserve from 10% to 8%.

The sole source writer was task-12. Independent UX/test prep, source review, fresh
visual finish review, mobile-only review and one bounded user-guide writer were
used. The guide writer checked 48 relative references, with none missing.

## Verification ledger

All runtime evidence is synthetic Chromium/local fixtures, not physical acceptance.
A mixed run is not called green: only its named PASS rows are reused.

 | Head | Evidence | Result | 
 | --- | --- | --- | 
 | df1816dd | operate-final-units.log, 1790 affected dashboard tests | 1788 passed; 2 static-budget assertions failed before the approved metadata correction | 
 | bb98e4d7 | operate-final-budget.log after byte record | 24 budget tests passed; independent measurement/strict-field review clear | 
 | df1816dd | local-ci-20261024-190737 | phone_layout, dashboard_access, observation_confirmation, saved_profiles PASS; historical navigation/status failures retained | 
 | bb98e4d7 | local-ci-20261024-191317 | sidebar_status and live_regions PASS; historical other fixture failures retained | 
 | 75a1de68 | local-ci-20261024-191827 | lint PASS | 
 | 75a1de68 | local-ci-20261024-191832 | wifi_recovery, apply_clarity, wifi_restart_reconnect PASS; final short-window navigation fixture failure retained | 
 | 67fac7c7 | local-ci-20261024-192222 | dashboard_navigation PASS after the one missing Controls-opening step | 
 | 2870fc2b | local-ci-20261024-192826 | complete help_icons PASS after asserting/dismissing only the expected sign-in success toast | 
 | bb98e4d7 | redesign-gui-worktree/local-ci-20261024-191545 | 37 GUI tests, 40 baseline and 40 axe states PASS; manifests clean, zero external/console/page/policy errors | 
 | bb98e4d7 | redesign-gui-worktree/local-ci-20261024-193045 | protected_dashboard_access, page_polling, update_status_recovery PASS | 
 | bb98e4d7 | independent shared-slot mobile diagnostic | 6 actual Reset-name/profile-delete dialogs PASS at 320 wide / 375 coarse / 390, no reset/delete POSTs | 

Local run folders are under test-results in the named worktree. All source rows
above share the frozen production source; test/metadata successors were not
represented as fresh full-native/full-browser runs. No full Python matrix, kernel
or release gate is claimed for this UI revision. Hosted CI is not claimed green.
The initial offline build-setting failure and all failed fixture receipts remain.

## Phone scope and limits

Original backlog §9 asked for persistent Panic/router access, 44px targets and
390×844,360×640,320×568 coverage. Those sizes pass normal and 112.5% wider-font
stress, as do short-window/landscape/coarse-pointer cases.375px page views and
axe checks are included in the GUI matrix. Page overflow is checked; horizontal
scrolling is confined to the compact page-link row. The independent extra review
verified actual confirmation-dialog buttons, cancellation, focus and long-dialog
internal scrolling; [full report and preserved failures](mobile-review.md).

Real phones, Safari/WebKit, virtual keyboards, dynamic browser chrome, assistive
technology, 200% OS text/zoom and physical test/management-network acceptance remain
unverified. The new dialog sample covers Reset Wi-Fi name and profile deletion,
not every dialog type. These limits do not constitute a physical acceptance pass.

## Screenshots

All images use synthetic data. [Provenance](screenshots/provenance.json) records
origins, source equivalence and hashes.

- [Desktop Devices](screenshots/desktop-devices.png)
- [Desktop Traffic](screenshots/desktop-traffic.png)
- [Phone Devices](screenshots/phone-devices.png)
- [320px large-font notification state](screenshots/phone-320-large-font.png)
- [Desktop utilities](screenshots/desktop-utilities.png)
- [375px phone delete dialog](mobile-evidence/dialog-delete-375.png)
- [320px long-dialog heading after internal scroll](mobile-evidence/dialog-reset-320-top.png)

[Independent reviews](review.md) returned ship/resolved at their stated scope.
[User guide](../user-guide.md) uses the preserved screenshots and current controls.
