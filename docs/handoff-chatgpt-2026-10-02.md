# JitterSim handoff from Claude to ChatGPT (session of 2026-10-02 evening)

Read this file first. It says where everything stands after Claude's session and what to do next in which order. Claude's weekly allowance ran out at the end of this session. Claude will not touch these branches again until Samantha asks.

## Goal

Samantha wants three things.

1. Updating the Pi and deploying a new version must be one click on the Pi dashboard or one command on the Mac.
2. The dashboard must be easy to open and use from the MacBook when the Mac is only on the Pi's own Wi-Fi because JitterSim switched the router Wi-Fi off.
3. The dashboard must feel smooth and simple. Today it feels clunky and needs an overhaul.

## Decisions Samantha made in this session

- The Pi stores a read-only fine-grained GitHub token (repository `smartinezai/jittersim` only with Contents read) in `/etc/jittersim/update-token` (root, mode 0600). This replaces the older rule of keeping repository credentials off the Pi. Only releases signed with the owner's Mac key can install. A leaked token can read the source but cannot install code.
- One neutral `.local` dashboard name (default `netlab.local`) is announced with mDNS on both Ethernet and the test Wi-Fi. Only that one address record is announced on the test Wi-Fi. This replaces the older plan to block mDNS on the test Wi-Fi.
- No browser extension or plugin is wanted for dashboard access. The homepage bounce design still uses an optional Chrome extension because a network cannot redirect a deep HTTPS page without breaking TLS. That design stays with ChatGPT.
- A redesign of the dashboard is wanted. The plan is `docs/ux-overhaul-plan-2026-10-02.md`. The clickable prototype is `docs/ux-prototype-2026-10-02.html`. Samantha has not yet given feedback on the prototype. Ask her before building phase 2 or later.

## Why the update button seemed missing

- Check for updates came from `main` on 2026-09-28 and lives only in the integration line (PR 45). The Pi line `feat/service-profiles-and-pi-hardening` never had it.
- In the integration build it is mounted with `workflowCard('release-controls', ..., 'setup')` and `TAB_PAGES.setup` points to the Router and Wi-Fi page. So it hid at the bottom of that page.
- It only looked for a release someone had already copied to the Pi over SSH. It never looked at GitHub.
- PR 68 below moves it to its own Updates page with a sidebar badge and makes it check GitHub.

## The pull requests and their order

All Claude PRs are drafts. Merge only when CI is green on the exact head commit. Merge from the bottom of a stack to the top. After each merge bring the next branch up to date with a merge commit (never a force push) and wait for green CI again.

The final status table at the end of this file gives the head commit and CI state of each PR at the moment Claude stopped.

Stack A (update path), each based on the one above it:

1. PR 45 `integrate/main-into-stack` into `feat/service-profiles-and-pi-hardening`. Brings the main line into the Pi line (staged updates, `jittersim update`, DNS failure chance, access controls, scenarios, delete for profiles and more). Claude resolved the `server.py` policy save conflict (the expected-version check stays and omitted sections keep their stored value). Claude also fixed a real page bug where a late "Token accepted" message overwrote "Profile saved" and made the impact fixture deterministic. Its description says the owner should try it on the Pi before it merges.
2. PR 46 `feat/cli-remote-and-deploy`. Remote CLI on the Mac and `deploy-from-mac.sh`. Claude added one total deadline across connect and TLS and headers and body in `remote_client.py`.
3. PR 52 `feat/signed-releases`. Signed releases and passwordless staging. Claude fixed `list-keys` for a non-root caller and added the test Wi-Fi detection in `deploy-from-mac.sh` and a short SSH connect timeout.
4. PR 66 `feat/deploy-without-ssh` (based on PR 52). `publish-release.sh` creates a GitHub release with exactly the two signed assets. `POST /api/updates/upload` stages a signed release over HTTPS through the dashboard. `deploy-from-mac.sh` takes the HTTPS path by itself when the Mac is on the Pi Wi-Fi. `jittersim remote update status|upload|install`.
5. PR 68 `feat/online-release-check` (based on PR 52). The Pi checks GitHub with the token through a one-shot root unit `jittersim-update-fetch.service`. New Updates page with installed version in the footer and an Update available badge. Settings for a daily check and Install automatically (both off by default).

