# Repair the final Linux failures

The combined Python 3.13 run found a stale phone tooltip assertion and a real
deployment timeout race. Preserve the failed run. Correct the assertion to
check the current positioned tooltip. Protect the watchdog decision from
termination. Use the watchdog completion status to report a successful timeout
kill. This avoids filesystem writes and preserves the actual command status if
the child already completed or termination failed. The parent waits for the
watchdog decision even after requesting its cancellation.

Use a controlled scheduling regression under each available shell to prove
timeout classification and preserve normal completion and failed termination.
Run the complete deployment tests and current phone/browser coverage. Review
the separate router form safeguard before the next source freeze. Rebuild
source bundles and wheels then repeat the complete configured local pipeline.
No live router or Pi writes are required. The changes are reversible commits.
