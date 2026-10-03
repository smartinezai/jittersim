# Device address review repairs

## Review summary

The address move must keep dashboard access and traffic protection attached to the device the administrator selected. Review of `f5335b0474c27fc9926e98300da829edf9466f3d` against integration `0c8abd4b499900799e0614d2901d2b9eb4078f33` found three changes needed before merge. Clear private device details whenever credentials lose trust. Keep address history usable when a device returns to an earlier address. Distinguish a refused save from a saved change whose network update failed.

Keep the existing administrator token requirement and verified connection identity. Do not introduce another credential or accept the destination address from the browser. Preserve the integration branch's sign-in and request handling repairs when bringing this older feature branch forward.

This document records an independent source review. No production files or tests were changed during the review. The coordinator retains implementation and release ownership under the user's existing authorization.

## Findings

1. **P2: Private identity details survive token clearing or rejection.** The new card and address details sit outside `private-dashboard` at `server.py:1121-1148`. The token input listener at `7327-7342` calls `setPrivateDataPanels` but these fields are absent from its panel list and lock hooks. A signed-in page can therefore retain device names and addresses after clearing the token. `loadAddressMoves` at `2499-2506` also lets a rejected obsolete read hide a newer session's offer. Move and undo responses must not update a later credential session.
2. **P2: Returning to an earlier address makes Undo impossible.** The helper explicitly allows moving back to a replaced address at `dashboard_access.py:134-135`. After A moves to B then B moves to A, `save_address_move` leaves both history rows. The guard at `server.py:13115-13117` refuses each Undo because the other row names its destination as a source. Candidate filtering allows this sequence. History must describe a reversible current chain rather than a cycle.
3. **P2: A network update conflict can undo access after protection was committed.** `move_device_address` commits protection and history before calling `_saved_control_response` at `server.py:13086-13103`. The subsequent apply can return 409 at `13655-13660`. The HTTP handler at `14043-14047` treats every 409 as a refused save and reverses helper access without reversing the committed protection or move record. The two stores then disagree about the address that owns access and protection.

## Repair steps

1. Add one identity-panel clearing operation to every credential lock path. Hide the panels and discard their private content and saved offer. Check both the credential revision and current trust before handling a read or write response. An obsolete failure must not hide current results. A write already sent may have completed, so suppress its stale display without replaying it.
2. Define the history rule for a return to an earlier address. Reconcile superseded records before a new move becomes authoritative. Preserve latest-first Undo for an ordinary A to B to C chain. Keep history and stored access bounded to their existing 64-entry limits.
3. Return an explicit saved/refused outcome from the move operation. Reverse helper access only when the database change was refused before commit. After a saved change fails to apply, retain matching saved access and protection with an honest failure response and a retry path. Include helper rollback failure in the recovery outcome.
4. Inspect Undo against later changes to access and protection. If a destination was removed then independently registered again or its protection was changed, an older move must not silently delete or overwrite that newer decision. Choose a clear refusal or explicit confirmation based on the current saved entries. Avoid inferring ownership solely from the destination address.
5. Inspect recovery across the root-owned access file and SQLite store. Check interruption after helper save, database save failure and helper Undo followed by a database failure. Preserve enough bounded state for retry or a clear management recovery action. Do not claim these two writes are atomic.
6. Bring the feature forward onto the reviewed integration source without replacing newer authorization, sign-in, concurrency or protection code. Review the complete resulting change before publication.

## Verification

- Browser checks on a narrow phone view and desktop: show the identity card and replaced list then clear or edit the token. Repeat with a rejected recheck and lost device access. No old device name or address may remain visible or be restored by a delayed response.
- Delay an old moves read, change credentials and load a fresh offer then release the old success or error. Only the current session may affect the page. Repeat with delayed move and Undo responses. Ensure a mutation is never sent again automatically.
- Exercise A to B to A through HTTP and the real helper socket. Restart the stores and verify the agreed Undo behavior. Also retain A to B to C latest-first Undo coverage.
- Force a refused database save and confirm access is restored. Separately force a post-commit 409 during plan preparation and confirm the access file, protection entries and move record still agree. Cover an apply failure and successful retry.
- Exercise later registration and protection edits before Undo. Cover helper rollback failure and the interruption boundaries between stores using disposable files and a disposable database.
- Run focused access, identity and protection tests then the configured full local checks on the final combined source. Run hosted checks when available. When GitHub capacity prevents them, execute the configured pipeline checks locally and report that hosted checks were unavailable.

## Deployment and recovery

The existing table is created automatically and the helper file remains root-owned with atomic replacement. Check an existing configuration and access file through upgrade rather than assuming a fresh store. Export omits move history while database backup includes it, so check restoration against the separately stored helper access file. The schema and helper file must remain readable by the chosen recovery version.