Stack B (Pi Wi-Fi access and smoothness), each based on PR 45:

6. PR 67 `feat/wifi-restart-reconnect`. Actions that restart the Pi Wi-Fi answer first and the page shows "Reconnecting to the Pi" and then the stored result. Load failed is gone.
7. PR 64 `feat/keep-my-mac-recognised`. A browser with the remembered token on a new private address can move Allow this device and protection to the new address in one confirmed step with undo.
8. PR 65 `feat/trusted-local-https`. A name-constrained local certificate authority on the Pi and a Trust this Pi download after sign-in (.crt and .mobileconfig) and the neutral name `pi.home.arpa` on the test Wi-Fi.
9. `feat/one-dashboard-address` (based on PR 65). The neutral `netlab.local` name on both networks.
10. PR 70 `perf/server-concurrency`. Threaded dashboard with two reserved Panic slots and a shared client scan cache with a stale fallback. Benchmark with a simulated 1.5 s apply: health and device list and Panic went from about 1.45 s to about 0 s.
11. `perf/page-polling`. Self-rescheduling pollers paused in hidden tabs, traffic sampling only on the Traffic page, the last good list kept while the helper is busy, request timeouts, targeted refresh after saves, no re-render while focused, Panic always enabled, a password manager friendly token form.
12. `feat/ux-quick-wins`. Redesign phase 0: neutral page before sign-in, one message instead of three, one word per idea, no internal IDs on screen, remember sign-in for 24 hours or 7 days or 30 days.

ChatGPT's own open work: PR 58 `codex/publish-homepage-reference` (homepage bounce reference and DNS acceptance journeys). Other drafts that predate this session: PR 50 (service list variation), PR 51 (hardening batch), PR 53 (phone layout), PR 54 (planned fixes document).

## What must still be done, in order

0. CI is blocked by a GitHub billing problem (see the final status table). Tell Samantha. Do the local work below in the meantime but do not mark anything ready or merge until CI runs again and is green.
1. Read the final status table below. For every PR with red or missing CI open the failing job log with `gh run view RUN --log-failed` and fix it on that branch. Known flaky tests seen tonight: `tests/unit/test_helper.py::UnixHelperServerTests::test_client_excludes_protected_devices_over_the_socket` (helper busy on Python 3.11) and `tests/unit/test_deploy_from_mac.py::test_ctrl_c_also_stops_a_running_call` under load and `tests/integration/test_wifi_recovery.py::test_idle_socket_clients_cannot_create_unbounded_workers`. Rerun once with `gh run rerun RUN --failed` before treating them as real.
2. Ask Samantha whether PR 45 may merge without her Pi test. If yes merge 45 then 46 then 52 then 66 and 68 in that order with green CI each time. If no give her the Pi checklist from the PR 45 body.
3. Merge the integration line forward into every Stack B branch after PR 45 merges. Expect conflicts in `src/jittersim/api/server.py` between PR 70 and `perf/page-polling` and PR 67 and `feat/ux-quick-wins` because all of them touch the dashboard. Resolve by intent and run the full suite and all browser journeys after each resolution.
4. Finish `perf/server-concurrency` if the final table says items are open: gzip and ETag for the page, serving before startup recovery finishes and the read-only tcpdump capture outside the helper operation lock. Also the helper's own `normal-access` still waits up to 3 s for the operation lock during an apply.
5. Make a plain `deploy-from-mac.sh` install by default (today it needs `--install`). Add `--stage-only`. About 25 tests assume the old default.
6. Run the On the Pi checklists of PRs 45 and 52 and 64 and 65 and 66 and 67 and 68 with Samantha. Order: install PR 45 once with the installer. Create the release key with `deploy/raspberry-pi/make-release-key.sh` and trust it. Set the GitHub token with `sudo jittersim update set-token` (stdin). Trust the Pi certificate on the Mac. Set the Mac's Private Wi-Fi address to Fixed for the Pi network.
7. Build the redesign in the phases of `docs/ux-overhaul-plan-2026-10-02.md` after Samantha has commented on the prototype. Phase 1 (one state endpoint with ETag and a keyed diff renderer) should build on `perf/page-polling`.
8. Fold the new items below into `docs/planned-features.md` and `docs/planned-fixes.md` after PR 54 merges.

