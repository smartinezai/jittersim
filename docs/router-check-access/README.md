# Reachable router status check

The footer previously displayed “Check router Wi-Fi” as noninteractive text while
the enabled native action was inside closed Controls. A visible native Check
button now shares the original handler and exact helper/token/password/rejection/
busy gate. It reads whether router Wi-Fi is on or off; it does not switch Wi-Fi.
The On/Off/Unknown state remains a plain span, and the original Controls action
remains available. Existing status text describes prerequisites; no new live region.

Independent review cleared the initial source at `7aa7389f` and the scoped compact
CSS/test correction at `5b943e91`. No backend, credentials, rate-limit, late-response,
package/version or installed-device changes. No gate or helper bypass.

- `full-pass-summary.md`: the full affected router_copy journey and the new action
  matrix pass at `5b943e91`; local synthetic fixtures only.
- `offline-final.log`: 120 targeted router/credential/static-budget tests pass.
- `lint-final.log`: lint passes. All seven strict budget metrics are unchanged;
  no baseline, definitions or tolerance edits. See `source-identity.json`.
- Matrix covers 1440×900, 375×844 and 320×568. No status request without the
  password; pointer and keyboard each send one explicit status read. Both actions
  disable while that read is deferred, then restore. The state stays read-only,
  the button is at least 44×44px, Panic/Controls and status fit, and no horizontal
  page overflow is introduced.

The first full journey failed an old single-column/sidebar geometry assertion.
The same assertion failed on unchanged 1.0.0 source in the separately retained
`unchanged-source-grid-failure-*` receipts. It now checks containment in the actual
two-column Controls grid and clearance above the safety bar. Initial status
clipping found in phone captures was corrected with scoped text layout. Earlier
failed receipts and the first passing action-only matrix are retained, not labelled
as an entirely green full run.

Actual inspected synthetic captures: [desktop](check-1440.png),
[375px phone](check-375.png), [320px phone](check-320.png). They show sample status
replies and a fixture credential acknowledgement, not owner credentials, actual
router evidence or physical acceptance. No live Pi/router or extension changes.
All wrapper jobs terminated and this lane's slots were released. No broad release
matrix or new screenshot-polishing loop was run.

Subsequent hosted jobs did execute and found CI failures. See the
[executed CI follow-up](ci-followup/README.md) for the bounded fixture corrections,
the separately committed Python 3.9 CSV correction, and their local results.
Earlier raw receipts above are preserved as historical evidence.
