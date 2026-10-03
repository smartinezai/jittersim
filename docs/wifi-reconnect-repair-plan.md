# Wi-Fi reconnect repair plan

## Human review summary

PR #67 needs repairs before integration. Its retry limits exclude request time. A lost save reply can confirm another browser's change. Late replies can clear newer form edits or restart a token check after credentials change. Server workers can proceed after a refused reservation or begin a restart after shutdown.

Keep each Wi-Fi write single-use. Confirm its outcome through reads tied to that exact request. Give the whole task a fixed elapsed deadline with request cancellation. Ignore old work after credentials or form state change. Reserve Wi-Fi work before accepting it and stop queued work during shutdown. Preserve the current phone controls when carrying the change onto the integration branch.

Risk is high because this changes the network used to reach the Pi. No database migration is needed. Physical Wi-Fi recovery remains unverified. The release coordinator must review this plan before production edits and retains integration and deployment ownership.

Open questions: agree the request identity lifetime and repeated-request response contract. Agree whether an already-started helper change finishes during service shutdown. Existing callers of synchronous Wi-Fi APIs must be checked before response changes.

## Reviewed source

- PR: https://github.com/smartinezai/jittersim/pull/67
- Read-only source: `feat/wifi-restart-reconnect` at `053201e729cf170466d6cd04df8bed11047443e5`.
- Integration: `0c8abd4b499900799e0614d2901d2b9eb4078f33`.
- Review branch: `codex/wifi-reconnect-review` starts at that integration commit. Only this plan and evidence are added.

## Findings that block integration

1. **P1: waiting is not bounded by elapsed time.** `reconnect.js` lines 44–59 count sleeps rather than request time. Lines 66–119 await requests without cancellation. Lines 280–298 reopen retry cycles indefinitely. `server.py` lines 7756–7782 cannot enforce the router deadline while the nested wait runs. Actual source reproductions show a 90-second retry taking 730 seconds and router confirmation continuing beyond 403 seconds. A never-resolving probe stays pending even after its limit passes. Use one monotonic deadline across requests and waits. A timeout of a write means its result is uncertain and must never cause another write.

2. **P1: lost reply confirmation is not tied to the submitted change.** `reconnect.js` lines 251–275 select the latest operation using only its timestamp. A reproduction submits an AP save once then reports a different browser's reset as success. A bounded request identifier must connect acceptance and outcome. If the match cannot be established, show an uncertain result and offer safe reads. Do not replay the write.

3. **P1: late work changes newer state.** `server.py` lines 7490–7550 clear drafts and passwords after an old operation completes. The actual submit handler deletes a newer draft and clears its new password in the reproduction. HTTP 202 acceptance also clears the submitted draft before completion through `request()`. `reconnect.js` lines 223–235 and 316–319 have no credential generation guard. An old waiter starts a token check after the token revision changes. Carry credential and form versions through callbacks. Cancel old readers and ignore late replies. Retain drafts until the exact submitted change is confirmed. Deduplicate resumed followers and Check again readers. Background recovery must not force delayed page navigation or reopen phone controls after the person moves elsewhere.

4. **P1: refused Wi-Fi reservation still runs router work.** `server.py` lines 12344–12374 continue when `WifiOperations.begin()` returns None. The separate preflight busy check does not reserve the operation. A reproduction proves the router callback runs while a manual change holds the slot and only the manual outcome is remembered. The helper's final network lock limits actual network writes. The demonstrated defect is incorrect admission and outcome tracking. Reserve before acceptance then release reliably on every finish or failure. Cover both background and synchronous paths without silently breaking existing clients.

5. **P1: a queued restart begins after shutdown.** `wifi_operations.py` lines 98–105 sleep then run work without checking the closed state. Lines 121–123 only mark closed. Closing immediately after acceptance still starts the work in the reproduction. Prevent queued work from starting after close. Define a bounded policy for work already inside the helper and retain an honest uncertain outcome when completion cannot be known.

## Ownership and integration

Reconnect ownership covers `reconnect.js`, `wifi_operations.py`, AP completion handlers and router reconnect callbacks. The update owner keeps HTTPS upload waits. Their later polling repair owns `notePiAnswered()` and `readOldAdminTools()` retry behavior. Their online update work owns `workflows.js` online checks and a guarded successful-sign-in hook. Coordinate shared `request()` timeout and accepted-draft changes before editing. Preserve authentication checks and token revision checks.

Do not replace the old dashboard wholesale. Integration has phone navigation focus, a controls sheet and newer chart interactions. PR #67 does not change `showPage()` directly. Retain the integration implementation and test actual phone actions rather than only resizing after desktop actions.