## New items for the planned lists

From Claude's gap scan of the integration branch (not covered by the branches above):

- Update steps stop the services before the backup and disk check and pip install, so the brief interruption can last minutes and can fail after the outage began (`src/jittersim/updates.py` around 577 to 662). Move the backup and disk check and a wheel build before the stop.
- Update steps send all command output to DEVNULL, so failures cannot be diagnosed (`updates.py` around 290 to 304). Keep the last 2 KB of stderr per step in the journal and one plain reason in `status.json`.
- After an update the open page keeps the old script. Put the release version in `/api/health` and show "Updated. Reload" when it changes.
- Schedules and limits still fires about 12 requests every 5 seconds through its own page jobs.
- The router-off banner cannot name the Pi network because the Wi-Fi name is not shown before a change.
- The stored Wi-Fi change result is in memory only and a dashboard restart within the hour loses it.
- A rollback copies the kept CA folder back as root:root so Trust this Pi can say not available until the next install.
- The upload endpoint can hold a worker for up to 120 s on a slow link. Fine after PR 70. Before PR 70 it blocks the whole dashboard.
- iPhone Safari may not install a .mobileconfig fetched as a blob link. Serve it through a plain link if the Pi test shows that.

From the redesign audit (`docs/ux-audit-2026-10-02.md`): applying a profile has five routes and stopping has four. A device profile and the general profile conflict without explanation. Blocking one app for one device has no direct path. The Devices page has 55 controls. Impact sits on Diagnostics away from the device. Protected is worded five ways. Internal IDs show on screen. Each status message appears three times.

## Rules to follow

- Use your own `codex/` branches or push to the Claude branches above only after Claude has handed them over (this file is that handover). Never force push. Never push to `main` or the Pi branch directly.
- Send a PR for review only when CI is green on its head commit. A local pass does not count.
- Never start or stop Docker containers that are not yours. The shared Docker VM had fourteen unrelated containers tonight.
- Never put a token or password in a file or log or command line. The GitHub token goes in through stdin only.
- Nothing may show the name jittersim before sign-in.
- Plain words in the dashboard. Dark theme only.
- Write the failing test first for every behaviour change.
- Keep `CHATGPT-TO-CLAUDE.md` and `CLAUDE-TO-CHATGPT-COORDINATION.md` untracked.

## Risks and open decisions

- PR 45 merges the main line into the Pi line. Its body asks for the owner's Pi test first. Samantha decides.
- Threading the dashboard (PR 70) changes locking everywhere. The reviewer fixed five races with tests. A real Pi run is still needed. One small window remains where an apply can land right before Panic and is undone right after.
- Install automatically (PR 68) installs every new signed release without asking once it is on. It is off by default.
- The admin token alone can now stage owner-signed newer releases over HTTPS (PR 66). That is the same threat model as passwordless staging. A stolen signing key plus the admin token from a registered device could install code.
- mDNS on the test Wi-Fi (one-address branch) lets test devices see the neutral name.
- Nothing from tonight was tried on the real Pi or a real Mac or iPhone. Installed Pi revision and recovery access are still unconfirmed.

## First three actions

1. `cd ~/Github\ repos/jittersim && git fetch --all --prune && gh pr list --state open` then read the final status table below and compare heads.
2. For each Claude PR run `gh pr checks NUMBER` and fix or rerun red jobs as in step 1 of the order above.
3. Ask Samantha three things: whether GitHub Actions billing is fixed, whether PR 45 may merge before her Pi test and what she thinks of `docs/ux-prototype-2026-10-02.html`.

## How to check the state

- `gh pr list --state open --json number,title,headRefName,isDraft`
- `gh pr checks NUMBER` and `gh pr view NUMBER --json headRefOid,mergeable`
- Claude's worktrees are `~/Github repos/jittersim.worktrees/claude-*`. Each is free to use now. Some contain an untracked `.venv-claude` with Playwright 1.63.0 (CI pins the same version). `claude-int-merge/.venv-claude` worked for all browser journeys.
- Local test run: `PYTHONPATH="$PWD/src" python -m pytest -q` and `ruff check --select E9,F63,F7,F82,F811 src tests` and `python -m tests.browser.NAME` for each journey listed in `.github/workflows/*.yml`.

