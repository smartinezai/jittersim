# JitterSim backlog audit, 2026-10-16

Read-only audit. Baseline: `origin/main` at `48c948d` (Merge PR 115), fetched 2026-10-16 20:10 Berlin.
Method: `git archive origin/main` extracted to a scratch folder and grepped; `gh pr list/view`; plan texts read from
the owner's main checkout (not modified). Nothing was run or tested: statuses
come from reading source, PR bodies and docs. Physical Pi checks cannot be judged from the repository.

## Status on 18 October 2026 (main `8c3f8171`)

This audit is a dated record. The rows and line numbers below are those of `48c948d`. What has moved:

| Item | State now |
| --- | --- |
| Top item 1: single-flight dashboard identity checks | Not started. No branch or pull request |
| Top item 2: traffic chart readable at low rates | Not started. Needs the owner's answer on plan decision D4 |
| Top item 3: one announcer and quiet polled lists | Done: PR 146 (focus fixes in PR 152) |
| Top item 4: accessible confirm dialogs | Done: PR 141 (`window.confirm` count is 0) |
| Top item 5: reconcile ROADMAP and planned lists | Done: PR 144, and refreshed again on 18 October |
| Top item 6: PR template and architecture test | Done: PR 145 (`tests/unit/test_architecture_rules.py`) |
| Plan 3: device apply-start with Everyone running | PR 149 (draft) builds it |
| Plan 9: Pi Wi-Fi follows the router | Merged: PR 140 |
| Plan 12: protected-device dashboard access | Merged: PR 92, landed with PR 136 |
| Plan 15: stable releases | PR 139, 112 and 117 merged. PR 111 is open. The first release is not cut |
| Plan 16: switching-test-failures | Listed as superseded in the docs index (PR 144) |
| Plan 21: device speed test | PR 121 is open. PR 153 (draft) is stacked on it |
| Pi reliability finding 1 (Panic and synchronous writes) | Done: PR 138 |
| Pinned-file work: independent expiry after a dashboard crash | Not started |
| Candidate 12: QA leftovers | PR 126 fixed most. Control characters in names and HTTP/0.9 remain |

The open pull requests of the excluded list on 16 October (92, 103, 104, 112, 136, 138, 139)
have all merged. The ones open now are 111, 121, 149, 150, 151 and 153.

Excluded as already covered:
- Open PRs: 92 (protected-device dashboard access), 103, 104, 111, 112, 117, 121 (device speed test), 136, 138, 139.
- In-flight branches: fix/stability-2026-10-16 (local-ci.sh Python 3.9, fixture status poll), fix/carry-forward-2026-10-16
  (PR 138: Panic waits, intake cleanup, dashboard grant), fix/pi-boot-robustness-2026-10-16 (mDNS/Avahi, journald boot noise),
  fix/wifi-follows-router-2026-10-16 (wifi_follow.py, applier.py, wifi.py, jittersim-ap.service), fix/release-dryrun-2026-10-16
  (PR 139), feat/device-speed-test (PR 121).

Hash-pinned set (`PANIC_SOURCE_FILES` in `updates.py:970`): `src/jittersim/panic_guard.py`,
`src/jittersim/networking/helper.py`, `src/jittersim/networking/access.py`. Any change to one needs a new approved bundle
in `PANIC_APPROVED_SOURCES` and an independent Panic-consumer review.

Note on the working checkout: 12 plan docs there differ from the main versions (the checkout is on an older
`codex/master-continuation` state). Main is treated as truth. Seven plans exist only in that checkout (untracked): device-apply-start,
gui-improvements, pi-only-accesspoint-rollout, protected-device-dashboard-access (also on PR 136 branch), switching-test-failures,
unaffected-devices, website-modes. `docs/README.md` on main already classes the other ~77 plan docs on main as Historical or
Superseded (PR 109), and a scan of their Status lines found no other open plan.

---

## 1. Plan-by-plan status

Size: S under 1 day, M about a week, L more. Value: H = stability or existing error, M = user-visible bug or gap, L = feature or housekeeping.

