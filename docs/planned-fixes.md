# Planned fixes

The exact October2 fix list is preserved in [the historical archive](history/2026-10-02-backlog-and-ux/planned-fixes.md).
New feature ideas are in [feature proposals](feature-proposals-2026-10-09.md).

Baseline: candidate `afa7d222` on `origin/main`, checked on 24 October 2026.
The open fixes below were reconciled with that source and its merged pull requests.
The six local candidate gates passed; the unit gate covered Python 3.12 only. Hosted
CI is billing-blocked, and the Python 3.9, 3.11 and 3.13 matrix has not been run on
this candidate. Pi and phone acceptance remain outstanding. See [local CI](local-ci.md).

## Changes after the 4 October candidate (9 to 19 October 2026)

- Router Wi-Fi writes are permanently refused by the Pi helper and the dashboard
  switch is read only (PR 87, merge `3903581`). Read-only router status checks and
  copying the router credentials to the Pi remain. The original outage cause is
  still unconfirmed.
- The dashboard has a skip link to the main content, unique region names and a
  named actions column (PR 88, merge `bf334fe`).
- Dashboard token checks that meet a busy helper (PR 86) and persistent outage logging
  (PR 91) are merged. Both change the privileged helper. An independent Panic-consumer review
  ([results](review-results-panic-consumers-outage-logging-2026-10-09.json)) approved that
  helper and access source as an entry of `PANIC_APPROVED_SOURCES`. PR 136 later added the
  newest entry, and main's `helper.py`, `access.py` and `panic_guard.py` match that one
  (checked with `shasum`). A review is not approval of deployment.
- The combined Panic batch landed with one approved bundle (PR 136, 16 October, which carried
  PR 92, 103 and 104): after a helper restart the stop flag comes from the Panic guard or
  `gateway.json`, Panic's household release opens the firewall first and never rolls back to the
  restrictive rules, the helper has an input pool of 16 plus a reserved Panic slot, a bounded busy
  retry and one total Panic deadline, and protected devices open the dashboard with an emergency
  device check on the Panic path ([review results](review-results-gated-panic-batch-2026-10-10.json)).
  The review found items that stay open ([carry-forward list](carry-forward-panic-batch-2026-10-10.md)).
- The Pi's test Wi-Fi no longer stays on a fallback name such as `JitterSim-Recovery` after the
  4 October incident: after a router copy that worked the Pi records the router's name and security
  (no password), and every boot renames a saved Wi-Fi with another name back to it (PR 140,
  `wifi_follow.py`). The router is not contacted and a manual Wi-Fi change or a name reset stops
  the following.
- Two Wi-Fis with one name (PR 160 and 169, merged): since PR 140 the boot gave the Pi the router
  name even when the router's own Wi-Fi was on. The boot step now looks once at the air and keeps
  `JitterSim-Recovery` for that boot when the router name is on the air from another BSSID
  (router safety audit round 2, owner decision D1). The look does not depend on the Pi's language.
  The test Wi-Fi also refuses to start when the Ethernet uplink is inside `192.168.50.0/24` (D3).
  Open: a physical check on the Pi that includes a cold boot with the router Wi-Fi on and shows
  `boot_scan: seen`, not `failed`.
- Merged on 18 and 19 October (details in the [changelog](../CHANGELOG.md)): the update status
  carries the signed release notes and a manual check only checks (PR 111), a device runs its own
  profile beside Everyone (PR 149), the management firewall keeps test Wi-Fi addresses and foreign
  ARP answers off the router cable (PR 150 and 157), saved devices follow a new private Wi-Fi
  address (PR 151), four gaps of the security audit are closed (PR 159), the helper removes what an
  interrupted apply left (PR 168), the scheduler keeps one database connection and unchanged quota
  counters are not written again (PR 164), and the QA leftovers of PR 152 are fixed (PR 162).
- Panic and capacity: Panic stays reachable when silent connections fill the dashboard
  queue (PR 95). The Panic reserve and the waiting queue are kept for real Panic requests
  (PR 123). Reliability tests for Panic and capacity were added (PR 93).
