# Wait for connection-check settings before editing

## Summary for review

The connection-check dialog accepts edits before its first settings response.
A late response can overwrite an entered probe count with the default 100.
The hosted browser check observed 10020 after trying to enter 20. A controlled
source check separately proved that a delayed response changes 20 to 100.

Keep the probe field and Start unavailable while the current dialog settings
load. Enable them only after the current response establishes the limits and
initial value. Keep Close available. This is a small risk B display-state
correction with low household risk. No API, helper or probe behavior changes.
Human feedback on this independently reviewed plan is required before coding.

## Work

1. Use the existing dialog, loading text, serial checks and request deadline.
   Prevent editing and starting during initialization. Do not add a sleep,
   a new retry loop or a separate service. A failed load must not enable Start
   with missing or stale limits. Reopening can use the existing load path.
2. Preserve in-progress checks, result history, cancel, focus return and
   protected-device behavior. A late response from a previous device or
   sign-in must not re-enable or repopulate the current dialog.
3. Add a controlled delayed-first-response browser regression. Verify the
   visible dialog cannot edit or start while loading. Then release the real
   synthetic response, verify the default, enter 20 and assert both the field
   value and submitted probe count are 20. Also delay the protected-device
   reopen that exposed the hosted failure. Test a previous device's response
   and a previous sign-in response arriving after a newer dialog opens.
   Neither may enable controls or overwrite settings in the newer dialog.
   Retain every existing result and privacy assertion. Do not repair only the
   fixture wait and hide the bug.
4. Run focused dialog state tests, lint and the complete affected connection
   journey at its existing desktop and phone sizes. Compare normal and
   loading/error states and exercise keyboard Start and Close.
5. Independently review the full diff before publishing the branch. Follow
   the real hosted result. Do not classify the earlier failure as billing or
   job cancellation.

## Evidence and boundaries

`api/connection-check.js` shows the modal before awaiting the initial load.
The load later assigns the default even if editing has begun. The initial
fixture path waits for settings but the protected-device reopen does not.
The exact-source reproduction is retained in
`/private/tmp/jittersim-connection-check-load-reproduction-2026-10-25.js` with
its JSON result beside it. The exact 10020 browser interleaving is inferred
from Playwright's separate selection and insertion operations, not claimed
as a deterministic browser reproduction.

Likely files are `api/connection-check.js`, its focused tests and the existing
`tests/browser/connection_check.py`. Preserve styling and the number limits.
No new endpoint, dependency, stored data, logs, migration or build input is
needed. Existing authentication and private-state clearing remain required.
Requests and memory stay bounded by the current dialog lifecycle. Deployment
uses the usual signed package. Reverting the commit restores prior behavior
without changing saved results or network policy.
