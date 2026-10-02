# Confirm observed-site profile restrictions

## Reviewer summary

Adding an observed website currently changes its saved profile to an allow list. A tab that remembers an old allow list can skip the question after another tab has made that profile unrestricted. The server then silently restores restrictions. Both single-name and group actions have this problem.

Require an explicit acknowledgment of the restrictive transition. Read the current profile and check that acknowledgment in the same SQLite write transaction as the save. The browser sends acknowledgment only after the existing in-page question is accepted. If the server rejects a stale action it returns the current profile. The page updates its remembered profile and asks on the next deliberate click. It never automatically repeats the POST.

The change is high risk because an incorrect transition could block other websites for devices using that profile. It needs independent plan and patch review plus real single/group browser and API regressions. The user has authorized plan execution and implementation for 24 hours. The release coordinator owns publication and requires green CI before merge. There are no open design questions that require another user decision.

## Scope and decisions

- Work on `codex/allowlist-confirmation` based on combined foundation `0c1f4f73708f23c33c53cca7dfb08e9a0a733b1f` in the reused isolated proxy worktree. Preserve all original branches.
- Keep `/api/observations/add-to-profile` and the existing single/group payload forms. Add `confirm_allow_only` as an optional strictly boolean field. Missing means false.
- Inside `ConfigRepository.transaction()` reread the profile. If its current mode is not `allow_only` require `confirm_allow_only` to be true. Otherwise return HTTP 409 with a specific error code and the current profile without saving anything.
- Preserve normalized names, deduplication, the 64-name batch limit and the 100-name saved-profile limit. Do not alter unrelated profile fields.
- An already restricted profile remains usable by existing clients without an acknowledgment. Older clients cannot silently make an unrestricted profile restrictive.
- Both dashboard paths record whether the existing in-page question was actually accepted. Send that boolean with the mutation. Decline and dialog failure send nothing.
- Propagate the structured conflict from the request helper. For this specific conflict update the matching cached profile and show the server message. Keep the button available for a deliberate second click that can ask for confirmation.
- No preflight refresh and no automatic mutation retry. Use the conflict response directly so no extra read or polling loop is needed.

## Atomicity and failure handling

`ConfigRepository.transaction()` starts `BEGIN IMMEDIATE` before the profile read. This prevents another repository connection from saving a mode change between the checked read and the save. Existing scheduler exclusion remains unchanged. The transaction contains only bounded local database work and no helper call or network operation. Failure rolls it back. A conflict must leave the profile, configuration version and saved history unchanged.

The acknowledgment records permission to make this restrictive transition. It is not a substitute for bearer authentication or device authorization. Existing endpoint access checks remain in force. The returned current profile contains the same information already available from the authenticated profiles API.

## Verification

1. Save this plan and obtain independent router and transaction review before implementation.
2. Add regressions proving the old source accepts the stale single/group action without confirmation.
3. Verify the API rejects an unrestricted profile with absent or false acknowledgment and accepts an intentional true acknowledgment. Reject strings, numbers and null. Preserve existing lists and other settings. Retain caps, malformed-name rejection and missing-profile behavior.
4. Use two connections to the same temporary SQLite database to prove another profile writer cannot enter between the checked read and save. Verify rollback on a save failure. Keep test waits bounded and release all threads in cleanup.
5. Run actual served dashboard functions for both actions. Verify initial decline, confirmation, existing allow lists, a stale conflict, the next-click question and no repeated POST after a conflict or network failure.
6. Run real authenticated API and Chromium single/group stale-tab scenarios including intentional confirmation after rejection. Use the local fixture only. No physical Pi or Docker work in this repair scope.
7. Run affected API, storage, workflow and dashboard suites plus required lint and whitespace checks. Ask an independent reviewer to inspect the complete patch and failure cases.
8. Commit the focused ready repair and report exact source and results to the release coordinator. The coordinator runs the final combined Mac/Linux and installed-package checks before publication and green-CI merge.

## Deployment, rollback and growth

No schema migration, backfill, dependency or shared build configuration changes are needed. The existing package and deployment paths carry the server and embedded page together. A stale cached page is safely rejected after upgrade until reloaded or updated from the conflict response. Rollback is an ordinary package rollback and restores the older behavior, so release checks must include the safety regressions.

The additional work is one short transaction and one bounded conflict response. It creates no background task, unbounded retry, queue or extra request. The same rule applies across concurrent tabs and independent database connections. GitHub CI is currently blocked before job steps by account billing. This external block is not test evidence and remains a mandatory merge gate.
