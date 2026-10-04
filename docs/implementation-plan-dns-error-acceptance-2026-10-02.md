# Verify the connection-error feature before homepage bounce

## Review summary

Verify the existing DNS failure setting before adding browser navigation.
Use an independent checkout of integration commit `6aae9d8` on
`codex/dns-error-acceptance`. Keep production changes and deployment with their
existing owners. Samantha authorized independent planning, implementation and
testing on 2 October. These tests will change only disposable local fixtures.

Add a served-browser check for the real Save and Apply controls at 0%, 100%
and an intermediate percentage. Check reload, disabled access rules and a
failed Apply followed by recovery. Separately extend the real Linux DNS check
to prove that returning to 0% restores allowed answers without unblocking
saved site restrictions. The first feature causes connection errors rather
than homepage redirects. Browser and operating-system caches can avoid a
fresh DNS request so the displayed chance is not a per-page probability.

Risk is low for test-only changes. Running Linux checks requires a disposable
container with network privileges and a free shared Docker VM. No live Pi or
router changes are part of this ticket. No deployment or remote push is implied.

## Current evidence and boundaries

- `AccessOptions` validates an integer percentage from 0 to 100.
- `render_access_firewall` sends prompt errors for redirected test-network
  DNS. Management queries and dashboard sign-in are outside those errors.
- Existing browser coverage exercises 20% once. Existing Linux coverage
  exercises 0% and 100% but does not return to 0% in the same failure trial.
- Browser fixtures use the real dashboard, settings repository and helper
  protocol with a simulated command executor. They prove control behavior.
  Only Linux packet checks prove actual resolver and firewall behavior.
- The setting belongs to household access rules. It affects test-network
  DNS even while experiment profiles are stopped. Record this distinction
  rather than promising that profile Stop disables it.

## Proposed changes

1. Add a standalone served-browser journey without changing existing fixtures.
   Read initial access settings and restore them before closing the fixture.
2. Exercise 0%, 37% and 100% through the actual controls. Check saved and
   applied values through the actual API after reload.
3. Check invalid percentages at the API boundary and confirm that they do not
   overwrite the last valid settings.
4. Inject a helper Apply failure at the browser request boundary. Confirm the
   control recovers and a later successful Apply reports the current value.
   Label this as a simulated failure rather than evidence of packet rollback.
5. Extend `tests/kernel/test_access.py` with 100% to 0% recovery for UDP and TCP.
   Check management resolution while failure is active and preserved blocked
   names after recovery. Include ordinary resolver restart if already supported
   by the existing test harness.
6. Run the focused journey, the relevant existing unit checks and lint. Run
   the Linux case once the shared VM is free. Do not repeat unrelated suites
   that already passed unless a new failure exposes a wider concern.

## Verification and delivery

Keep fixture startup, page actions, HTTP requests and process cleanup bounded.
Do not collect credentials or browsing data in saved results. Use only local
example domains in resolver checks. Reuse the existing installed browser
runtime rather than adding a package dependency.

No build configuration, database migration or runtime service changes are
required. Existing browser and Linux test runners can invoke the added checks.
Registering the journey in CI is a separate integration change.
The source commit and any environment limitations must be recorded in results.
Leave live-client caching and Pi recovery acceptance marked unverified until a
safe recovery connection and the required clients are available.


## Local verification results

Base source: `6aae9d819f0fc91b391198ce2e2f63e31c26dd45`.

- The new `python -m tests.browser.dns_failure` journey passed at desktop and
  phone widths in 46.07 seconds. It covered 0%, 37%, 100%, reload, invalid API
  values, disabled household rules and recovery after a simulated Apply error.
- The 73 existing checks for access rules, models, resolver port, helper access
  and the access API passed in 5.43 seconds on Python 3.12.13.
- The new browser file passed the full configured lint check. The repository
  passed its focused CI lint check. The change had no whitespace errors.
- The extended Linux case compiled and one case was collected. It has not run.
  The shared Docker environment was in use so no container command was issued.

The browser used existing Python 3.9.6 with Playwright 1.60.0 and Chrome
154.0.8037.95. Its executor was simulated. CI uses a different Playwright version.
These results do not prove resolver packets, live-client caching or Pi recovery.
The Linux run remains pending until the shared VM is free. Coordinate standalone
browser registration with the integration owner before changing the workflow.