| # | Plan | Status on main | Evidence | Remaining (1-3 lines) | Pinned files | Size | Value |
|---|---|---|---|---|---|---|---|
| 1 | combined-router-wifi-copy | DONE | PR 76. `sync-router-ap` in dashboard.html/js sidebar. Clipboard buttons gone. | Nothing in code. Physical copy acceptance is an owner check. | no | - | - |
| 2 | dashboard-verification-contention | PARTLY | PR 86 (0.5 s bounded wait, at most 3 waiters, token kept on `helper-busy`; helper.py:81,477) and PR 91 (bound token checks). dashboard.js:5710-5717 shows "Choose Check token to try again". | Every private API call from a test-network peer still does its own helper `dashboard-access status` (`server.py` `_check_device_access` ~5909). No shared in-flight work. Sign-in retry is manual. The 4-simultaneous probe was never repeated on the installed Pi. | helper-side caching: YES (helper.py, access.py). Server-side single-flight: no | M | H |
| 3 | device-apply-start | PARTLY | PR 96 (apply-and-start, `device_start_scope` table) and PR 127 (7 review defects). docs/device-apply-start-design-2026-10-09.md. | The owner-approved case "Everyone is running: the device gets its own profile, others unchanged" is not built. It is refused with 409. Needs the general-profile renderer to exclude the device address from the shared mark in both directions plus Linux packet checks. | no (renderer.py, compiler, applier; confirm no helper change) | M-L | M |
| 4 | device-profile-picker | DONE | PR 84. `select#device-profile` (dashboard.html:692) with load, empty and failure states. | Nothing. | no | - | - |
| 5 | erratic-connections | Delivery A DONE, B-F NOT STARTED | PR 84 (independent outage choices, service-list behaviour, live queue update, session patterns). ROADMAP M15 boxes. Only the older burst-loss `gemodel` exists (renderer.py:129-143). | M15 verification gate (limits, counters, reset, restart, rollback, independent expiry after a dashboard crash). Deliveries B (long-tail delay, capability checks), C (stages, pause-then-release), D (connection faults, presets), E (nearly finished downloads), F (usage worsening). | Expiry gate: YES (helper). B: probably no. C, D, F: likely yes (helper or proxy) | L each | L |
| 6 | group-device-controls | DONE | "Clarify device group controls" 83fd99c in main. "Profile for this group", checkbox list (dashboard.html:713-722). tests/browser/group_devices.py. | Nothing. | no | - | - |
| 7 | gui-improvements | PARTLY | Done: Phase 0 baseline (PR 79, 124 static ratchet), tokens and refresh look (PR 115), skip link, unique landmarks, table header (PR 88), chart legend and hover (PR 77), gzip with ETag (server.py:331), request timeout with plain lost-connection text (dashboard.js:290-340, reconnect.js), axe report in CI (tests.yml:92). | Open slices: GUI-2.1 per-page title and h1; 2.2-2.4 one announcer (32 `aria-live` in markup, 6 flip sites, no `role="alert"`); 2.5 form errors; 3.1 11 native `window.confirm` (gui-stats.json); 3.5-3.7; 4.2 data-scaled y-axis, 4.4 text alternative, 4.5 pause; 5.3 `<dialog>` controls sheet; 7.3-7.7 (vnu, Biome, WebKit, PR template); 8.x skill. Owner decisions D4 (chart scale) and others still unrecorded. | no | L total, slices S-M | M |
| 8 | maintainability | PARTLY | Ticket 1 done: dashboard.html/css/js split, `api/dashboard.py` assembly, docs/architecture.md, server.py 17,318 to 7,046 lines. | Tickets 2-7 (profile request handling, rule explanation, experiments, browser features, storage, release/runtime) not started. The "small automated check for forbidden imports and missing packaged resources" was never added. server.py is hot (PR 92, 121, 136, 138). | tickets 2-6 no | M each (guard test S) | L |
| 9 | pi-only-accesspoint-rollout | PARTLY, paused | Installer repaired, Air cutover accepted 4 Oct, then outage events B and C (docs/outage-dossier-2026-10-04-2026-10-09.md). Follow-ups on main: PR 87 (router writes refused), 105 (safe DHCP hand-over), 106, 120 (outage lab), 125 and 135 (upstream-safe impairments), 133 (router canary). fix/wifi-follows-router in flight. | Owner-run gate: read-only capture ladder, long Pi-only run with impairments off, then delay and jitter only. Optional "confirm or revert for Pi Wi-Fi changes" (feature proposal 8). Not agent-actionable now. | confirm-or-revert: YES (helper) | owner-run | H (not now) |
| 10 | profile-update-failure | DONE in code | Cause fixed by 46931bc "Keep profile checks available during network reads" in PR 84 (PR 82 closed in favour). Duplicating-netem ordering handled in renderer.py (`KIND_ORDER`, line 70). | Repeat the original profile edit once on the installed Pi (owner). | already in helper, nothing left | - | - |
| 11 | protected-device-ad-blocking | DONE in code | 165f2c5 and 95186a5 via PR 84. `config/adblocking.py`, `networking/adblocking.py`, jittersim-adblocking.service, "Block ads" (dashboard.html:297), blocklist update path. | Physical acceptance: three-client check, kill-the-blocker recovery, Pi delay and memory. | no | - | - |
| 12 | protected-device-dashboard-access | IN FLIGHT | PR 92, carried by 136 and 138 | Excluded. | yes | - | - |
| 13 | reset-tooltip | DONE | 717b452 via PR 84. `setupSidebarActionHelp` (dashboard.js:7), `.sidebar-action-help` CSS. | Nothing. | no | - | - |
| 14 | router-wifi-recovery | DONE | PR 87 (helper refuses router Wi-Fi writes, switch read-only), 91 (outage logging), 106 (password-check cooldown), 133 (canary), 90 (tests). | Outage cause stays unproven (owner evidence). | already done | - | - |
| 15 | stable-releases | Tooling DONE, first release NOT CUT | PR 108 (release.sh), 109, 110, 114, 116, 122. docs/releasing.md, release-candidate.yml, release registry. `gh release list` and tags are empty. | Owner approval and first `v0.2.0`. Supported Pi/OS matrix and hardware upgrade rehearsal. PR 139, 111, 112, 117 are the open pieces. | no | owner-run | in flight |
| 16 | switching-test-failures | OBSOLETE | The live router-switching test no longer exists: router Wi-Fi writes are refused since PR 87. | Mark Superseded in docs/README (add to the Superseded table). | no | S (docs) | L |
| 17 | traffic-chart-interaction | DONE | PR 77 (99364e2), `pointermove` handling in dashboard.js, tests/browser/traffic_chart.py. | The y-axis scale part lives in gui-improvements 4.2. | no | - | - |
| 18 | unaffected-devices | DONE | PR 7, 31, 32, 55. `config/exemptions.py`, "Unaffected by profiles" list. | Nothing. | no | - | - |
| 19 | unfinished-form-navigation | DONE | PR 84 (PR 83 closed). workflows.js "Review ..." buttons, tests/browser/draft_navigation.py. | Nothing. | no | - | - |
| 20 | website-modes | NOT STARTED | `website_mode` only accepts `unrestricted` and `allow_only` (config/models.py:296). | Exempt, block and multiply-delay modes, rule priority, outage behaviour, rollback. | plan says helper changes: probably YES | L | L |
| 21 | device-speed-test | IN FLIGHT | PR 121 (plan only on the branch) | Excluded. | no | - | - |

