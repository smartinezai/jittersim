# Update status and polling recovery verification

The production repair is independently reviewed at
43d2269c3959b56b211616accf6c15862bde0cee. The verified compatibility test
follow-up is db0cdf84726e979479c304eed8989097ef2e3532. Root combined the whole
branch by a clean local merge at 92129f71ef0b6f67b0922b7e2a592643d21801a7.
The four frontend and terminal production files exactly match reviewed
43d2269. The three runtime password files still match approved 563082b.

These are local Mac source and real dashboard fixture checks. They do not
establish final installed Linux release readiness or real Pi acceptance.
No remaining repair branch has been published or installed on the live Pi.

## Passing focused checks

- 429 affected dashboard consumer checks with no failures or skips.
- 47 final ownership, restore, terminal and connected-device checks with no
  failures or skips.
- 127 checks covering all eight root-owned compatibility failures from the
  completed broad run with no failures or skips.
- Configured Ruff checks and source whitespace checks.
- All ten real page request budgets, zero hidden-tab requests and eight
  behavior checks. Save refresh used 18 GETs against 39 for full refresh
  with exactly one POST and no unrelated diagnostics read.
- Desktop 1280 and phone 390 update journeys passed eight cases each with
  no page errors. Actual device enrollment and removal passed both layouts.
  Update status is not requested before accepted device access. Removal
  clears private command text and disables private update settings.

Failure-first cases demonstrated stale traffic and connected-device results,
old read responses overwriting settings, delayed online-check settings and a
lost confirmed installed version. The tests exercise the served functions.
A pending setting save disables both related controls. Lost save replies
trigger a later read without repeating the POST. A pre-restore response cannot
become a new restore result. Completion without an identified restore remains
explicitly unconfirmed.

## Broad check and remaining gates

The complete Mac source suite at 43d2269 ran 4,433 cases in 318.897 seconds.
It passed 4,375 cases, failed 19 and skipped 39 Linux kernel cases. Eleven
release publication and signing fixture failures are assigned to the update
owner. They need committed package version inputs and semantic expected
labels. Eight root-owned wording, input-boundary and request-support fixtures
were corrected with the 127 passing checks above. Production validation was
not weakened. The previous interrupted broad run remains incomplete evidence.

Final assembly still needs the independently reviewed preparation, installer,
certificate and concurrency changes. Run every configured supported-Python,
clean Linux build, installed-wheel, browser, kernel and release command on
that final combined source. Hosted account-limited jobs must remain recorded
as unavailable. Main source publication is separate from safe live Pi recovery.

## Retained evidence

The primary checkout retains 69 diagnostic files in
`test-results/remaining-release-repairs-2026-10-03/online-polling-diagnostic/`.
`diagnostic-report.json` records actual pass and failure counts plus production
hashes. `sha256-inventory.json` identifies every saved artifact. Retain through
10 October 2026. Original failure-first cases, failed browser attempts and the
incomplete broad run are preserved alongside the passing results.
