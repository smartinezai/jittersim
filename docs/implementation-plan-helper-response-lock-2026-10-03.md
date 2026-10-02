# Release the helper lock before replying

## Review summary

A completed helper request can make the next request fail as busy. The server writes its reply while it still holds the operation lock. A client can receive that reply and send its next request before the previous handler releases the lock. Two full Linux runs exposed this in different immediate follow-up requests.

Create the complete JSON response bytes with their trailing newline while the operation lock is held. Release the lock before writing those bytes to the socket. Keep the existing lock around the service operation and keep all wait limits. Verify successful replies and error replies with a test that deliberately pauses the old handler after sending its reply. Existing slow-operation tests must still prove that overlapping work is refused or waits within its current limit.

This is a small reliability repair with medium release risk because it changes shared helper request ordering. It changes no stored data, commands, authentication, settings or protocol fields. No open product decision is required. The current 24-hour takeover authorization covers implementation after independent plan review. The release coordinator owns the final combined review, publication and deployment.

## Evidence and scope

- First final Linux run: the service-address client received a busy error immediately after applying a plan.
- Second final Linux run: recovery after a failed apply received a busy gateway-status result and skipped its next scheduler tick.
- Both failures are consistent with `HelperRequestHandler._handle_request` sending the service result before its `finally` block releases `operation_lock`.
- The same ordering exists in the combined release candidate `934863b46c464250508ae64b1ff872e87caa5fb9`.
- Repair only this handler and add focused concurrency coverage in `tests/unit/test_helper.py`.

## Steps

1. Have a separate reviewer check this plan for concurrency and failure risks.
2. Add deterministic socket regressions for successful and returned-error replies. Pause response transport before writing and after writing. While transport is held verify that a read and Panic can finish when a connection slot remains. Check cleanup after service and serialization exceptions.
3. Capture the regression failure on the current source.
4. Keep service execution and response serialization inside the existing lock. Release it in the existing exception-safe path before writing the finished immutable response bytes.
5. Run the focused regression and existing helper read/write tests. Run the service-address and API recovery tests that exposed the race.
6. Run the complete local suite, required lint checks and an independent source review. Report exact results and the focused commit to the release coordinator.
7. The coordinator repeats combined Linux, browser and release checks. Merge remains gated on exact-head green CI. The GitHub billing failure currently prevents that gate.

## Concurrency and growth

The lock continues to serialize service operations. Reads still refuse immediately while work is running. Writes still wait only for the existing bounded interval. A slow client no longer holds the service operation lock after the service work has finished. The eight-connection cap, payload size and socket timeout bounds stay in place. Eight stalled connections can still fill those bounded slots. Availability checks require a free connection slot. Completion replies describe their completed operation even if a later operation starts before the earlier reply arrives.

## Security and data

The protected Unix socket, peer access rules, helper request validation and command limits remain unchanged. No new data is read, copied, retained or logged. No database change or migration is needed. Errors from service execution must still release the lock. Errors while replying must not leave a held operation lock.

## Deployment and recovery

Use the normal release installer after the full combined candidate passes its gates. No special environment command, migration or backfill is needed. The coordinator handles the Pi and must preserve its current configuration and management access. Roll back through the previous official release if final acceptance fails. No device action is part of this isolated repair.

## Verification record

Independent plan review confirmed the ordering defect and required protected response serialization, tests before and after transport, error cleanup coverage and explicit acknowledgement of the eight-connection limit. These refinements are included above. Record the deterministic before/after result, focused test count, complete local test count and independent source review before handing over the commit.
