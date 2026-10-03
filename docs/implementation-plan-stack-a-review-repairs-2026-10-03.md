# Repair the two Stack A review blockers

## Human review summary

Two source reviews found defects on branches whose current CI is green. Repair
them with failing checks before treating those branches as ready to merge.
An interrupted rollback from an old backup can restore a revoked signing key
and replace the current GitHub token. The Mac command can also wait beyond its
request limit during a stalled DNS or `.local` lookup.

Keep recovery trust separate from the old backup's configuration. Preserve the
current signer list and token across repeated recovery attempts. Keep name
lookup within the existing command deadline without accumulating workers that
continue indefinitely. Both changes should stay small and remain on their
owning branches. They introduce no new update transport or signing protocol.

Trust recovery is high risk because it can undo key revocation. The main
verification is failure during restoration followed by another recovery attempt.
Name lookup is a reliability change whose verification is stalled resolution
under repeated calls. Samantha approved this plan and its independent review
on 3 October. Implementation, publication and conditional merge are authorized.
When GitHub limits prevent jobs, run the intended workflow
checks locally against the exact combined head rather than halting verification.

## Sources and ownership

| Work | Exact source head | Owning worktree under `/Users/samantha.martinez/Github repos/jittersim.worktrees` |
| --- | --- | --- |
| PR 46, Mac CLI | `7899f5a630cf4437090fcc967daf29ea72d92068` | `claude-cli-deadline` |
| PR 52, signed updates | `4330aa21c590b354da31754545b295737ec06da1` | `claude-signed-fixes` |

Both branches belong to this handoff. PRs 60, 71, 72 and 74 retain their existing
owners. The coordinator owns final integration and Pi deployment. Keep the
coordination files untracked. Never force push or push to main or the Pi branch.

When combining the CLI with the final dashboard, preserve the confirmation
contract introduced by `95573db`. Adding observed sites to a profile that does
not currently allow only listed sites requires `confirm_allow_only` to be a
strict boolean `true`. The server checks the current saved mode. A stale client
gets HTTP 409 and the current profile without changes. Review every CLI or
service caller that uses this action. Refresh after refusal and require a
deliberate confirmed action before another write. Never replay the POST
automatically. This is an integration dependency rather than a new repair
assignment in the approved repair scope.

The central reasoning is maintaining the current authorization rules through
failure and keeping an elapsed-time promise through work that cannot be
cancelled merely by returning from a request. Fixture setup is routine work.

## Confirmed failures and repair requirements

### Trust during interrupted legacy rollback

PR 52 `src/jittersim/updates.py:1064` copies an older backup's configuration
before removing its trust settings at line 1069. A data-copy error can therefore
leave the old signer list active. Automatic recovery at line 1250 repeats the
preservation step at lines 1029-1032 and replaces the saved current list and
token with the older backup values.

Two temporary checks use real signature verification, staging and recovery.
Process calls are replaced at their boundary and the data-copy failure is
injected at its filesystem boundary. Both fail. Immediately after interruption
the revoked signer is trusted again. After successful recovery retry that signer
remains trusted and the replacement signer and current toy token are lost.
The probe is `/private/tmp/test_jittersim_pr52_legacy_trust_interruption.py`.

Required behavior:

- Never copy an old backup's signer list or token into live configuration,
  including legacy backups and failure partway through copying.
- Preserve the current trust settings once for a recovery attempt. Repeated
  attempts must not overwrite them with partially restored configuration.
- Incomplete recovery must fail closed. A missing or revoked current signer
  must not become trusted merely because an older backup lists it.
- Keep the current token private and root-only. Keep key revocation and the
  monotonic release floor intact through rollback.
- Retain compatibility with old recovery copies. Reject unsafe or incomplete
  state visibly rather than claiming rollback succeeded.

First add the failing regression to the owning test suite. Cover interrupted
legacy restoration, automatic retry, a later successful retry, absent current
trust, signer revocation after a failed recovery and newer backups. Check
repeated interruption at the configuration-copy and trust-restoration boundaries.
Keep the real update manager under test. Then make the focused repair.

### Name lookup within the Mac request deadline

