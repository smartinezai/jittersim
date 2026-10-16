# Planned fixes

The exact October2 fix list is preserved in [the historical archive](history/2026-10-02-backlog-and-ux/planned-fixes.md).
New feature ideas are in [feature proposals](feature-proposals-2026-10-09.md).

Baseline: `origin/main` at `48c948d` (the merge of PR 115 on 10 October 2026), checked on
16 October 2026. Statuses come from the source and the merged pull requests on that commit. Nothing
here was run on a Pi. The 4 October accepted main `5e0fed2` matched tested candidate 6fb864f. Hosted
jobs cannot start (billing limit), so every pull request is gated by `scripts/local-ci.sh` on its head
commit ([local CI](local-ci.md)).

## Changes after that candidate (9 and 10 October 2026)

- Router Wi-Fi writes are permanently refused by the Pi helper and the dashboard
  switch is read only (PR 87, merge `3903581`). Read-only router status checks and
  copying the router credentials to the Pi remain. The original outage cause is
  still unconfirmed.
- The dashboard has a skip link to the main content, unique region names and a
  named actions column (PR 88, merge `bf334fe`).
- Dashboard token checks that meet a busy helper (PR 86) and persistent outage logging
  (PR 91) are merged. Both change the privileged helper. An independent Panic-consumer review
  ([results](review-results-panic-consumers-outage-logging-2026-10-09.json)) approved that
  helper and access source as the newest entry of `PANIC_APPROVED_SOURCES`, and main's
  `helper.py`, `access.py` and `panic_guard.py` match it. The review is not approval of
  deployment.
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
retained. The complete current Linux sequence passed.

All configured local pipeline checks passed and the accepted main tree matches
the frozen candidate. Signing trust, safe recovery and physical Pi/phone
acceptance remain deployment gates. Hosted jobs stopped before execution by billing limits
can use fully completed local counterparts under the current human authority.
Do not describe them as successful hosted jobs.

The physical router incident remains unresolved. Later healthy addresses and
services were recorded after recovery. They do not establish the initial
network isolation, protected-client match or sender of the router event.
PR 91 and PR 133 persist safe outage evidence. Comparing router event times with
the Pi's state is an owner-run capture ([outage dossier](outage-dossier-2026-10-04-2026-10-09.md)).

Other historical findings require fresh source confirmation before becoming
new tickets. The archived list retains all details and proposed fixes.

## Open fixes

In flight on 16 October 2026 (states are in the [roadmap](../ROADMAP.md)):

- PR 138 works the carry-forward list of the Panic batch: a rollback that brought back a
  revoked dashboard grant, a backup lock that delayed dashboard Panic and a stalled write of
  the stop records. PR 136 lands the combined Panic batch with one approved bundle and
  carries PR 92, 103 and 104. Both touch hash-pinned files and need the independent review
  and a matching approved bundle before merge.
- PR 139 fixes release tooling bugs that a real dry run of v0.2.0 found. PR 111 and 112 add
  signed release notes to the update status and name the version on the Updates button. PR 117
  raises the wheel build lock. They belong to the first release, v0.2.0, which is not cut.
- Not in flight and still open:
  - Every private request from a test-network peer still asks the helper for its own dashboard
    access status (`_check_device_access`). PR 86 bounded the wait and kept the token, but
    simultaneous requests do not share one answer and the sign-in retry after "Device
    verification is busy" is manual. The probe with four simultaneous checks has not been
    repeated on an installed Pi.
  - Names accept control characters and bidirectional overrides through the API and import.
    This is a log and display hygiene question ([QA report](qa-report-2026-10-09.md)). An
    HTTP/0.9 request still gets an answer with no status line. The browser fixture is not
    hermetic for update actions.
  - Loaded Pi capacity was never measured (feature proposal 20 in
    [feature proposals](feature-proposals-2026-10-09.md)).

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
not repeated after the look refresh of PR 115.

## Test fixture reliability

The first current Python 3.9 run observed an empty readiness marker before its
contents were written. The unchanged full retry passed. Both runs are retained.
PR 99 fixed it: a test child now writes the marker under a temporary name and
renames it into place (`tests/marker_support.py`), so a polling parent reads the whole
marker or none. No production failure was demonstrated by this fixture race.

See [final local verification](verification-final-local-release-2026-10-04.md).
