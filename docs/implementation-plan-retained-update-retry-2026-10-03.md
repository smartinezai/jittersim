# Retry a retained failed update

## Review summary

A failed update may keep its original request and recovery copy so a later
attempt can recover safely. The current public request path refuses every
retained request as already queued. Add one explicit retry from the CLI and
dashboard after the earlier work has stopped. Keep the same request and recovery
identity. A retry must not stage a new release or replace the recovery copy.

The update owner handles the manager, helper and public backend contract.
Root handles the dashboard action and regression checks. The main reviewer
checks the exact contract before implementation and final source afterward.
The current human authorization clears a separate plan-feedback pause.

## Required contract before frontend implementation

The backend must report whether a retained request can be retried plus its
immutable identity and operation kind. The identity must exist before any
service is stopped or preparation fails. Retry admission checks the identity
under the same operation lock and refuses a replaced or newer request.
Unconfirmed running commands keep retry unavailable. A legacy manager that
cannot establish these facts exposes no retry action.

The exact field names and transport command remain with the update owner.
The endpoint must require the existing accepted administrator and device
access. Its payload is bounded and can name only the retained request. A
second submission must not create another request or recovery copy.

## Frontend behavior

1. Read current private status through the existing bounded category owner.
2. Show a plain Retry action only for an identified retained operation.
   Explain an unavailable retry using the backend's current safe status.
3. Require an explicit confirmation with the existing recovery connection
   reminder. Check accepted sign-in and current device access after every wait.
4. Confirm the request identity again before sending one POST. The backend
   independently rejects stale requests. Never replay a lost POST response.
5. Follow the same request's status without claiming success from an unrelated
   complete operation. Changed credentials and revoked access discard old
   success, errors and final control cleanup.

## Verification and recovery

Use real served functions with held promises plus actual browser fixture
journeys. Cover preparation failure before service stop, recovery failure,
unknown command ownership, eventual confirmed command completion, a changed
request during confirmation, double click, lost POST reply and access loss.
Prove a public CLI retry reaches the same saved request and recovery copy.
Include old-release rollback startup compatibility for retained Panic state.

No automatic retry loop, new token store or release download is added. The
new contract remains optional for older managers. Exact final source passes
configured lint and the complete combined installed Linux workflows. A failed
retry retains the original recovery information. Publication and live Pi
installation retain the normal source and independent recovery requirements.
