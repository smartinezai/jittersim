# Prepare the update before stopping services

Build and verify installation inputs while the dashboard still runs. Save the
old immutable code before stopping it then take the final settings snapshot
after its writers have stopped. Publish a complete recovery generation with one
atomic pointer update. Keep the previous complete copy available throughout.

This makes failed downloads, missing build inputs and insufficient disk space
fail before the outage. It also removes the gap that currently deletes the old
backup before publishing its replacement. Installation and recovery share a
finite service allowance. Recovery must remain independent of the installed
application package. Samantha already approved implementation and publication
after passing checks. The independent reviewer considers this concrete design
before these high-risk source changes.

## Ownership and compatibility

The update owner changes package preparation, backup publication and the broader
`apply_update` flow. The CA owner changes dedicated current-certificate keep and
restore functions and their call before restart. The Wi-Fi owner changes the
AP credential block in `install.sh`. The concurrency owner changes dashboard,
helper and scheduler shutdown. Do not edit those blocks concurrently.

Keep current signer and token snapshots, release floor and standalone recovery
manager. No direct main or Pi push, forced push, new endpoint or new runtime
dependency is needed. Existing plain `backup` directories remain readable.

## Prepared installation

1. Under the existing root update lock, recheck trusted signature, release order,
   version and safe interface names. Record `preparing` before invoking the
   release's preflight and egress checks. These checks must not stop services.
2. Create a unique root-owned 0700 preparation directory. Use the target
   installation's Python interpreter so the wheels match its Python and machine.
   Build the project and all runtime dependencies into this private wheel folder.
   The project build, including acquisition of its declared build requirements,
   must finish before the outage. No source build is allowed after stopping.
3. Create a disposable private verification environment with that same Python.
   Install only the prepared wheels with `--no-index --no-deps`, check their
   dependency closure and import the application modules under isolated mode.
   All build requirements have already served their purpose in creating the
   application wheel. The outage consumes complete wheels rather than rebuilding
   from their sources or needing those build requirements again.
4. During the outage install the exact verified wheels with no index, no source
   build and no dependency download. Check the resulting dependency closure and
   isolated application imports. Run installed bootstrap and CA/name setup only
   after compatible package installation and before services restart.
5. Bound prepared wheel count and total bytes, command time, temporary inputs
   and free-space use. Reject symlinks and unexpected prepared files. Keep build
   cache and temporary folders inside the private preparation directory. Remove
   inactive preparation folders only under the update lock with bounded scanning.

## Complete recovery generations

Use named root-private folders `backup-<32 lowercase hex characters>` and a
small root-private `backup-current.json` selector. The selector accepts only a
known local name and its completed request identifier. It never follows an
arbitrary path. If no selector exists, the previous plain `backup` directory is
the legacy recovery copy. A present malformed selector fails visibly.

Copy old code into the candidate while services run. Keep that copy separate
from the active selector. Check free space before this work. Copy regular files
in bounded chunks, check the deadline throughout and retain intended symlinks
only for root-owned immutable code. Refuse unsafe mutable settings entries.
Transient upload inputs are not recovery data.

After successful service stop, copy config, database, household access state,
managed units and command/dispatcher links into the same candidate. The SQLite
database and its journals are then copied after all writers have stopped. Keep
trust and current CA identity using their separately reviewed rules.

Sync copied files and directories before writing `complete.json`. Its request
identifier, target version and source ownership describe this exact snapshot.
Sync the completion record and candidate directory. Publish the selector with
the existing atomic JSON replace plus parent-directory sync. Do not delete the
previous complete generation before this publication. Retain at most the active
generation and one previous complete generation plus one in-progress candidate.
Cleanup refuses unexpected entries and never deletes the selected recovery copy.

## Interruption and finite recovery

Record durable phases `preparing`, `stopping`, `backing-up`, `installing`,
`checking` and `rolling-back`. Associate the queued request with a unique install
attempt before creating its candidate. A completed generation belongs to that
attempt. Do not restore an unrelated earlier backup just because an update was
interrupted before a new snapshot was complete.

- Interruption during preparation leaves live services and the active recovery
  selector unchanged. The next invocation clears only that attempt's incomplete
  inputs then retries preparation under the lock.
- Interruption after stop but before completed publication leaves live code
  unchanged. Restart its services first and resume preparation safely. Do not
  claim that merely timing out a stop proves its writers have stopped.
