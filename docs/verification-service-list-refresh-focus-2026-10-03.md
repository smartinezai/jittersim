# Service-list refresh focus verification

## Summary

The normal five-second Schedules refresh kept selected values but replaced
the focused service checkbox. The repair keeps surviving controls while
updating catalogue names, domain hints and order. Removing the focused service
moves focus to the next remaining position or Extra hostnames when none remain.
Live outage status still updates. Polling, authentication and writes are unchanged.

The source and tests at `3e3b069ed42e914594d457eda12967b6b00ba90f` passed focused
verification and a complete independent review. The final combined source
still needs its coordinated checks and actual green CI before remote merge
or deployment. GitHub jobs currently stop before execution because of account
payments or spending limits.

## Evidence

- The unchanged installed candidate from `9312bb3` failed the new browser
  regression after a normal poll. The Microsoft Teams checkbox remained
  checked but was inactive when focus was expected.
- The fresh repaired wheel passed the actual Chromium checks at 375px and
  1440px through the normal five-second timer. The final log contains only
  `Service-list focus checks passed at phone and desktop widths`.
- Checks cover unchanged controls and keyboard toggling, catalogue additions,
  removals, name and domain changes, reorder, outage-only changes, edits and
  caret movement during a pending read, focus fallback, read failure and
  recovery. No service-list writes or browser script errors occurred.
- Both test contexts are disposable. Their unfinished edits and response
  overrides cannot affect the original browser journey page.
- Existing dashboard and service-list API checks: 26 passed.
- Required Ruff checks, Python 3.9 syntax and whitespace checks passed.
- A test cleanup issue in Playwright 1.63.0's `Response.finished()` was found
  during the first positive run. Reading the response body instead produced
  the final clean shutdown. Shared browser dependencies were unchanged.

The installed dashboard source matches the immutable repair commit:

```text
Python: 3.12.13
Playwright: 1.63.0
Server SHA256: e6389c48ddbffe79809f0ce5b25b07618f93a34a9fc46235003fba051b0c355b
Wheel SHA256: 8e3ac982eac332be56969e3235d6431daa189ae675a38e1a72de001a05756ed5
Wheel bytes: 390739
```

Reproduce with an installed package in the isolated environment:

```sh
python -m tests.browser.service_list_focus
python -m pytest tests/unit/test_dashboard_service_lists.py tests/unit/test_service_list_api.py
```

The existing browser journeys also run both focused contexts, so current CI
wiring includes the regression. The coordinator owns the one final combined
installed-package, Linux and browser run. This patch adds no migration or
runtime dependency. Rollback restores the previous dashboard package then
reloads the page. Focus on rebuilt saved-list action buttons is a separate
improvement outside this edit-form repair.
