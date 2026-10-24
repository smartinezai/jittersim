# Router password check errors

The router password already describes its status text but failed checks have no linked error summary, invalid state or summary focus. Add those details for the existing check only. Keep the dark interface, entered password, Remember choice, retry limits and network behavior. Use the existing rejection decision so offline, helper, throttle and authorization failures never accuse the router password. No open product question remains.

## Scope and decisions

- Add a hidden focusable summary inside router-credentials with a link back to the password. Reuse existing error styling and the existing credential-router-status description. Add no live region.
- Keep a router-specific error helper and link handler. Clear errors on password edits, Clear password and a permitted retry. Show blank-input and rejected-password errors with invalid state. Other current failures get recovery text without invalid state.
- Preserve routerPasswordBlocked, routerCheckWaiting, routerRequest, routerAction and polling behavior. Keep all existing verification and rate-limit decisions.
- Guard the password check result with both password and admin-token revisions. Focus the summary only if the check began and ended inside the router credential controls or its disabled control left focus on the body. Background checks and departed focus remain undisturbed.
- Token code from the explicit eaa022f3 dependency stays unchanged.

## Verification

Add a unique offline Node harness that executes the served check and edit handlers through pytest. Check failed then successful input, blank input, same-rejected-password blocking, preservation of values, described error, link/focus behavior, retry cleanup, clear, throttle and helper/network/auth failures. Hold old replies across password and token changes to verify stale results cannot repaint or take focus. Run the unique test plus existing floating credential and router-copy tests through the shared wrapper with a private copied offline environment. No browser, Docker or full suites while release checks run.

## Risk and rollback

This changes only front-end error presentation and stale response ownership. There are no endpoints, new stored values, requests, auth policy changes or deployment steps. Reverting the two production files and unique test restores the previous behavior. Browser/screen-reader verification remains deferred for the owner gate.
