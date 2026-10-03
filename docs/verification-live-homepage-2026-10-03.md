# Live homepage verification

Implementation a80dd59a9c177ca747bc5a64a58f7598a5311be9 is saved locally on codex/homepage-live-plan-review.
It builds on integration 0c8abd4 through merge 151178d. The newer combined
release needs its own merge review and checks. Installed Chrome trust and
physical Pi acceptance remain open. No remote or Pi change occurred.

- Complete local Python checks: 3,256 passed with 39 Linux packet skips.
- Regular installed wheel: 509 focused checks passed. Its 67 source files
  match the tested source. Wheel SHA256 is
  17b9d13b8a3c2b898491c58a4abcaae3af0ce82bea1196b69ff94431783edbdb.
- Actual extension: eight checks passed in Chromium 148.0.7778.96 with
  substituted policy transport. Pairing stayed off. Off and tab closure
  cancelled delayed work. Credential refusal cleared pairing.
- Real Python HTTPS listener checks passed with normal certificate
  verification and untrusted-certificate refusal.
- The actual older package reader at 0c8abd4 accepted the automatically
  converted current database. Supported latest settings and every other
  table stayed intact. The version advanced once. A second run did nothing.
- Worker interruption checks cover all three admissions. Backup file and
  directory synchronization failures leave the current database unchanged.
- Workflow Ruff, full Ruff for new Python files, JavaScript syntax,
  installer syntax and whitespace checks passed.

The normal Chrome HTTPS fixture failed before pairing while waiting for
its native certificate chooser. The original error is retained in
[the receipt](homepage-chrome-trust-failure-2026-10-03.txt). That fixture
was removed. Separate extension and HTTPS passes do not establish normally
trusted extension fetch on the installed AP.

Hosted CI, the other configured Python versions, Linux packet checks and
live deployment remain unverified on this change. The new browser CI step
is registered but has not run in hosted CI. No shared Docker resource changed.
The central comprehension check is deferred to the readiness review.

Exact commands, runtimes, artifact hashes, skip reasons and remaining gaps
are saved in [the machine-readable report](verification-results-live-homepage-2026-10-03.json).
Deployment choices and integration overlap are in
[the release handoff](live-homepage-release-handoff-2026-10-03.md).