- Interruption after completed publication but before the `installing` status
  still has a request-matching complete recovery copy. Restore that copy, since
  later source changes may have begun even if status publication was interrupted.
- During installation or checking, restore only the matching complete copy.
  Explicit user rollback uses the selected complete copy. Current trust, token,
  floor and CA identity survive restoration. The fixed standalone manager stays.
- Failed recovery keeps durable rollback intent for a later attempt and reports
  failure. Successful recovery clears the request only after service health is
  confirmed.

The update service allows 900 seconds. Use an internal total limit of 840 seconds
to leave a termination margin. Forward work has 600 seconds and reserves the
remaining 240 for restoration. Each command, copy and health check uses the
remaining allowance. No late success is accepted after its deadline. Recovery
after a service restart begins a fresh bounded recovery attempt rather than
silently discarding the still-queued request.

Command output is bounded while reading it. Drain both streams without an
unbounded capture, kill the owned process group on timeout or output overflow
and reap it. Report command name, exit category and known safe failure reasons.
Do not print arbitrary tool output that could include a credential. The network
connection query accepts a bounded answer and never puts a token or password in
an argument or saved log.

## Failing tests and final evidence

First reproduce failed wheel preparation or disk checks stopping services,
package work happening after stop and interruption deleting the only complete
backup. Cover interruption before and after each publication boundary, stopped
writers, a real SQLite snapshot, unsafe paths and cleanup limits. Test finite
commands with trickling output, excess output and child processes that retain
the pipe. Preserve failed recovery and successful retry checks.

Build real wheels in a clean private environment and install them offline to
prove the prepared inputs work. Run a standalone copied-manager stage, install
and rollback with the application package unavailable. The coordinator verifies
the final exact source with Python 3.9, 3.11 and 3.13 plus the configured Linux
and browser workflows. Physical Pi installation and normal Mac trust acceptance
remain separate evidence. No live services change during these local checks.

## Independent review and concrete limits

The independent review on 3 October approved the direction and requires the
following boundaries before implementation. The review considered the saved
plan and the current installer, update manager and service definitions.

- Use the exact verified wheels even when the package version equals the
  installed version. Force reinstall with explicit paths. Record at most 64
  wheel names, sizes and SHA-256 hashes tied to the signed full commit and label.
  Recheck those hashes before offline installation. Clear inherited pip settings
  and environment. Isolated imports must come from the installed package rather
  than either source tree. Check imports as the dashboard service user too.
- Allow at most 512 MiB in private preparation inputs and at most 20,000 entries
  there. Allow at most 4 GiB and 100,000 entries in a recovery snapshot. Directory
  depth is at most 32. Individual prepared files are at most 256 MiB. Enumerate,
  copy, sync and clean with deadline checks. Reserve the measured complete
  snapshot size plus the preparation allowance and 100 MiB of free space before
  stopping. Recheck free space for the final mutable snapshot without changing
  live source on refusal. Do not follow immutable interpreter links recursively.
- Retain at most two complete generations and one incomplete candidate. Scan at
  most 16 known preparation/generation names and refuse unexpected or excess
  entries visibly. Validate every restore input before deleting a live tree.
  Preserve the CA owner's per-file permissions rather than overwriting those
  leaves through a recursive config ownership pass.
- Stop every mutable-data writer, including the service-address watcher and
  relevant dispatcher activity. Require confirmed inactive service state before
  the final snapshot. A stop timeout has an unknown outcome and creates durable
  restart/health intent. Late helper work remains owned until the process ends.
- Install a standalone manager that understands this attempt and selector format
  before publishing either format. The first upgrade uses the ordinary installer
  and its existing atomic manager installation. An old manager must never be
  selected after new-format records exist. Future forward manager replacement
  must preserve format compatibility. Keep explicit rollback target identity
  durably pinned across retries.
- An atomic replace may succeed before parent sync fails. Inspect the actual
  validated selector, completion record and attempt to choose recovery rather
  than trusting a local publication-success flag. Test process termination on
  both sides of publication, including before installing status is written.
- If services stopped but no new copy was published, failed restart or health
  still retains a retryable request and explicit recovery intent. Never clear
  the request just because snapshot publication had not finished.
