# Phone dashboard access preparation

Check the recorded phone issue where “Allow this device” sits beneath the open
Credentials panel. Prepare a regression journey only when current source still
has that gap. Preserve the existing design and completed page-heading change.

## Scope

- Inspect the Credentials panel, token check and dashboard access registration.
- Reuse the existing loopback fixture with dashboard access required so that a
  valid token does not automatically enrol the browser.
- Prepare a phone journey that checks the Allow action is reachable after the
  token is accepted without forcing a click or clicking through a covering panel.
- Edit only tests/browser/phone_layout.py plus this unique plan and local evidence.
  Do not change production files while the router credential writer is active.

## Checks and limits

Inspect the existing fixture's isolation and enrolment controls. Use ordinary
Playwright clicks and prove the intended target receives pointer input. Check the
smallest supported phone and the existing font stresses. No browser, Docker or
full gates run during the owner release. Syntax and wrapper lint may run offline
using a private copied runtime. Record unexecuted checks clearly.

This preparation changes no runtime behaviour, private data, endpoints or storage.
Production ownership must be released before implementing a confirmed fix.
The chosen regression must be run against both base and corrected source when
the browser queue is available. If current source already offers a correct Allow
target inside the panel, record that evidence and stop without inventing a fix.

## Released source scope

The router credential writer has completed and source ownership is released.
The source inspection confirms that Allow remains outside the fixed panel and
the accepted-token branch does not expose it. Add a bounded handoff from that
branch to the existing Allow control. Keep registration dependent on the person's
explicit click. Preserve the token revision guard and do not close another task's
panel or move focus after the person has moved elsewhere. Add offline behaviour
checks for those ownership conditions and keep real-browser evidence deferred.

The released scope also includes the token-check call chain and manual click/paste
adapter. Carry explicit manual handoff intent through the existing queued check.
Combine queued manual intent without leaking it into a later background check or
another token revision. Restrict the handoff to phone layout. Current focus in the
token controls and an open panel are required, and moving focus or pressing outside
those controls while awaiting the result cancels the handoff. Remove temporary
focus listeners when the check finishes, including stale results. Preserve the
existing desktop flow. Update source-extraction markers in affected existing test
harnesses and the old phone access journey's manual-close expectation.