## 2. ROADMAP.md (main) audit

Open boxes by kind:
- Stale, already done in code: Milestone 2 "ifb plan" (`UPLOAD_DEVICE = "jittersim-ifb"`, renderer.py:39, PR 125). Milestone 13 "Find a trusted online source" (signed online check exists, `/api/updates/check-online`) and "Explore a separate browser feature for returning to a home page" (`extensions/homepage-bounce`). Milestone 15 "Complete independent plan review" and the paragraph "undergoing independent source review" (lines 350-352).
- Owner physical acceptance (not agent work): M9 quota on real traffic, M10 isolation on actual Pi, M11 four boxes (WPA verification, coverage and client count, repeated profile changes and reboot, household network design), M12 radio and release recovery, M13 error behaviour, M14 ICMP and router restart, M15 limits/counters/rollback, router security physical acceptance.
- Open decision: M14 "stop profiles from resuming after a Pi restart".
- Not started features: M16 (clustered loss and long delays beyond gemodel, stages, pause-release, presets), M17, M18, M19 (Deliveries B-F, plus website modes in planned-features).
- `docs/planned-features.md` and `docs/planned-fixes.md` still say "current main is 5e0fed2" and do not list PRs 89-139.

## 3. docs/pi-reliability-followup-2026-10-05.md

| Finding | Status |
|---|---|
| 1 Panic depends on synchronous writes | IN FLIGHT: PR 138 (Remove Panic's rules even when stop records stall, bound dashboard Panic's wait, test_panic_guard_write_wait). Excluded. Pinned. |
| 2 Worker saturation (16+2 dashboard workers, 8 helper connections) | DONE: PR 95 and 123, tests test_homepage_worker_slots, test_panic_saturation_recovery, test_helper_total_deadlines. |
| 3 Optional DNS and proxy services | DONE as tests (test_helper_access: failing DNS service fails the apply, Panic still works after DNS failure; test_access). The "minimal operating mode" is an idea only. |
| 4 Hardware replacement unsupported | Open, owner-run: loaded capacity never measured. Proposal 20 (capacity self-test) needs helper. |

