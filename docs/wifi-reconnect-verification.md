# Wi-Fi reconnect verification

The reconnect repair has passed local verification. Independent full-change source review approved the repair and the follow-up checks. Combined Linux release checks and physical Pi recovery remain the coordinator's gates before publication.

Reviewed starting source: PR #67 at `053201e729cf170466d6cd04df8bed11047443e5`, carried onto integration `0c8abd4b499900799e0614d2901d2b9eb4078f33`. Initial application commit: `7b6b0ac41fca70a3f078a5527a66354102a57b3b`. Final source and test fingerprint: `9dc671736e63449a55ad0a441735e0c3fe31fd724f137dc0447a05a89d93ad1b`. The retained manifest lists each file hash.

- Python 3.12 local suite: 3256 passed, 39 skipped and 835 subtests passed. The skipped cases require Linux networking and are not claimed as passed.
- Focused lint: `ruff check --select E9,F63,F7,F82,F811 src tests` passed. Git whitespace checks passed.
- Reconnect script and installed dashboard script parsing passed in Node.
- Isolated wheel build passed. All 66 packaged files match source. Wheel SHA256: `b81f16e099710b502c248ecd1bf2ab69ae8f49ca9da9f9f8909da8886f3c5fdf`.
- Browser reconnect journeys passed at desktop width 1280 and phone width 390. Both cover dropped result reads, a genuinely lost acceptance reply with one write, retained sign-in, reload notice, newer form edits and delayed navigation. Phone checks exercise the controls sheet and retained focus.
- Existing Wi-Fi recovery, token memory at desktop and phone widths, and dashboard navigation journeys passed. No script exceptions were reported by the reconnect journey.
- New regressions cover request time, hung bodies/probes, wrong operation IDs, stale POST403 before access locking, HTTP202 drafts, newer edits, process restart, count/retention limits, worker-start failures, partial starts, shutdown and complete synchronous responses.

A literal source-text check for the old submit-button finally line was removed. The actual request path now checks button ownership and the recovery tests exercise its consuming behavior.

Evidence is retained under `test-results/reconnect-repair-2026-10-03/` with browser screenshots and traces under `test-results/`. Browser artifacts use fixture credentials only. The known local favicon404 remains a fixture resource issue. It is not a script failure or a networking assertion failure.

No Docker, remote push, remote merge or Pi deployment was performed for this repair. Hosted checks may be unavailable under GitHub limits. The coordinator must execute the final workflow commands locally on the final combined source and record those results without claiming the hosted jobs passed.

The final browser test forwards the write once with a five-second test timeout, asserts its exact accepted ID then drops the reply. This keeps the interception inside the browser deadline and avoids a test callback race after cancellation. It changes no application behavior.


The follow-up checks refuse a router-copy ID used for another action, keep already completed Wi-Fi outcomes unchanged after later refusals and suppress an old router result or error after credential changes. Each reported issue was reproduced before repair. Focused follow-up tests passed then the full local suite was rerun on the approved source. The five approved application file hashes match the retained manifest.
