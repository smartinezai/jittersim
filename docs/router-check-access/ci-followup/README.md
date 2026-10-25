# Executed CI failures and bounded follow-up

Hosted CI **did execute** on PR252 head
`80029b962370c26b646ac68cc74f2ce44e3a9c2c`. Runs
[40137197832](https://github.com/smartinezai/jittersim/actions/runs/40137197832)
and [40137197807](https://github.com/smartinezai/jittersim/actions/runs/40137197807)
passed lint, Browser journeys and GUI baseline. Python 3.9/3.11/3.13 and Pi Linux
failed the stale floating-credential status assertion; Python 3.9 also found the
CSV defect below. Pi browser stopped in `group_devices`; Homepage reference
stopped in `dashboard.cjs`; later steps in those jobs did not run. Linux datapath
was cancelled. These are executed results, not a billing-blocked run.

The earlier raw receipts remain intact. Local runner summaries copied here also
retain their generated blanket claim that hosted CI could not start; that claim
does not describe the actual runs above. No successful hosted rerun is claimed.

## Corrections and evidence

- The router status is now the plain text `Not checked`, with a separate native
  Check button. The credential fixture expects that exact status and retains its
  zero-timer and zero-network-call assertions.
- Python 3.9's CSV writer did not quote a carriage return when only LF was a
  record terminator. The actual unchanged baseline exporter reproduces the
  reader error in [csv-python39-baseline.log](csv-python39-baseline.log).
  CSV commit `86828a3e` uses standard CRLF records so both newline characters
  trigger quoting. It preserves the embedded CR/CRLF data and existing formula
  prefix neutralisation. No CSV assertion was weakened. The three added pytest
  cases and the [nine-case program](csv-regression-program.py) cover literal
  CR, internal CR, CRLF and all six formula prefixes. The same program passes
  against source on [Python 3.9.6](csv-python39.log) and
  [Python 3.12.13](csv-python312.log). This is not a full Python 3.9 suite run.
- The group journey's own success toast covered the next phone action. The
  [original local failure](group-baseline.log) reproduces the hosted interception
  at another group action. The fixture now checks the exact successful save
  message and uses its real Dismiss button after each save. Membership,
  validation, privacy, keyboard and geometry assertions remain unchanged;
  there are no forced clicks or added sleeps.
- Phone admission already closes credentials to focus Allow this device. The
  homepage authentication fixture now closes the panel only while it remains
  visible, then uses the existing admission control. The
  [original phone case](homepage-baseline.json) reproduces the hidden Close
  timeout. The [corrected existing matrix](homepage-fixed.json) passes all
  **36 cases** at 1440px and 390px, retaining privacy assertions.

The group/homepage fixture files are unchanged from PR base `b061d2d8` before
these repairs. The dashboard prefix containing authentication, toast and group
logic is byte-identical between that base and the original PR head; only the
later router-status code differs. Its digest and the tested file digests are in
[identity.json](identity.json). These fixture failures predate this PR's router
status change. The CSV exporter also matches that baseline before correction.

## Local verification

- [Affected native modules](native-summary.md): **211 passed**, no failures,
  errors or skips: floating credentials 64, connection-check mutation 22,
  core 63, API 62. Tests used the private installed Python 3.12 package; the
  installed exporter matches the corrected source digest.
- [Final group journey](group-summary.md): passed at desktop and phone sizes,
  including the exact success-toast assertions; 14 seconds.
- Homepage privacy: the existing `dashboard.cjs` script only, **36 passed**,
  33.7 seconds, under one of the four shared browser locks and a 180-second
  managed process-group limit. No extension or full homepage job repeat.
- Gating Ruff (`E9,F63,F7,F82,F811`) and `git diff --check`: passed.

Private cached Python and Chromium were used. Node Playwright 1.63.0 was restored
with offline, script-disabled npm installation from the existing local cache;
there was no external download. No full suite, full browser matrix, Linux/Pi job,
remote push, merge, signing, installed package or live router mutation occurred.
Full downloaded hosted logs and intermediate failed local receipts remain under
`test-results/router-ci-fixture-repairs` in the repair checkout.
