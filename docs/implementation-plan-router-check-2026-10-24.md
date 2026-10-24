# Reachable router Wi-Fi check

Reproduce with the existing synthetic router fixture and counted status requests:
missing password blocks checking; accepted password leaves the footer's Check text
noninteractive while the real button is hidden in closed Controls. No live router.

One writer owns the footer markup, existing render/binding and narrowly scoped
button CSS plus router regressions. Expose a native Check action beside the plain
read-only state, sharing the original disabled expression and pollRouterWifi
handler. Preserve the Controls action and all backend/password/busy/rate gates.
Use existing status text for prerequisites, not a new announcer. Verify desktop,
375/320px, pointer/keyboard,44px, no overflow/Panic obstruction and exact read-only
request counts, then affected unit/budget/lint, independent review and draft only.

Outcome: visible native Check shares the original utility action's exact gate and
handler. Unknown remains plain text labelled Not checked. Source5b943e91 has
independent final CLEAR;120 focused unit/budget tests and lint pass. Full affected
router_copy browser journey plus1440/375/320 action matrix passes in000452.
The old full-row geometry assertions also failed on unchanged1.0.0 source in000255;
updated bounds retain real Controls containment and safety-bar clearance checks.
A compact-status clipping defect found in initial captures was fixed in the same
bounded batch. Failed/pass receipts and synthetic captures are retained in
router-check-access/. No router mutation, backend, installed package or release change.