## Final status table (2026-10-03 00:45 Berlin)

BLOCKER: GitHub Actions stopped starting jobs around 00:30. Every job fails in about 3 seconds with "The job was not started because recent account payments have failed or your spending limit needs to be increased". That is a GitHub billing setting only Samantha can change. Until it is fixed no PR can get green CI so nothing may be sent for review or merged. Any red result after 00:30 says nothing about the code. Rerun the jobs once billing works with `gh run rerun RUN`.

| PR | Branch | Head | CI on that head | Notes |
| --- | --- | --- | --- | --- |
| 45 | `integrate/main-into-stack` | `cf2a6fe` | billing failure | Claude left it at `1044184` with all checks green. Someone else pushed `cf2a6fe` later. Check what it adds. PR 72 also uses this branch. |
| 46 | `feat/cli-remote-and-deploy` | `7899f5a` | green | |
| 52 | `feat/signed-releases` | `4330aa2` | green | |
| 64 | `feat/keep-my-mac-recognised` | `f5335b0` | green | browser journey `address_move.py` never run locally |
| 65 | `feat/trusted-local-https` | `0245124` | green | Unbound lines not checked with `unbound-checkconf`. Rollback restores the CA folder as root:root. A docstring in `api/trust.py` wrongly says the easy address is in the health check. |
| 66 | `feat/deploy-without-ssh` | `f8f4b39` | billing failure | Merged PR 52 in. On the test Wi-Fi the deploy now goes over HTTPS by itself. With `--ssh` or `--use-installer` it stops early. Local: 3442 passed (one wording failure fixed after), lint clean. |
| 67 | `feat/wifi-restart-reconnect` | `053201e` | green | one 3.11 helper-busy flake was rerun |
| 68 | `feat/online-release-check` | `1817898` | billing failure | Merged PR 52 in cleanly. Local: 3514 passed, lint clean. Browser journeys incl. the edited `journeys.py` not run. Two small fixes still open: read `/api/updates` on every dashboard open so the badge shows without pressing Check, and fall back to the installed version so the watch label never reads "Updated to .". |
| 70 | `perf/server-concurrency` | `c31b5cb` | billing failure | Threaded server plus scan cache (reviewed, five races fixed). Claude added gzip and ETag: page 527 KB to 109 KB and 304 on reload. Local: 2871 passed, lint clean, `dashboard_access` and `token_remember` journeys pass. Still open: serve before startup recovery finishes, tcpdump capture outside the helper lock, `GET /api/plan` waits behind an apply, helper `normal-access` waits up to 3 s for the operation lock. |
| 73 | `feat/ux-quick-wins` | `8c9b435` | none yet | Phase 0 built at `2f802a6`. `8c9b435` is a WIP commit of the reviewer's unfinished fixes. Review and test it before trusting it. |
| none | `perf/page-polling` | `8402633` | none | All eight page items are implemented. About 80 unit tests in node harnesses fail because they lack the new helpers. `2372c6e` and `8402633` are WIP commits of an unfinished harness repair. No PR yet. |
| none | `feat/one-dashboard-address` | `a857dd3` | none | WIP commit of an unfinished build for `netlab.local` on both networks. Unreviewed and untested. No PR yet. |
| 58 | `codex/publish-homepage-reference` | | | ChatGPT's own |

Other sessions opened PRs during this session that Claude did not touch: 60 `feat/semantic-versions`, 71 `fix/cli-cleared-list-notice`, 72 (on `integrate/main-into-stack`) and 74 `fix/deploy-uses-release-version`. Check their owners before editing those branches. PR 74 and PR 60 likely touch the same deploy and version code as PRs 66 and 68.

Extra branch with an older partial handoff from an agent: `docs/handoff-pr45-2026-10-02` (`808422c`). It is superseded by this file and can be deleted after Samantha agrees.
