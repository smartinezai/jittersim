# Explicit recurring Traffic discovery capture

Start from frozen outcome UI747 on separate branch codex/recurring-discovery-scan.
Root owns backend/integration/release. Outcome and graph source stay unchanged.

One UI writer owns discovery HTML, scan lifecycle and its existing private-clear,
mode-availability and click hooks, plus unique scan unit/browser tests. A single
navigation lifecycle hook to syncObservationScanning in showPage is needed to
stop immediately on every navigation path; no other navigation change. No CSS,
backend, producer, outcome contract, pinned/networking/release/package edit.

Default OFF, never stored or auto-resumed. Start scanning explicitly starts one
actual POST /api/observations/scan using the existing bounded API. After each
completed capture/read, schedule the next capture 30 seconds later; failures use
60/120-second bounded backoff, and repeated failure stops for explicit restart.
One in-flight identity prevents manual/recurring overlap. Stop clears the timer
and invalidates current delivery ownership, while the existing server capture
may finish within its deadline. Start stays unavailable until that flight ends.
No abort/kill claim or new architecture. Progress/result/error goes to a plain
capture-status paragraph, not recurring toasts or a new live region.

Require verified admin, unlocked private dashboard, helper, visible document and
Traffic page before every POST and timer schedule. Page/tab hide, navigation,
private clear/access loss/token change stop the loop permanently; returning does
not restart it. Late capture/list results may not restore private state. Existing
GET saved polling remains independent and is never called an actual capture.

Tests: fake-clock exact POST cadence/completion/backoff, Stop, no overlap, identity,
auth/helper/visibility/navigation/privacy and stale completion/error. Preserve
existing new-name/comparison assertions using the adapted harness; browser adds
actual recurring POST counts and Stop/navigation/lock checks at desktop/phone to
one affected observation_confirmation journey via shared wrapper. Keep all seven
strict metrics/definitions/tolerance unchanged. Freeze and send exact source to
root for independent review; no push/merge/deployment/signing/Pi action.