## Implementation sequence after review

1. Agree shared request and sign-in boundaries with the update owner. Carry the PR onto the current integration source in an isolated checkout.
2. Introduce bounded request identity and atomic Wi-Fi admission. Keep stored outcomes bounded and password-free. Refuse conflicting work without invoking the helper. Define shutdown behavior.
3. Add one elapsed deadline and cancellation for recovery readers. Keep the write separate from retries. Treat lost write replies as uncertain until an exact outcome is found.
4. Guard credential, draft and page state. Allow at most one recovery follower per operation. Preserve current phone navigation and saved sign-in behavior.
5. Add focused regression coverage for the reproduced failures. Review the complete change for resource bounds, secrets, API compatibility and shutdown races.
6. Run local checks on the final source then publish only the reviewed source. The coordinator owns the final combined branch, merge and Pi release.

## Verification and release gates

The three existing focused modules passed locally after enabling their loopback server fixture: dashboard reconnect, Wi-Fi operations and AP drafts. These tests do not prove the new failure cases safe. The source-level reproductions use actual functions with controlled clocks, promises and helper callbacks. See `test-results/reconnect-repair-2026-10-03/read-only-review/`.

Required new checks include stalled headers and bodies, slow requests, repeated healthy probes with failed result reads, token changes during recovery, newer drafts while an old operation finishes, duplicate resume calls, conflicting router/manual work and shutdown during the delay. Count writes to prove each mutation is sent once. Confirm failed registration still requires device permission and public or unauthenticated requests cannot read operation details. Never store or log Wi-Fi passwords, admin passwords or tokens in outcome records.

Run desktop and phone browser journeys for AP save, reset and router copy across disconnects. Exercise taps, controls-sheet visibility, focus, Back navigation and a delayed reply after changing pages. Check reduced motion and banner controls. Inspect installed wheel assets, script parsing and content security policy behavior in a clean environment.

Run every check from the final integration workflow definitions, including supported Python versions, lint, clean Linux builds, installer and network checks, browser journeys and other enabled release jobs. If GitHub limits prevent hosted jobs, perform their intended commands locally and retain exact source identifiers, commands, results and artifacts. Report hosted checks as unavailable. Do not replace Linux or network checks with weaker macOS tests. No Docker runs are started during this read-only review.

Before physical Pi testing, the coordinator must establish independent management access and a bounded recovery route. Record current Wi-Fi settings without exposing secrets. Verify actual disconnect, rejoin, outcome reads and restoration. A software rollback alone does not restore changed Wi-Fi credentials. If recovery fails, use management access to restore known settings before repeating any change.


## Agreed implementation decisions

The dashboard obtains a signed request ID through an authenticated read before sending a change. Issuance changes no network settings and carries no password. The ID uses Pi time advanced by elapsed time and belongs to the current process generation. Its acceptance window is five minutes. Earlier process IDs are refused after a restart even while their original time window remains open. A missing exact result stays uncertain.

Exact Wi-Fi outcomes have a cap of 64 and one hour of elapsed retention. New work is refused at capacity instead of removing replay protection. Router outcomes keep the existing four-minute read window with at most 64 request records retained for one hour. Neither store carries a password. Repeated IDs never start another worker. Legacy synchronous AP requests keep their complete original helper response.

Worker admission and shutdown use the same protected state. Failed thread creation cancels queued admission and records a failed exact outcome. Work already admitted keeps its slot until the bounded helper finishes. Close reports that unfinished work as uncertain. Router thread creation is protected as well so a failed router copy cannot strand a Wi-Fi reservation.

Request timeouts include headers and response bodies. Recovery uses one elapsed task deadline and abortable reads. Automatic token recovery performs at most one follow-up sign-in check rather than starting fresh retry cycles. A stale response is rejected before any device-access lock, draft change or error-panel update. Submit button ownership prevents an old request from releasing a newer request's button. An accepted HTTP202 keeps the draft until exact confirmation. ID issuance cannot consume the pending submit button or clear its draft.

The coordinator reviewed this plan and authorized these repairs. The final verification record is in `docs/wifi-reconnect-verification.md`. Multiple control processes would require a shared bounded outcome store and shared generation rules. This change deliberately fails closed after a process restart instead of replaying a missing write.

The completed review follow-up also makes router-copy replay check its original action under the same lock as its credential context. Completed Wi-Fi outcomes cannot be rewritten by later refusals. Outer router callbacks discard obsolete results and errors after either credential revision changes. Both conditions have API or served-code regressions.
