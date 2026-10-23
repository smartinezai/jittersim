# Is the front-end and test/CI tooling overengineered?

Reviewed: `origin/main` at `36e0d2ce` (a read-only detached worktree), 2026-10-17. Read-only: no code changed, no PRs opened.
Tools: jscpd 4 (duplication), eslint 9 (unused code, complexity), acorn (function sizes), plain `wc`/`grep`. Open PRs checked with `gh pr list --state open`.

## Status on 18 October 2026 (main `8c3f8171`)

The review below is a dated record. Its line numbers and open-PR columns are those of `36e0d2ce`.
What has happened since:

- Item 5 (`experiments/ui-react-prototype/`) and item 6 (`tests/browser/gui_container.py` and its
  unit test, plus the two container sections of `GUI_BASELINE.md`) are done: PR 148. The
  `gui-baseline` job and the 40 uploaded screenshots stay.
- The "small hardening" note is done: PR 148 made `assemble_dashboard()` insert the four extra
  scripts exactly once and refuse a second `startDashboard();`.
- Item 4 (the homepage reference prototype) was looked at and deliberately left. The live journey
  records 8 checks and the prototype about 32, so deleting the prototype would drop real coverage
  (policy rejection, expiry and protected identity cases exist only there). See PR 148.
- The window.confirm count of the metrics (13) is now 0: PR 141 moved every question into the
  in-page dialog. PR 146 removed every `aria-live` attribute of the polled lists and the focus
  keeper is the third redraw mechanism item 14 mentions. Items 14 and 18 should be read with that
  in mind.
- Items 1 to 3, 7 to 13 and 15 to 18 are open. The dead JavaScript of item 7 is still in
  `dashboard.js` (`renderProfileTable`, `keepUnaffected`, `removeUnaffected`, `showRuleSection`).
  `.github/workflows/wifi-recovery.yml` still lists the stale branch of item 16.
- PR 111, 121, 149, 151 and 153 are open and touch `dashboard.js`, `workflows.js` or `server.py`.
  PR 141, 143, 146, 147 and 152 have merged.

## Status on 23 October 2026 (measured against main `03fe31d8`)

- Item 7 is done. Removed from the page scripts: `renderProfileTable`, `keepUnaffected`,
  `removeUnaffected`, `showRuleSection`, the helpers only they used (`describeImpairment`,
  `describeExtras`, `formatBandwidth`), the unused `savedProfileRandomness` with its reset listener,
  and the unused `releaseStatusRequest`, a `state` in the device cards and a `result` in the speed
  test. ESLint `no-unused-vars` over the assembled page now reports only the functions that the two
  script blocks share (`readOldAdminTools`, `scrollBehavior`, `revealElement`). The CI step that runs
  ESLint is still open.
- Item 8 was looked at and left on purpose. The guards (about 50: 42 in `dashboard.js`, 6 in
  `dashboard-navigation.js`, 3 in `reconnect.js`, 2 in `workflows.js`) name functions or variables of
  the same page, but about 150 node tests build their page from hand-cut fragments and rely on them.
  One of them (`typeof successMessage`) checks a parameter and has to stay. Removing them needs stubs in about 11 test preludes,
  which adds more test lines than it removes. Do it together with one shared test prelude.
- Item 16: the stale branch is removed from `wifi-recovery.yml`. The rename and the single ruff run are
  still open.

## Verdict (5 lines)

1. The product code is not copy-paste bloated (JS duplication 0.26 %, CSS 0 %, Python tests 0.84 %). The vanilla, no-build, inline-CSP dashboard design is defensible for an offline Pi and earns its keep.
2. The overengineering is in the **tooling around it**: a 1,126-line `local-ci.sh` plus about 1,100 more lines of helpers, a map file, a parity test and docs that re-list what the workflows already say. There are 31 browser "journeys" that each start their own server and browser, a container harness used only by hand, and a reference prototype that still gates CI.
3. Tests are about 1.8 times the source (110k lines against 61k). About 22k unit-test lines cover the front-end alone, and roughly 150 or more assertions pin exact JS/HTML source text. Two ratchet files (`gui-stats.json`, `dashboard_ids.txt`) add churn to every UI PR without catching real bugs.
4. In `dashboard.js` the cost is fragment coupling and scattered mechanisms rather than clever abstractions. It has about 53 `typeof fn === "function"` guards for functions that are always present, two tooltip systems, five ad-hoc timers next to a good generic `makePoller`, three ways to keep focus across redraws and 14 hand-copied randomness blocks.
5. Suggested order: remove the dead tooling first (items 1-7, about 4-5k lines, mostly low risk), then make the dashboard consistent bit by bit (items 8-15). Don't rewrite the dashboard in a framework now.

