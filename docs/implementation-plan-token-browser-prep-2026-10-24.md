# Token error browser regression preparation

Extend the existing disposable dashboard access journey to verify the token error behavior in a real DOM at desktop and phone widths. Reuse its local helper and API fixture. Page routes can safely return service and helper contention failures or abort a request without contacting hardware. The main risk is automatic recovery racing assertions, so hold health probes during the offline assertions then release them to test automatic retry focus. Browser execution remains deferred until the release owner opens that gate.

## Scope and decisions

- Claim only `tests/browser/dashboard_access.py` plus this unique plan and a local report in `test-results/token-browser-prep/report.md`.
- Start from detached commit `eaa022f3c74b199fca267d81d871be82714b08ce` in `task-11/token-browser-prep-worktree`.
- Extend the existing wrong-token case with visible summary, HTTP 401 invalid state, preserved token and remember choices, focused summary and linked-field focus assertions.
- Inject bounded service, helper contention and offline failures at the existing access-status read. Verify that these keep the token valid and preserve the same values.
- Let real offline recovery retry against a service failure while focus is outside the token form. Verify that the summary appears without moving focus.
- Remove routes and submit the fixture's accepted token. Verify error cleanup after editing and after acceptance before continuing existing enrollment coverage.
- Do not change production files, shared scripts, fixture infrastructure or shared documents. No new endpoints, stored production data, external requests or deployment changes.

## Review and validation

Read the complete patch independently after implementation and check route cleanup, finite waits, focus timing and preservation of the existing journey. Run only bounded offline static lint through the shared wrapper with a private copied dependency environment. Do not start a browser, Docker, full test gates or any owner runtime. Record unrun browser validation explicitly and commit locally without pushing.

## Rollback and open questions

Revert the focused test commit to roll back. The deferred browser run must establish real DOM focus behavior before release. No other product question or migration is involved.