- Pi-only Wi-Fi and the router: the test Wi-Fi DHCP hand-over is safe for devices that
  join the copied router name (PR 105). Router password checks are spaced apart (PR 106).
  Damaged, copied and reordered packets stay away from the household router, with upload
  corruption on an `ifb` device (PR 125), and protected or unaffected devices stay out of
  that intake (PR 135). The outage log has a read-only router health canary (PR 133). The
  Pi-only cutover outage lab and its results were added (PR 120).
- Updates and releases: one command cuts, checks, signs and publishes a release (PR 108).
  The Pi updates in one step from its terminal or from the Mac (PR 110). An update is
  reported as done only when its install request completed (PR 122). The release test
  image copies the scripts, docs and workflows it needs (PR 137). The dashboard stops
  cleanly on SIGTERM (PR 113).
- Dashboard: the refreshed look with design tokens, icons and motion (PR 115). The
  schedule, timer, progression and limit forms have browser tests and the defects those
  found are fixed (PR 118). A static budget ratchet guards the dashboard page (PR 124).
  Applying a profile to one device starts only that device (PR 96), the review defects
  of that change are fixed (PR 127) and a failed device-only cleanup stays owed until it
  works (PR 131).
- QA: the exploratory findings of 9 October ([report](qa-report-2026-10-09.md), PR 107) 1 to
  5 and 7 and the cheap part of 6 are fixed (PR 126). Three flaky tests are deterministic
  (PR 99). The 22 pip-audit findings in the release locks are triaged (PR 119).

- The carry-forward fixes of the Panic batch landed with a second approved bundle (PR 138,
  18 October): a stalled write of the stop records no longer holds Panic, dashboard Panic waits a
  bounded time for the scheduler lock and answers unconfirmed when it cannot take it, a rollback
  keeps the current `dashboard-access.json` and removes the upload intake it cannot leave behind,
  the installer starts the unchanged installation again when that cleanup fails, and the status
  reads of the Updates page and the ad blocking line no longer discard a running device scan (the
  502 of the browser explorer). The review found items that stay open (see Open fixes).
- Pi boot robustness (PR 142): the `.local` name is announced again because Avahi no longer refuses
  records from other programs, the name service survives an early hangup and restarts after any
  exit, the journal stays on disk under `90-jittersim-persistent.conf` and the boot noise of the
  control firewall and AP units is gone.
- Dashboard accessibility: every destructive action asks in one accessible in-page dialog (PR 141),
  one announcer and quiet polled lists keep focus on refresh (PR 146), and the QA sweep fixed focus
  after a question, Escape on the traffic page and save chatter (PR 152). The stability pass
  (PR 143) made local CI run on Python 3.9, skipped the TLS tests LibreSSL cannot run and gave
  every Python the same 4300-digit limit for JSON numbers.
- Release tooling: a real dry run of v0.2.0 found six bugs that every real release would have hit
  and PR 139 fixed them (the last local CI check, the Python path, the draft lookup, the notes
  heading level, a key that left the agent and over-long notes). PR 117 raised the wheel build lock.
  The Updates page names the staged release on its install button (PR 112).
- Clean-up: dead and test-only code and two unused prototypes were removed (PR 147, 148), the
  release test image copies the top-level pages (PR 154) and a pull request template and an
  architecture test were added (PR 145). The roadmap and these lists were reconciled once on
  17 October (PR 144) and again now.

The coordinated 4 October candidate repaired watchdog timeout classification, bounded
helper requests, preserved upload-timeout handling and refused unknown legacy
router fields or write destinations. Manual router status checks prevent
background discovery from causing new login attempts. The precise Panic
producer and consumers have whole-source approval. Random ranges preserve the
existing relative-mode selection sequence.

Two browser checks needed updates when those changes were composed. Recovery
now explicitly checks router status and waits for intercepted requests.
Keyboard diagnostics permits only the pure range-preview POST while continuing
to detect actual writes. The failed runs and corrected focused checks are
retained. Those results describe the 4 October candidate.

Candidate `afa7d222` has passed lint, unit (Python 3.12), browser, GUI, kernel and
release checks locally. Signing trust, safe recovery and physical Pi/phone
acceptance remain deployment gates. Local results do not establish successful hosted
jobs or an installed release.

The physical router incident remains unresolved. Later healthy addresses and
services were recorded after recovery. They do not establish the initial
network isolation, protected-client match or sender of the router event.
PR 91 and PR 133 persist safe outage evidence. Comparing router event times with
the Pi's state is an owner-run capture ([outage dossier](outage-dossier-2026-10-04-2026-10-09.md)).