## 4. docs/router-outage-followup-2026-10-05.md

| Corrective step | Status |
|---|---|
| 1 Keep router-write refusal and legacy-form guard | DONE: PR 87. |
| 2 Shared limit for manual password checks | DONE: PR 106 (router-check cooldown). |
| 3 Bounded privacy-safe router operation records | DONE: PR 91 (persistent outage logging) and PR 133 (read-only canary). |
| 4 Compare router event times with Pi state | Owner-run: capture checklist in docs/outage-dossier. |

---

## 5. Top 6 independent work items

Ranking logic: stability first, then existing user-visible defects, then guardrails. None edits `panic_guard.py`, `networking/helper.py`
or `networking/access.py`. Shared files are named so the parent can order them. `dashboard.js` is one 9,654-line file: items 2, 3 and 4 touch
different regions (chart ~1309-1560, announcer ~795-1867, confirms at the line numbers below) but all three move `gui-stats.json`
numbers. Resolve that conflict with `python -m tests.browser.gui_stats --update` (keeps one number per line). Hosted CI is billing-blocked, so
each PR needs `scripts/local-ci.sh` evidence on its head commit.

### 1. Single-flight dashboard identity checks (stability, S-M)
Value H: the owner's recurring "Device verification is busy" failure is the open acceptance gate for Pi-only Wi-Fi.
Branch `fix/dashboard-access-single-flight`. Start after PR 138 merges (it rewrites `ControlServer.dashboard_access`, +61 lines at about line 1403 on that
branch versus 1358 on main, and the `_check_device_access` caller area).
- Brief: `_check_device_access` (server.py ~5909 on main) calls `dashboard_access(peer)` once per private request, and the helper answers
  `helper-busy` when its access lock is held (helper.py:477-484, PR 86 only softened it). Add `src/jittersim/api/access_flight.py` and use it
  in `ControlServer.dashboard_access` for `operation == "status"` and non-loopback peers only.
- Concurrent callers for the same peer string share one helper call (Event plus result or exception slot). The entry is removed the
  moment the call returns: no TTL and no reuse of a finished answer.
- Safety: a generation counter is bumped before and after every non-status `dashboard_access` call (enroll, remove, move). A request that
  starts after a write began never joins a flight that started earlier. Any exception goes to every waiter unchanged, so the code stays
  fail-closed and `helper-busy` keeps its code.
- Tests (new `tests/unit/test_dashboard_access_single_flight.py`): 8 threads on one peer make 1 helper call; different peers make separate
  calls; a write during a flight forces a new call; removal is visible on the next request; loopback bypass unchanged; PR 86 behaviour
  unchanged; replay the "4 simultaneous checks, 3 busy" probe against a fake helper that holds the lock.
- Files: `src/jittersim/api/server.py`, new `access_flight.py`, new test. Do not touch the three pinned files. Needs an independent security
  review (authorization path). Suggested depth: Sonnet 5.5 high plus an independent review. Optional S follow-up in dashboard.js ~5710: one
  bounded automatic retry when the token check answers helper-busy.

