# Finish update status and page refresh recovery

## Review summary

Finish the reviewed online-check and polling work on frozen combined source
326f1f3. Load private update state after accepted sign-in and device access.
Keep later status reads bounded and reject old credential results. Recover
connection availability after an uncertain save without repeating the save.
Keep failed diagnostics pending for a later retry. Preserve private branding,
Wi-Fi recovery, graph sampling, focused controls and Panic access.

The existing update-path and page-polling plans were independently reviewed.
Their human feedback pauses are cleared by the current 24-hour authorization.
The main reviewer reviews this exact combined scope before implementation.
Root owns frontend adapters. Other owners retain preparation, certificates,
installer passwords and server concurrency. No live Pi changes are included.

## Source and sequence

1. Import the complete polling feature from 8402633113d60265805cdf9118c3ac2d7da371be
   by a normal local merge. Review every conflict against both current source
   and original feature behavior. Preserve all-method credential fencing and
   current request options, reconnect, brand and graph behavior.
2. Retain original failing browser evidence and reproduce the relevant defects
   against the combined served page. Add actual-function regression checks for
   failed status retry, stale credentials, successful availability after an
   uncertain save, failed deferred diagnostics and overlapping refresh scopes.
3. Remove the construction-time private online read. Reload after accepted
   access through fresh, remembered and enrolled sign-in. A failed online read
   remains unknown and retryable. Reading status never starts a GitHub job.
4. Read completed daily check state and staged releases from the Updates page.
   Use known health version when the release record has no installed label.
   Keep one physical request per status category and bounded waits. Reject
   responses after credential revision or access loss. Never replay a write.
5. Repair deferred diagnostics retry and connection availability separately
   from the unconfirmed outcome of a timed-out save. Register the actual
   polling browser journey in the current workflow.
6. Review the complete patch independently. Save focused local commits and
   exact evidence. Hand the frozen source to the integration owner. Final
   configured workflows run on the final combined release before publication.

## Verification and recovery

Use real served JavaScript and real browser fixture journeys. Exercise manual
and remembered sign-in, enrollment, changed tokens, revoked device access,
failed read retry, daily staging, hidden tabs, save timeout and Panic during
Apply. Run affected Python/Node checks and configured lint. No new runtime
dependency, stored data, database migration or authentication endpoint is added.
Browser status remains private. Reverting source restores prior behavior.

Final verification uses installed wheels, all supported Python versions,
clean Linux builds and every configured browser/kernel/release/GUI command.
Billing-blocked hosted jobs are recorded honestly and replaced locally under
human authorization. Main publication is separate from physical Pi deployment
and its independent recovery requirement.

## Concrete independent review requirements

Every asynchronous success, error handler and final control cleanup must check
its credential revision and current accepted device access before changing
private state, controls or starting a following request. Hold old promises
through replacement credentials and access revocation in regression checks.
Check late rejection and final cleanup as well as a late successful answer.

A successful availability read only establishes connectivity. Keep a timed-out
save outcome explicitly unconfirmed. Its POST count remains one through
recovery, page changes and subsequent status reads. No read marks the save as
successful and no recovery path repeats it.