## Ranked simplifications

Risk: S = mechanical, guarded by existing tests. M = behaviour-adjacent, needs a journey run. L = broad.
"Lines" means the estimated net lines removed.

| # | Where | What to do | Lines | Risk | Open PRs touching it |
|---|---|---|---|---|---|
| 1 | `scripts/local-ci.sh` (1126), `scripts/local_ci_exec.py` (191), `scripts/local_ci_report.py` (221), `scripts/local-ci-changed-map.txt` (53), `tests/unit/test_local_ci.py` (422), `docs/local-ci.md` (272) | Replace with one `noxfile.py` (nox is free and Python-native, about 150-250 lines). Its sessions would be `lint`, `unit`, `browser`, `gui`, `homepage`, `kernel` and `release_checks`. Make the workflows call `nox -s <session>`, so CI and local runs share **one** list instead of two lists kept equal by a 422-line parity test (`local-ci.sh:38-127`). `--failed` becomes `pytest --lf`, JUnit output is built into pytest, and shellcheck/actionlint/pip-audit go into a pre-commit config. `act` is not the right tool: it needs Docker for every job and is slow and incomplete on a Mac for the Playwright `--with-deps` jobs. Keep the lock and dirty-tree checks only if the owner really uses them (about 20 lines in nox). | ~1,900 | M | #121, #141 (map), #143, #146 |
| 2 | `tests/browser/*.py` (31 modules each spawn `tests.browser.fixture` and launch Chromium themselves: 31 copies of `start_fixture`/`Popen` and 33 `sync_playwright()` blocks, e.g. `help_icons.py:59`, `phone_layout.py:43`, `apply_clarity.py:43`) | Turn the journeys into pytest tests with `pytest-playwright` and one session-scoped `served_dashboard` fixture in `tests/browser/conftest.py`, run with `pytest tests/browser -n auto` (pytest-xdist). The 31 `run:` lines in `wifi-recovery.yml:60-91`, the module lists in `local-ci.sh:44-80` and the changed-file map all go away. Selection uses `-k` and markers. Journey bodies stay the same. | ~700-1,000 | M | #121, #141, #143, #146 |
| 3 | `tests/browser/baselines/gui-stats.json`, `tests/browser/gui_stats.py`, `tests/unit/test_gui_budget.py` | Keep the **strict** counters (`window_confirm_calls`, `inline_*`, `aria_live_*`, `important_declarations`), which encode real rules. Remove the size and style counts with a 2 % tolerance (`page_bytes`, `js_bytes`, `*_gzip6_bytes`, distinct padding, margin and colour values) from the gate, or print them as a report only. Today every feature PR has to run `--update --allow-growth` (#146 says so in its body), and three open PRs conflict on this file. | ~60, plus less churn | S | #121, #141, #146 |
| 4 | `experiments/homepage-bounce-reference/` (about 1,000 lines plus lockfile), `tests/browser/homepage_bounce_reference.py` (66), `tests/test_homepage_reference_fixture.py` (156), `tests.yml:58-74` | Delete the prototype. The shipped extension `extensions/homepage-bounce/` is already covered by `tests/browser/homepage-live/journey.cjs`. The prototype is a near-copy (every file differs slightly) and still gates every PR. Move its `package.json` (Playwright for Node) to `tests/browser/homepage-live/`. Check first that the live journey covers the reference's contract cases (pairing, expiry, protected identity). | ~1,200 | S-M | none |
| 5 | `experiments/ui-react-prototype/` (164 lines plus lockfile, not referenced anywhere) | Delete it. Keep its measured size table (Preact 6.4 KB gz, React 69.7 KB gz) in `docs/ui-overhaul-proposal-2026-10-09.md`. `experiments/` should not live in main. With items 4 and 5 done, the folder disappears. | ~190 | S | none |
| 6 | `tests/browser/gui_container.py` (589) and `tests/unit/test_gui_container.py` (433) | This harness runs screenshots in an "owned container" with image digests and wheel hashing. It is only run by hand (`GUI_BASELINE.md:99,127`), yet CI unit-tests it (`tests.yml:90`). Delete both. Also make the 30-minute `gui-baseline` job (40 screenshots that are uploaded but **never compared**) `workflow_dispatch` or weekly. Keep `a11y_axe` on every PR, because it earns its keep. If visual regression is ever wanted, use Playwright's own `to_have_screenshot`. | ~1,020 | S | none |
| 7 | Dead JS: `dashboard.js:7885` `renderProfileTable` (about 25 lines), `dashboard.js:8727` `keepUnaffected` (19), `dashboard.js:8747` `removeUnaffected` (8), `dashboard.js:9274` `showRuleSection` (3). Unused variables: `dashboard.js:8060` `savedProfileRandomness` (written 3 times, never read), `workflows.js:651` `releaseStatusRequest`, `workflows.js:367` `state` | Delete them. Also add a minimal **eslint** step to CI (flat config with `no-unused-vars`, `no-undef` and `no-unreachable`, run against the assembled script). Today CI has **no JS lint at all**, only `ruff --select E9,F63,F7,F82,F811` for Python. | ~65 | S | #141, #146 (dashboard.js) |
| 8 | About 53 `typeof X === "function"` guards: `dashboard.js` (45, e.g. 7× `guardDraft`, and in `request()` at 267-377), `dashboard-navigation.js` (6), `reconnect.js` (2) | `assemble_dashboard()` always inlines `workflows.js`, `access.js`, `reconnect.js` and `trust.js` into the same closure, so these functions always exist. Remove the guards. The only reason left for them is the node harness in `tests/dashboard_script.py`, which cuts single functions out of the page by brace counting. Fix the harness by giving it stubs instead of guarding production code. | ~0 net, `request()` complexity drops from 69 | S-M | #141, #146 |
| 9 | `tests/unit/dashboard_ids.txt` (275 ids) and `test_dashboard_navigation.py:55` | This was a one-off guard for the 2026-10-01 redesign: every old id must survive forever. Replace it with a generic test that every id the scripts look up with `element("…")` exists exactly once in the assembled page. That catches real breakage, needs no list, and also sees ids created in `workflows.js` template markup, which the current test skips (`HTML.split("<script>")[0]`). | ~270 | S | #146 adds an id |
| 10 | About 150 or more direct source-text assertions, such as `assertIn('option.value = EVERYONE_ELSE_TRAFFIC;', DASHBOARD_HTML)` (`test_api.py:1284, 3046-3050, 3337-3347`, plus fragment loops at 3140, 3308 and 3386; about 99 in `test_api.py`, 17 in `test_dashboard_quick_wins.py`, 12 in `test_dashboard_phone_layout.py`) | These pin implementation text: a rename or reformat fails them with no behaviour change. Delete the ones a browser journey or a node-run behaviour test already covers. Stop adding new ones, and keep only the security ones (CSP hashes, `test_api.py:2050`). | ~500-1,000 over time | S per test | #146 (test_api.py) |
| 11 | `dashboard.html:465-655`: 14 hand-copied 13-line `randomness-field` blocks (upload/download × 7 impairments; jscpd misses them because ids differ) | Replace them with one `<template id="randomness-field">` and a 20-line clone loop that fills ids, units and min/max. This is plain HTML and needs no library. | ~150 | S-M | none |
| 12 | Two tooltip systems: `dashboard.js:7` `setupSidebarActionHelp` (103 lines) and `access.js:268` `installFieldHelp` (69 lines). Help text is split between `data-help` in `dashboard.html` and tables in `access.js:250-266` | Keep one tooltip controller and keep all help text in the markup (`data-help`). Floating UI (MIT, about 3 KB gzipped, vendored) could replace the hand-written viewport math. Native `popover` would handle show/hide. | ~80-100 | M (`help_icons` journey) | #141 (access.js) |
| 13 | Polling: `makePoller` (`dashboard.js:7596`) is good, but traffic uses its own `setTimeout` chain (`1721-1890`), impact uses `setInterval` (`2193-2410`), the router Wi-Fi poll has its own flags (`7328`), and there are 5 separate `visibilitychange` listeners (`6979, 6984, 7584, 7621` plus makePoller) | Run traffic, impact and router Wi-Fi through `makePoller(job, interval)` so that hidden-tab handling, no-overlap rules and back-off live in one place. | ~60-100 | M (`page_polling` journey) | #121 |
| 14 | Redraw and focus guards: `replaceIfChanged` and `interactionInside` (`dashboard.js:200-214`), about 20 more `document.activeElement` checks (e.g. `rerenderConnectedDevices` at 8756), and #146 adds a third mechanism (`MutationObserver` focus keeper) | Use a keyed DOM morph instead: idiomorph (BSD-2, about 3 KB) or morphdom (MIT). It keeps focus and open `<details>` natively, so all three mechanisms collapse into "morph the list". Do this **after** #146 lands, as a follow-up. | ~100-150 | M-L | #146 |
| 15 | Hand-drawn canvas traffic chart: `trafficSeriesGeometry`, `trafficLineHits`, `drawTrafficKey`, `setupTrafficChartInspection` and `drawTrafficChart` (`dashboard.js:1379-1672`, about 280 lines) | **Borderline, don't do this yet.** uPlot (MIT, about 50 KB) would cover the drawing, hit testing and legend, but it adds a vendored file and a CSP hash for about 250 lines saved. Do it only if chart features grow. Do **not** adopt ECharts (about 1 MB) or Bootstrap: their untracked copies in the owner's main checkout should not reach main. | ~250 | M | none |
| 16 | `.github/workflows/wifi-recovery.yml` | The file name is wrong for its content: it is the "Pi release checks" workflow. It still pushes on the stale branch `feat/service-profiles-and-pi-hardening` (line 6). Ruff runs in both workflows. Browser journeys are split across `tests.yml:33` and `wifi-recovery.yml:51`. Rename it to `pi-release-checks.yml`, drop the stale branch, keep one ruff run, and after item 2 use one browser job. Keep the 3.9/3.11/3.13 matrix and the docker datapath steps, because they earn their keep. | ~40 | S | #121, #146 |
| 17 | `docs/`: about 157 dated plan, verification and review records (about 20k lines, 1.5 MB) at the top level (99 `implementation-plan-*`, 29 `verification-*`, 17 `review*`, 25 `*.json`), plus `IMPLEMENTATION_PLAN.md` at the repo root (marked "historical") | `git mv` them into `docs/history/<yyyy-mm>/`. `docs/README.md` already classifies them as historical, and git keeps their content. Top-level `docs/` would then hold about 25 current guides. This does not delete history. | 0 deleted, about 157 files moved | S | #144 (docs index) |
| 18 | Markup in JS: `workflows.js:299-310` `workflowCard(…innerHTML)` and `workflows.js:1220-1245` (the whole Updates card as a template string), plus `access.js` innerHTML (2 sites) | Move that fixed markup into `dashboard.html`, so one file owns markup, the id tests see it, and the CSP and innerHTML surface shrinks. | ~0 net | S-M | #111, #112 (workflows.js) |

Total for items 1-11 is about 4.5-5.5k lines removed, mostly tooling and tests, with a low risk profile.

### Small hardening (not a simplification)

`dashboard.py:28-33` inserts the four extra scripts by replacing every occurrence of `"    startDashboard();"`, without the "exactly once" check that the `__JITTERSIM_*__` markers get. Today it occurs once (in `dashboard.js`), but a second occurrence would silently duplicate four files. A real marker would remove the risk.

## What is complex but earns its keep

- **`assemble_dashboard()`**: inlining with hashed CSP, no build step and no Node on the Pi is the right call for an offline appliance. The per-load size (764 KB raw, 162 KB gzip) is acceptable on a LAN.
- **`request()` (`dashboard.js:267`, complexity 69)** handles timeouts, uncertain writes ("result unknown"), token-revision races and the protected-access choice. These are real safety cases. Draft and button bookkeeping could move out, but the core should stay.
- **`makePoller`**, which skips hidden tabs and never overlaps runs. It is the pattern the other pollers should copy (item 13).
- **The kernel and datapath tests in Docker and the outage lab**, which test real `tc`/`nft`/Unbound. Nothing simpler would catch those regressions.
- **The Python 3.9/3.11/3.13 matrix**, which covers the Pi OS versions.
- **axe-core accessibility checks** with vendored `axe.min.js` and `provenance.json`, and the **Lucide icons** inlined with their licence.
- **The strict GUI counters** (`window_confirm_calls`, inline handlers and styles, `aria-live` counts). These encode real rules. The size counters are the problem (item 3).
- **Release-candidate workflow and signed updates, Panic, fail-closed access**: out of scope and kept.

## Metrics

### LOC by area (origin/main `36e0d2ce`)

| Area | Files | Lines |
|---|---|---|
| `src/jittersim` total | 100 | 60,854 |
| of which Python | | 45,631 |
| Front-end assets (`api/*.js, *.css, *.html`) | 12 | 15,212 |
| `dashboard.js` | 1 | 9,728 |
| `workflows.js` / `access.js` / `reconnect.js` / `trust.js` / `dashboard-navigation.js` | 5 | 1,251 / 350 / 372 / 167 / 291 |
| `dashboard.html` | 1 | 1,488 |
| CSS (5 files) | 5 | 1,576 |
| Assembled page | | 15,227 lines, 764 KB, 162 KB gzip |
| `tests/` total | 366 | 110,329 |
| `tests/unit` | 229 | 79,469 (3,896 test functions) |
| of which front-end unit tests (`test_dashboard_*`, `test_renderer`, `test_gui_*`) | ~50 | ~21,700 (787 tests run JS through node) |
| `tests/browser` | 52 | 12,664 (33 Playwright scripts) |
| `tests/integration` / `kernel` / `qa` / `wifi` | | 10,315 / 4,368 / 2,061 / 372 |
| Local CI tooling (sh + 2 py + map + parity test + docs) | 6 | 2,285 |
| `.github/workflows` | 3 | 261 |
| `experiments/` | 13 | 1,167 (plus 2 lockfiles) |
| `docs/` | 204 | 33,852 lines, 3.2 MB (about 157 dated historical records, 1.5 MB) |

The test-to-source ratio is about 1.8:1. Front-end unit tests plus browser tests (about 34k lines) are 2.3 times the front-end source.

### Duplication (jscpd, min 5 lines / 50 tokens)

| Scope | Duplicated lines | % |
|---|---|---|
| JS (all `api/*.js`, 12,159 lines) | 32 | 0.26 % |
| CSS | 0 | 0 % |
| HTML | 26 | 1.75 % (but item 11's 14 blocks differ only by id) |
| Python tests and scripts (84,738 lines) | 709 | 0.84 % (biggest: `linux_queue_stats.py` and `linux_service_list_packets.py` share 32 lines, `test_ca_health_deadline.py` and `test_update_health_deadline.py` share 29) |

Copy-paste is not the problem. Parallel mechanisms and harnesses are.

### Biggest functions (assembled script)

| Lines | Function | Location |
|---|---|---|
| 312 | `renderObservations` (inner arrow 286) | `dashboard.js:3947` |
| 271 | `renderConnectedDevices` (inner arrow 198) | `dashboard.js:2837` |
| 122 | `drawTrafficChart` | `dashboard.js:1549` |
| 111 | `request` (complexity 69) | `dashboard.js:267` |
| 110 | `checkAdminToken` (complexity 40) | `dashboard.js:5691` |
| 103 | `setupSidebarActionHelp` | `dashboard.js:7` |
| 103 | `renderSavedProfiles` | `dashboard.js:6312` |
| 98 | `sidebarStatus` (complexity 39) | `dashboard.js:2415` |
| 84 | `pollTraffic` | `dashboard.js:1774` |
| 80 | `installAccessControls` | `access.js:176` |
| 72 | `installDraftControls` | `workflows.js:204` |

eslint `complexity > 25` flagged 12 functions: `request` 69, `checkAdminToken` 40, `sidebarStatus` 39, `retryUpdate` 35, `renderRouterWifi` 34, `followUpdate` 32, `pollRouterWifi` 30, `routerAction` 30, `renderUpdates` 29, `syncRememberedCredentials` 27, and two anonymous functions. The script has 1,004 functions in total.

### Front-end mechanism inventory

- **Timers:** 2 `setInterval`, 29 `setTimeout` sites, 4 `makePoller` users, 5 `visibilitychange` listeners.
- **DOM building:** 288 `createElement` calls in `dashboard.js`, plus innerHTML template markup in `workflows.js`/`access.js`.
- **Cross-file guards:** about 53 `typeof … === "function"/"undefined"`.
- **`window.confirm`:** 13 calls (#141 replaces them with `<dialog>`, which is the right call).