### 2. Traffic chart readable at low rates (user-visible defect, M)
Value M: with a 100 Mbit/s ISP line and a 2 Mbit/s profile the plot is about 2 percent full, and the chart is the main view for testing slow profiles.
Branch `feat/traffic-chart-data-scale`. Implementation can start now. Merging needs the owner's OK of plan decision D4 (option b, recommended).
- Brief: in `drawTrafficChart` (dashboard.js:1479-1560) replace `scaleMax = 10^ceil(log10(max(data and limits)))` (line 1499) with a 1-2-5 step
  scale from the data peak plus headroom. Draw a limit line only when it fits. Otherwise draw a labelled marker at the top edge ("ISP limit
  100 Mbit/s"). Keep the PR 77 hover, pin and legend behaviour and add no new hex colours (gui-stats ratchet).
- Add a visible latest-rates line and a collapsed `<details>` table of the last 30 samples, refreshed at most every 2 s with no `aria-live`.
  Add Pause and Resume with `aria-pressed` that stops `/api/traffic` sampling while paused.
- Acceptance: a 2 Mbit/s stream with a 100 Mbit/s ISP limit fills at least 10 percent of the plot and the marker is visible. After Pause the
  request count stays flat for 5 s. Update `tests/unit/test_dashboard_traffic_chart.py`, `tests/browser/traffic_chart.py` and record the new rule
  in `docs/implementation-plan-traffic-rate-reference-lines-2026-09.md`.
- Files: dashboard.js chart region, dashboard.html chart block (lines 1254-1266), those tests. No server change and no dependency.

### 3. One announcer and quiet polled lists (existing accessibility defect, M)
Value M: 32 `aria-live` regions in the markup, many polled every 2-5 s, so screen readers repeat refreshes. `showToast` flips `aria-live` (dashboard.js:1867) and the credential panel flips two regions (795-796, 817-818, plus reconnect.js:219).
Branch `fix/dashboard-announcer-and-polled-lists`. Plan items GUI-2.2, 2.3, 2.4.
- Brief: add two persistent hidden regions, `role="status"` and `role="alert"`, always in the DOM. `showToast` writes to one by kind and no longer sets
  `aria-live`. Move the credential-panel flips to the same regions.
- Remove `aria-live` from polled list containers in dashboard.html (connected-devices, unaffected-devices, saved-profiles, group-list, schedule,
  timer, progression, data-limit and service lists, time-quota-list, observations, uplink-details, traffic-status, traffic-rate-status). Keep one
  status line for count changes ("3 devices connected"). After `replaceChildren` restore focus by `data-key`.
- Acceptance: a MutationObserver journey shows nothing mutated by pollers sits in a live region; Start, Apply and Panic each announce in exactly one
  node (extend `tests/browser/sidebar_status.py` which asserts `["action-announcer"]`); errors reach `role="alert"`; a focused row button stays focused
  across one 5 s refresh; flip sites go 6 to 0 and `aria_live_in_markup` drops (`gui_stats --update`).
- Files: dashboard.html, dashboard.js, reconnect.js, tests/browser (new journey), gui-stats.json. Keep every id in `tests/unit/dashboard_ids.txt`.

### 4. Accessible confirm dialogs instead of 11 native confirms (UX and accessibility, M)
Value M-L. Branch `fix/dashboard-accessible-confirms`. Plan item GUI-3.1.
- Brief: extend `confirmWorkflow(message)` (workflows.js:633, 16 callers) to `confirmWorkflow(message, {confirmLabel, danger, title})`. The one-argument form stays
  the default. Add focus return to the opener and Escape cancel. Do not add a second helper.
- Convert these `window.confirm` sites in dashboard.js: 556 (reverse address move), 4787 (delete group), 7142 (Pi Wi-Fi change), 7173 (restore Wi-Fi name),
  7372 (copy router Wi-Fi), 8143 and 8151 (delete profile or device), 9141 (clear events), 9593 (import settings). Callers become async with a busy guard
  against double clicks.
- Delete, do not convert, the click handler at 7501-7509: `router-wifi-toggle` is forced `disabled` (renderRouterWifi) and the helper refuses writes since PR 87.
  Leave line 650 (remove dashboard access) until PR 92 or 138 merges, because they edit that block.
- Tests: replace the `page.once("dialog")` handlers in tests/browser (journeys.py, dashboard_access.py, phone_layout.py, router_copy.py, address_move.py,
  group_devices.py, dns_failure.py, observation_confirmation.py, forms_support.py) and any Node harness that stubs `window.confirm`. Each converted site gets a
  journey asserting the dialog, the verb label, Escape and focus return. `window_confirm_calls` falls 11 to 1; run `gui_stats --update`.
- Files: workflows.js, dashboard.js, tests/browser. No server change.

### 5. Reconcile ROADMAP and the planned lists with main (docs only, S)
Value L (but cheap): ROADMAP and planned lists contradict main, which misleads the next agent. Branch `docs/reconcile-roadmap-2026-10-16`.
- Brief: fix the stale items listed in section 2 above (ifb, online source, home-page bounce, M15 review text). Replace "current main is 5e0fed2" in
  docs/planned-features.md and docs/planned-fixes.md with the real baseline and add the 9-16 October PRs (87, 88, 91, 95, 96, 105, 106, 108, 110, 113, 115, 118,
  120, 123-127, 131, 133, 135, 137).
- Add `implementation-plan-switching-test-failures-2026-10-01.md` to the Superseded table in docs/README.md (replaced by PR 87). Point ROADMAP "Current
  remaining work" at the open PR list and the physical checklist.
- Optional, only if the owner wants history: commit the untracked plans and notes that are not on main (see the "NOT-ON-MAIN" list in section 1) as Historical.
- Every claim must be checked against main with git. Docs-only, so `scripts/local-ci.sh --changed` selects no browser module. Per the owner's rule a planned-lists
  docs PR is pushed and merged once CI is green.
- Files: ROADMAP.md, docs/planned-features.md, docs/planned-fixes.md, docs/README.md. Does not overlap with items 1-4 or 6.

### 6. Guardrails: PR template and an architecture test (hygiene, S)
Value L-M: every plan notes "no pull request template was found", and the maintainability plan asked for "a small automated check for forbidden imports and missing
packaged resources" that was never added. Branch `chore/guardrails-pr-template-architecture-test`.
- Brief: add `.github/pull_request_template.md` with: risk class and household risk, "touches a hash-pinned file (panic_guard.py, networking/helper.py,
  networking/access.py)? then approved bundle and Panic review", user-visible change, README and CHANGELOG impact, verification evidence on the head commit
  (local-ci.sh result while hosted CI is billing-blocked), pre-existing problems fixed or deliberately left, rollback.