- The 600/240 allowance starts at operation entry. Commands may drain at most
  1 MiB of combined output and retain at most 16 KiB of diagnostics. The connection
  query accepts at most 4 KiB. Health response bodies are at most 64 KiB and use a
  total read deadline. Kill and reap the owned group after timeout or overflow.
  CA, trust restoration and cleanup use the same remaining allowance. Keep failed
  recovery intent after expiration. Restore old dependencies and code offline.

These requirements are implementation gates within the already authorized task.
No additional human permission pause is required. The complete final source and
new failure reproductions receive independent review before publication.

## Explicit retry of retained failed work

The existing manager retains failed recovery intent, but a new install request
then refuses while that intent exists. Add one explicit retry of that same work.
The independent reviewer approved this direction on 3 October. The update owner
implements the root backend and local command. The dashboard owner implements
the authenticated route and button. The helper owner adds its small dispatch
outside the network control lock with normal shutdown ownership.

Mint a public `request_id` of 32 lowercase hex characters before first
preparation. Keep it with an immutable `request_kind` of `install` or `rollback`
in the request, every progress result and the final result. Switching an install
to recovery does not change these fields. Older retained requests gain them
under the update lock before work starts. Frontend progress follows this identity.

Failed status exposes either no retry or `retry` with `id`, `request_id`, `kind`,
`version` and `recovering`. Its 64-character lowercase hex `id` is a fingerprint
of the exact retained intent including the attempt and pinned recovery copy.
This is an admission check rather than progress identity. Changed intent makes
an old click stale. Return a plain `retry_unavailable_reason` if the command group
or update service cannot be confirmed stopped.

The status probe is read-only with a short total deadline. Bound record reads,
process checks and systemd output. Confirm the complete pending process group
has ended and the update service is inactive or failed with MainPID, ControlPID
and TasksCurrent all zero. Unknown or malformed answers refuse retry. Never
queue work from a status read. Recheck all these conditions under the update lock
on admission rather than trusting a prior status response.

An authenticated explicit POST to `/api/updates/retry` contains only `retry_id`.
The helper verifies it then starts the existing one-shot service without waiting.
It never stages another release, creates another request or chooses another
recovery copy. Return 202 with fresh status. Stale, busy or unknown work returns
409. A missing compatible helper returns 503. The browser never automatically
replays this write after an uncertain answer or replacement credentials.

Add `sudo jittersim update retry --yes [--wait]` through the same helper backend.
Waiting follows the same public request identity and treats a completed recovery
as success. Retry an unpublished forward attempt with its original attempt after
clearing only that attempt's incomplete inputs. Preserve failed intent after an
unknown start answer. Require failing backend and public-command tests before
implementation, then review the complete source and integration evidence.

The source review also requires finite entry waiting for the one-shot service.
Only `apply_update` may wait up to 15 seconds for the queue dispatcher to release
the update lock. This waiting belongs to the existing total operation allowance.
Other update callers continue to refuse a held lock immediately.

If a retained queued or running phase is orphaned, a read-only probe may show
failure and offer the same retry only after confirming stopped processes and no
pending systemd job. Query the explicit Job property with empty values included.
Refuse a missing property or positive job number. Recheck the request and status
fingerprint plus the unit before admission. Preserve the actual queued response
immediately after starting rather than performing an orphan probe in that reply.
Keep one process-owned probe reservation if cancellation cannot be confirmed.
Later status reads may inspect it but cannot launch another probe until it ends.
Explicit admission propagates unknown completion. It never starts mutable work.

## Preserving current stopped shaping and household state

The helper owner provides a separate strict root-owned persistent safety guard
outside mutable historical data. It survives reboot and is read before access
startup. Do not infer loss of `/run/jittersim` from a helper stop alone because
the access boot unit also owns it. Capture the complete latest helper control
envelope after confirmed stop and restore it before any service startup.

Normal update, explicit rollback and failed-install recovery must preserve
current Panic and disabled household state. Before replacing mutable data or
starting older services, refuse a candidate that cannot honor the guard. Prove
support from the candidate's actual implementation rather than its version.
Keep current safe source and the pinned recovery copy on refusal. Do not change
the meaning of older outage flags or silently rewrite saved SQLite state.
Include matched-version stop/start, failed SQLite Panic writes, reboot and
legacy-reader refusal in the final proof. These hooks receive a separate source
review before their integration.
