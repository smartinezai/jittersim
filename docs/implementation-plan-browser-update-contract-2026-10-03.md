# Align the complete browser journey with update recovery

## Review summary

The frozen release passes the full Python 3.13 suite but its complete browser
journey retains expectations from an older update interface. Keep the reviewed
application behavior. Update the journey to distinguish automatic checks at
sign-in from local status reads while Updates is visible. Retain exact request
budgets and prove that uncertain preference saves are not repeated. Preserve
confirmed installed versions when a later status has no version. Simulate real
identified update phases when testing successful completion.

Risk is limited to verification. No production code, credentials, stored data,
package dependencies or deployment behavior changes. The current failing log
and full Python receipt remain attributed to source 51b96e6. Review this plan
before editing the journey under the existing 24-hour implementation authority.

## Change and verification

1. Keep the initial one-read sign-in check and no automatic install assertion.
2. Test disabled or unknown automatic-check preferences while the default page
   is visible. Separately require one status read on entering Updates. Control
   browser time around request-budget assertions so an ordinary five-second
   status refresh cannot race those assertions. Resume normal time afterward.
3. For a failed preference POST, verify one write and a read of the actual saved
   value. For failed reads, retain disabled controls and an explicitly unknown
   result. Manual checks remain possible without inventing a saved preference.
4. Match actual release failure messages and retained installed-version behavior.
   Use the current identified installing/complete protocol for completion checks.
   Keep confirmation, request identity, page errors, phone and desktop checks.
   Preserve the Wi-Fi reset's one-request check with a bounded wait for its
   asynchronous route callback and use the current default network name TestNet.
5. Execute the actual served complete journey and the update recovery/polling
   browser modules. Review the complete test diff independently before freezing.
6. Build the final wheel and run all configured browser, Linux, Python-version,
   kernel, package and GUI checks. Reuse a previously completed check only after
   proving all of its execution inputs are byte-identical. Preserve its original
   source identity and document the exact mapping, never relabel old receipts.
7. Assess the Linux job deadline from completed clean-build/full-suite/packet
   timings. Any bounded deadline-only workflow fix receives its own review.

## Failure recovery

Keep each failed attempt and its current source. Stop on a real application
regression instead of relaxing assertions. The browser fixture change can be
reverted by removing its focused commit. No live Pi action is part of this fix.