PR 46 `src/jittersim/remote_client.py:443` calls synchronous `getaddrinfo` before
checking the remaining deadline. A temporary delayed resolver exceeded a 20 ms
allowance and returned after 123 ms. This is independent of the downstream
HTTPS deployment-wait repair.

Required behavior:

- Name resolution, connection, TLS handshake, sending and response reading
  share the existing request deadline.
- A stalled resolver must not keep the caller waiting beyond that deadline.
- The deadline covers actual CLI process exit as well as the connection
  function returning. A resolver still running must not hold command exit open.
- Repeated timeouts must not accumulate unlimited resolver threads, pending
  requests or results. A timed-out worker keeps its capacity slot until its
  work finishes or it is actually stopped. Returning from a waiting caller
  must not allow unlimited replacement workers. Define a clear capacity response.
- Keep address retries within the same deadline. Preserve verified TLS,
  hostname checking and refusal of redirects and environment proxies.
- Do not retry an uncertain write while recovering from a connection failure.

Add a failing check using the real connection function with a delayed resolver
at the external boundary. Include repeated held resolutions, capacity recovery,
an expired deadline, multiple returned addresses and normal `.local` resolution.
Test actual command exit while resolution remains held. Reject late results
and prove no connection starts after the deadline.
Avoid relying on the live Mac resolver for a deterministic unit check. Then
make the focused repair without adding a new dependency.

## Verification and delivery

1. Human feedback and implementation approval received on 3 October.
2. Add and run the focused failures before production changes. Use toy signing
   and token data only. Do not read actual credentials into fixtures or logs.
3. Repair each owning branch then run its affected tests. Review complete diffs
   for limits, interruption behavior, old-record compatibility and secret access.
4. Run the full Python suite, requested Ruff rules and all browser journeys named
   in each final workflow. Existing PR 46 review ran 348 focused checks. Those
   passing results do not establish the new deadline behavior.
5. Make separate local commits for independent fixes. Publish ready changes
   together then immediately resume babysit monitoring on their exact new heads.
6. Carry the chosen integration base and sibling update branches into the final
   release. Run every workflow check on the exact resulting commit. Use local
   execution while GitHub limits prevent jobs and preserve source/wheel hashes,
   runtimes, dependency versions, commands, results and skip reasons. Report
   material platform and cached-dependency differences. The coordinator makes
   the final merge and deployment decision.

No shared build dependency or database schema changes are proposed. CI must
still test supported Python versions and a clean package installation. Never
start or stop unrelated Docker containers. No live Pi action belongs to this
repair phase. Current management recovery is unconfirmed.

The Mac request continues to read the same authorized dashboard data. Recovery
copies existing configuration without introducing additional retained data.
Keep temporary recovery files private, bounded and safely removed only after
completion. A revert alone cannot undo a trust-setting restoration, so failed
recovery must retain the current trusted state and a usable recovery record.

No PR risk template was found in the prior repository inspection. Treat trust
recovery as high risk and require independent review of its completed change.

## Independent evidence and open decisions

The PR 52 security review found the trust defect and reproduced both failing
checks. It found no additional blocker in signature-before-parsing, archive
checks or the exact passwordless-command boundary. The PR 46 source review
confirmed the resolution gap and passed 348 focused checks. These findings
come from this continuation rather than an earlier external review round.

Current trust copies publish atomically. Configuration restoration excludes old
trust files before copying. A durable format record distinguishes recovery by
the repaired manager. A nonempty unmarked recovery folder is refused before
replacing live folders because an older manager may have made its live and
saved trust disagree. Live changes made during recovery take precedence on
retry. Rollback retains the current standalone recovery manager.

At most four daemon threads may resolve dashboard names. Returning after a
deadline keeps the worker's capacity occupied until the system resolver ends.
Late answers cannot start a connection. A short-lived command may exit while
resolution remains held. No dependency is added.

Exact combined-head verification remains pending. Billing must not halt local
checks.

The saved companion plan was independently reviewed on 3 October and found
accurate and suitable for human review. The review requested the process-exit
and retained-worker-capacity requirements now included above. It made no
source changes and ran no additional tests.