Other historical findings require fresh source confirmation before becoming
new tickets. The archived list retains all details and proposed fixes.

## Open fixes

Open against candidate `afa7d222` on 24 October 2026. PR 161, PR 170 and their
review follow-ups are merged. PR 175 is held until v0.2.0 is installed; the PR 209
Docker test-harness follow-up is held until publication. Their states are in the
[roadmap](../ROADMAP.md) and on GitHub.

- Two owner decisions from the review of PR 138 ([review results](review-results-carry-forward-2026-10-16.md)).
  The recommended default applies until the owner decides.
  1. A rollback to a release that does not know the upload intake cannot always remove it. Today
     the cleanup runs before the restore, runs again after it and the final status warns to press
     Panic, check `tc qdisc show` and reboot the Pi. The alternatives are to block such a rollback
     or to keep a cleanup-capable Panic path until the intake is gone.
  2. A rollback restores the database, so a device removed from **Protected devices** after the
     update comes back with its protection grant for the dashboard. The default is to accept that
     and remove the device again. The alternatives are to keep the current protected list across
     rollbacks or to ask before restored protection grants access.
- Review notes carried forward from PR 138 ([carry-forward list](carry-forward-panic-batch-2026-10-10.md)).
  A late error of the stop-record saver has no log line (O1). An early save error that is not a
  storage error ends Panic before any rule is removed (O2). Both wait for the next change of the
  pinned `helper.py`. The household release saves its suspension with a plain `fsync` inside
  `AdStore.access_transition()`, so a stall that begins there still holds it. The stalled-save fix
  bounds only the saves before the rules are removed. The helper socket still authorizes by group
  only ([design note](design-helper-action-authorization-2026-10-09.md), a proposal). A boot with an
  incomplete household release latches a Panic stop, and Panic can be unavailable for up to 90
  seconds at boot if a restore hangs.
- Every private request from a test-network peer still asks the helper for its own dashboard
  access status (`_check_device_access`). PR 86 bounded the wait and kept the token and PR 136
  added a bounded busy retry, but simultaneous requests do not share one answer and the sign-in
  retry after "Device verification is busy" is manual. The probe with four simultaneous checks has
  not been repeated on an installed Pi. The backlog audit proposes one shared answer per peer
  ([backlog audit](backlog-audit-2026-10-16.md), item 1).
- Small dashboard defect left by the QA sweep of 17 October (PR 152). The note under **Router outage
  behavior** says "until you choose Apply" but the button is **Start**. The other findings of that
  sweep (Revoke all homepage pairings, Clear on the Measurements page, row names on the **Delete**
  and **Edit** buttons and the repeated Restore Wi-Fi name sentence) were fixed in PR 162.
- Names accept control characters and bidirectional overrides through the API and import.
  This is a log and display hygiene question ([QA report](qa-report-2026-10-09.md)). An
  HTTP/0.9 request still gets an answer with no status line. The browser fixture is not
  hermetic for update actions.
- Loaded Pi capacity was never measured (feature proposal 20 in
  [feature proposals](feature-proposals-2026-10-09.md)).
- Tooling notes. The usage text of `scripts/local-ci.sh browser` still says 28 Pi browser modules
  (there are 32 besides the journeys). The advisory checks of local CI report findings
  (zizmor 13, pip-audit 21, shellcheck 16 on 17 October) that never gate. The two overengineering
  reviews list further removals ([backend](review-overengineering-backend.md) and
  [front end and tests](review-overengineering-frontend-tests.md)).
