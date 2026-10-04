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

## Reviewed transport direction

The agreed public request is POST `/api/updates/retry` with exactly one
`retry_id` containing 64 lowercase hexadecimal characters. The API calls
`HelperClient.retry_update(retry_id=...)` once and returns 202 with the fresh
helper status under `status`. Known stale or busy admission refusals return
409. A missing or unsupported helper returns a plain unavailable response.
Existing token and accepted-device checks apply before helper dispatch.

Register this endpoint as an action so queue admission does not acquire the
scheduler configuration lock. Root owns the API workflow module and dashboard.
The concurrency owner owns the helper adapter. The backend owner supplies the
stable `request_id` and operation kind for status ownership before watchers
are implemented. Unknown fields and malformed identifiers never reach helper.


## Final status field names

The stable `request_id` is 32 lowercase hexadecimal characters, minted before
first preparation. `request_kind` is `install` or `rollback` and remains the
original choice through automatic recovery. Both fields accompany queue,
progress and terminal responses. A missing operation has null values.

`retry` is null or contains `id` (64 lowercase hexadecimal admission hash),
`request_id`, original `kind`, original `version` or null and `recovering`.
`retry_unavailable_reason` is a safe plain message or null. The dashboard
matches progress by the stable request identity. The admission hash is used
only to reject stale confirmations. Legacy status without identity never
becomes a confirmed completion for an identified operation.