- Add `tests/unit/test_architecture_rules.py`: AST-scan `api/dashboard.py` and the dashboard asset modules so presentation code imports nothing from `api.server`,
  `config.storage`, `networking` or `engine`; assert every `*.js` and `*.css` in `src/jittersim/api` is either assembled by `assemble_dashboard()` or on an
  explicit allow-list (would have caught a forgotten asset such as `speed-test.js` from PR 121); assert the `package-data` globs in pyproject.toml cover them.
- Constraints: no source change, runs on Python 3.9 without Playwright. Register the test in `scripts/local-ci-changed-map.txt` only if the runner needs it
  (unknown files default to all browser modules). Not a README.
- Files: new `.github/pull_request_template.md`, new test. Overlaps nothing.

Suggested order: 5 and 6 any time (no overlap); 2, 3, 4 sequentially because of dashboard.js and gui-stats.json (smallest and least risky first: 4, 3, 2, or whichever
the owner wants first); 1 after PR 138 merges.

---

## 6. Next candidates (not in the top 6)

7. **Device apply-start with Everyone running** (plan 3 remainder). M-L, renderer.py, compiler, applier, Linux packet checks needed. Owner-approved behaviour, currently refused with 409. Not pinned. Needs a Linux container and `tests/kernel`.
8. **GUI-2.1 per-page title and h1, 2.5 form errors, 2.8** (S-M). Shares dashboard.html and dashboard.js with items 3 and 4, so do it after them.
9. **Delivery B of erratic connections**: long-tail delay option plus Linux capability check before applying (L). Keep the capability check in applier or executor and out of helper.py, or split a helper change into its own pinned PR.
10. **Maintainability ticket 2** (profile and impairment request handling out of server.py). Wait until PR 92, 121, 136 and 138 have merged: server.py is their hot file.
11. **Website modes** (plan 20). L, high risk, probably helper change. Defer until the Panic batch is merged and approved.
12. **QA leftovers from docs/qa-report-2026-10-09.md**: control characters and bidirectional overrides are still accepted in profile and device names via API and import (log and display hygiene); an HTTP/0.9 request still gets an answer without a status line; the browser fixture is not hermetic for update actions (fix/stability touches tests/browser/fixture.py, so avoid that file now). All S, low value.

## 7. Pinned-file work worth doing later (not recommended now)

- **Independent expiry after a dashboard crash** (ROADMAP M15 gate, feature proposal 4, "a lease the helper enforces itself"). M, helper.py. Highest-value stability item left, but it needs the new approved bundle and Panic review, and PR 136 and 138 both rewrite helper.py right now. Do it after they merge.
- **Helper-side caching or sharing of identity checks** (plan 2): replaced by item 1 in the dashboard process, which avoids the pinned files.
- **Confirm or revert for Pi Wi-Fi changes** (feature proposal 8, from the dossier's "design finding"). M, helper. Overlaps fix/wifi-follows-router: wait for that branch.

## 8. Caveats

- Nothing was executed. "DONE" means the behaviour and tests are present in `origin/main` source and a merged PR says so. "Physical acceptance" is always separate.
- Line numbers refer to `origin/main` at `48c948d`. PR 92, 136 and 138 move several of them in server.py and dashboard.js.
- The `docs/README.md` index classification of the other ~77 plans was trusted after spot checks (outage logging to PR 91, random ranges and patterns to PR 84, device visibility to PRs 29-43).
