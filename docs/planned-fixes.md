# Planned fixes

The exact October2 fix list is preserved in [the historical archive](history/2026-10-02-backlog-and-ux/planned-fixes.md).
This page describes accepted main `5e0fed2ccb2414373643f20db1df59aa2de25306` and tested candidate 6fb864f.
New feature ideas are in [feature proposals](feature-proposals-2026-10-09.md).

## Changes after that candidate (9 October 2026)

- Router Wi-Fi writes are permanently refused by the Pi helper and the dashboard
  switch is read only (PR 87, merge `3903581`). Read-only router status checks and
  copying the router credentials to the Pi remain. The original outage cause is
  still unconfirmed.
- The dashboard has a skip link to the main content, unique region names and a
  named actions column (PR 88, merge `bf334fe`).
- Dashboard token checks that meet a busy helper (branch
  `codex/dashboard-token-verification-fix`, PR 86) and persistent outage logging
  (branch `codex/outage-logging`) are complete and tested locally. Both change the
  privileged helper, so the updater refuses them until the combined helper and
  access modules have an independent Panic-consumer review and a matching
  approved bundle in `PANIC_APPROVED_SOURCES`.

The coordinated candidate repairs watchdog timeout classification, bounds
helper requests, preserves upload-timeout handling and refuses unknown legacy
router fields or write destinations. Manual router status checks prevent
background discovery from causing new login attempts. The precise Panic
producer and consumers have whole-source approval. Random ranges preserve the
existing relative-mode selection sequence.

Two browser checks needed updates when these changes were composed. Recovery
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
Persisting safe outage evidence belongs to the next batch.

Other historical findings require fresh source confirmation before becoming
new tickets. The archived list retains all details and proposed fixes.

## Observed accessibility improvements

The exact 6fb Linux scans completed but were observational. Eighteen states
reported moderate or minor findings. Twelve need a working keyboard link to
the main content, eight need distinct labels for repeated page sections and
two need text in an empty table heading. No serious or critical violations
were reported. These are separate follow-ups with their own source checks.

PR 88 added the skip link, unique region names and the named actions column. The
scan now reports no `landmark-unique` or `empty-table-header` findings. It still
reports `skip-link` in the 12 phone-width states, because axe treats the off-screen
sidebar links (`#devices`, `#router` and so on) as skip links and wants matching
element ids. The page keeps those hashes unmatched on purpose so Back works without
scrolling. Resolving that needs a navigation design change and stays open.

## Test fixture reliability

The first current Python 3.9 run observed an empty readiness marker before its
contents were written. The unchanged full retry passed. Both runs are retained.
A separate improvement should publish the fixture marker only after its contents
are complete. No production failure was demonstrated by this fixture race.

See [final local verification](verification-final-local-release-2026-10-04.md).