- Dashboard QA sweep of 23 October 2026 (main `3afb48d5`, 601 controls pressed at 1440 and 375 px, no
  uncaught error or failed request). Four S3 findings were fixed in PR 204. Left open:
  - **S2, keyboard focus is dropped to the page after Start or Reset network.** Both buttons are disabled
    while the action runs (`setActionBusy`, `dashboard.js` about 6042) and nothing gives focus back, so the next
    Tab starts at "Skip to main content". The same happens after confirming **Reset Wi-Fi name** on a phone.
    Panic keeps focus. Fix: in `setActionBusy(false)`, if focus is on the body and the button had it when the
    action began, focus the button again. It touches the code that drives Panic-adjacent buttons.
  - S3: the phone bar cuts "Router Wi-Fi · read only" at 375 px and below (`phone.css` about 104, needs a shorter
    label or two lines). The sidebar needs a scroll in short or landscape windows (the quick **Reset Wi-Fi name**
    button is 4 px under the fold at 1366x768). `#connection-notice` is an `aside role="status"` toggled with
    `hidden`. The group select text "Create a group on th..." is cut (tests pin the wording). The "Update
    available" pill wraps to two lines in the desktop nav (a shorter text fixes it). On a phone the Allow
    button sits under the Credentials sheet. The help icons are 20x20 px, under the 24 px minimum of WCAG 2.2.
    The Schedules form wraps its End field alone at 1366x768. Panic is behind the backdrop while a window is open
    (by design, Close and Escape work).
  - Triage of the Codex dark-theme findings: the scrolling page-link row at 375 px is intended
    (`phone.css`, pinned by `test_every_page_link_stays_in_one_row_that_scrolls_inside_itself`). Axe reports
    `skip-link` on seven pages at 375 px because the scrolled-away page links look like hidden skip links. It
    is a known false positive that predates these pull requests and `a11y_axe.py` only observes, so reviewers
    should not report it again. The "Token accepted. Diagnostics loaded." toast covering content at 375 px is
    intended (it leaves after 6 seconds and has Dismiss). Optional polish: skip it for the automatic sign-in on
    page load.
- Follow-ups of PR 189 (dashboard speed test) that PR 208 did not close: a script with the admin token can park
  several workers for 1 second each while the first part streams, so allow one waiter per run. The brcmfmac
  behaviour of the priority reset is not yet checked on a Pi. PR 208 added the order test for the two socket
  options, the Linux value 12 for `SO_PRIORITY` and the label without the internet.
- Follow-ups of PR 188 (2000-name block list, from the reviews and the measurements) that PR 208 did not close:
  renewals queue up to 24 lookups, so a new name can wait seconds behind them on a slow DNS server (cap queued
  renewals at 8). A failed renewal caches an empty answer for 60 seconds and drops good addresses (keep the old
  addresses when a lookup raises). Measured at 2000 names of 29 characters: the `/api/access` answer grows from
  9.9 KB to 135 to 179 KB and is polled every 5 seconds (leave the names out of the poll), compiling a plan
  takes 9.3 ms instead of 0.7 ms on a Mac (about ten times that on a Pi 3B+, cache the plan by configuration
  version), and the 4096-address cap shows only in a log line. PR 208 closed the delete-rule check and the
  history restore test.

## Observed accessibility improvements

The exact 6fb Linux scans completed but were observational. Eighteen states
reported moderate or minor findings. Twelve need a working keyboard link to
the main content, eight need distinct labels for repeated page sections and
two need text in an empty table heading. No serious or critical violations
were reported. These are separate follow-ups with their own source checks.

PR 88 added the skip link, unique region names and the named actions column. The
scan then reported no `landmark-unique` or `empty-table-header` findings. It still
reported `skip-link` in the 12 phone-width states, because axe treats the off-screen
sidebar links (`#devices`, `#router` and so on) as skip links and wants matching
element ids. The page keeps those hashes unmatched on purpose so Back works without
scrolling. Resolving that needs a navigation design change and stays open. The scan was
not repeated after the look refresh of PR 115 or the announcer change of PR 146 (which removed the
`aria-live` attributes of the polled lists).

## Test fixture reliability

The first current Python 3.9 run observed an empty readiness marker before its
contents were written. The unchanged full retry passed. Both runs are retained.
PR 99 fixed it: a test child now writes the marker under a temporary name and
renames it into place (`tests/marker_support.py`), so a polling parent reads the whole
marker or none. No production failure was demonstrated by this fixture race.

The explorer test `test_every_page_is_clean_at_desktop_and_phone_width` failed about one run in
ten with `GET /api/connected-devices -> 502` (`network changed during client scan`). Two status
reads of the Updates page and the ad blocking line counted as network changes in the helper client
and discarded a running scan. PR 143 hid it in the browser fixture and PR 138 fixed the cause by
listing the reads as read-only (the fixture retry was removed again).

See [final local verification](verification-final-local-release-2026-10-04.md).