No new external endpoint authority is needed. Names and addresses are read from saved entries or verified kernel observations. Keep them out of logs and clear them from the page when credentials lose trust. Keep the current request body, file size, device count and history bounds.

Do not deploy until the coordinator has a verified management recovery connection and final combined verification. If the release is reverted, restore the matching configuration and access state together rather than mixing stores from different points in a move.


## Revised ownership and recovery design

Use a random 32-character move identifier as operation evidence. It is not a credential and grants no access. The browser still sends only the old address. Only the server passes operation evidence to the authenticated helper connection.

- Root access entries gain optional `owner_id`, `replacement_id` and `undo_id` fields. A new destination has the move's owner ID. The replaced source records the same replacement ID. Undo requires both to match, removes the destination and writes an Undo receipt on the restored source in the same atomic file replacement. Removing then re-registering a destination clears that ownership, including when both writes happen in one second. Missing identifiers on legacy replaced entries allow reading but refuse Undo with a management recovery message.
- SQLite move records gain `move_id`, `phase` and `previous_protection_owner`. Phases are pending, saved and undoing. Add an optional owner ID to each protection row. Ordinary protection saves clear that ID. A move claims its new protection row with its ID. Undo requires that claim and an absent old protection row before touching either store. Restoring an earlier chain step restores its owner only when that earlier move record remains present.
- Persist the pending move record before asking the helper to change access. Bind the helper request to the server-observed destination and generated move ID. A fresh helper identity mismatch refuses mutation. A failed or timed-out helper reply keeps pending evidence rather than assuming nothing happened. Protection and the saved phase commit in one SQLite transaction after the helper response. A saved response includes an explicit saved flag even if live application returns 409 or 502.
- On a refused save, compensate helper access using the exact move ID. Successful compensation removes the pending record. Failed or uncertain compensation retains it with a recovery-required response identifying the old address, new address and move ID. Neither branch silently claims a complete rollback.
- Before Undo, prove current ownership and persist the undoing phase. If the helper reversal succeeds but the database write fails, keep the undoing record. A management recovery retry can prove the completed helper reversal from its receipt and finish the protection transaction. It must not repeat a completed mutation or accept an unrelated registration as a receipt. Normal Wi-Fi device authorization is retained, so loss of current access requires the management recovery connection.
- Returning to an earlier address removes superseded cycle records only inside the successful protection/history transaction. Do not prune pending or undoing evidence to make room. Refuse a new move at the 64-record limit unless reconciliation frees space. Scan at most the existing bounded access and move lists.
- Add schema columns through idempotent automatic migrations. Old records receive empty ownership IDs and cannot be undone on inferred ownership. Existing access file version 1 remains readable with strict validation of the optional identifiers. SQLite backup preserves claims and recovery phases. Settings export omits them and imported protection entries have no claim, so an old Undo refuses. A pre-feature recovery binary cannot read new root fields. Deploy rollback must use the matching upgraded recovery helper or restore the pre-upgrade access-file snapshot with its matching database. Do not promise compatibility with an older helper that rejects these fields.

The coordinator approved this scope under the existing human authorization. Main review of this revised design is required before production changes. The original Claude worktree remains untouched. Failing-first cases live in the isolated integration-based repair branch.


### Concurrency guards added after main review

Reserve both addresses against every pending or undoing record in the same short transaction that saves intent. Refuse overlapping moves and Undo. A same-pair retry returns the existing recovery identifier rather than replacing it or replaying the helper mutation. Saving a record must never replace an existing different move ID, including saved records.

Persist `prepared_version` from the repository configuration version before waiting for the helper. Recheck it inside the final protection/history transaction. This provides durable revision evidence for an original protection row whose owner ID was empty, including a same-field remove/re-add during the helper wait. Entering undoing stores its preparation version and recovery retains that version. Recheck ownership before and after the helper call. Keep SQLite transactions out of network waits. A later configuration change causes refusal and conditional compensation or a clear recovery requirement, never an overwrite.


## Implemented helper contract and verification

The repaired dashboard uses distinct `move-address-owned` and `undo-move-owned` operations for every move, compensation and recovery call. There is no legacy mutation fallback. Owned moves require a nonempty 32-character hexadecimal move ID, the exact verified destination and a strict boolean access-transfer intent. False intent prevents a transfer even when registration changes before the fresh helper check. Owned Undo retains the existing authorization and requires its exact ID.

Address writes start a new agreed access-trust generation and pause private reads without changing the token value. Existing reconnect readers are cancelled when available. Only a guarded token read follows the write. Private requests and token-check functions remain owned by the reconnect repair.

Independent final source review approved these repairs. Focused backend and desktop/phone browser verification are recorded in `verification-device-address-repair-2026-10-03.md`. Final combined checks and paired-store release recovery remain coordinator-owned gates.
