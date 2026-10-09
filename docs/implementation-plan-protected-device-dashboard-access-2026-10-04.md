# Give protected devices dashboard access

## Human review summary

Protecting a device should automatically allow it to reach the JitterSim admin
dashboard. The owner requested this behavior on 4 October 2026. Today traffic
protection and saved dashboard access are separate. The Air needed both to be
configured during the Pi-only access-point preparation.

Keep the admin token required. Protection grants the device permission to
connect, not the token itself. Use the verified current device identity and
preserve private-network isolation. This feature must not grant SSH access,
router administration or access from the public internet.

This is planned work. No permission or running Pi setting is changed by this
document. Review the implementation design and removal behavior before coding.
Treat the change as high risk because it changes dashboard authorization and
could either widen access unexpectedly or lock out the owner.

## Expected behavior

- Adding protection also grants dashboard access without a second registration
  step. Report success only when the permission is actually available.
- Existing protected devices receive the same permission during a bounded
  upgrade step. Preserve existing separately granted dashboard permissions.
- Reset, Panic, profile changes and reboot retain the protection-based grant.
- Require the admin token for protected devices as for other allowed devices.
  Keep certificate validation and socket-peer identity verification.
- Show whether access comes from protection, a separate saved grant or both.
- A changed private Wi-Fi address does not inherit access merely because its
  name or IP matches. Use the existing verified address-change workflow to move
  protection and access together. Retire the old identity without losing undo.

## Removal decision to review

Proposed default: removing protection removes only the grant that came from
protection. A separately saved dashboard grant survives. Removing a separate
grant does not override access while the device is still protected. Explain
this on the permission controls so the displayed state matches actual access.

Review how a protected device can be explicitly denied dashboard access and
whether that exception belongs in this first version. Also decide the warning
and recovery behavior when removing the current or last working control device.
Do not silently remove the owner's only verified control route.

## Implementation work

1. Review the protection write path, root helper permission checks, control
   firewall and address-change recovery together. Inspect imports, grouped
   device actions, settings restoration and boot restoration too.
2. Keep separate reasons for a grant. Effective permission should be the union
   of manual grants and current protected identities. Do not treat every
   automatically added entry as a permanent manual grant.
3. Design one coordinated operation for saving protection and updating helper
   permissions. Preserve current request ownership and stop controls. Make
   retries safe after interruption. If one part fails, retain traffic safety
   and report the exact remaining work rather than false success.
4. Reconcile the current limits before implementation. Protection allows 256
   devices while the dashboard permission store allows 64. Support the complete
   intended set with explicit finite limits. Do not silently omit devices.
   Avoid a new database query or network scan for every dashboard poll.
5. Add an automatic upgrade step that is safe to repeat and bounded by the
   supported device count. Preserve manually granted access and record enough
   state to recover from a crash or roll back without broadening permissions.
6. Store only the existing device identity, label and grant reason needed for
   these decisions. Do not copy admin tokens, router passwords or private keys
   into permission records, activity messages or logs.
7. Review the full change before running the relevant configured release
   checks, building an installed package and testing it on the Pi. Deployment
   must use the official guarded installer and a checked private recovery copy.
   Keep a verified management route during upgrade and rollback.

## Verification and release gates

Cover add, remove, both grant reasons, existing protected devices and the full
supported device limit. Check invalid or spoofed identity, missing token,
unprotected clients and certificate failures. Verify that protection alone
cannot bypass authentication or expose unrelated private network services.

Exercise concurrent protection changes, stale requests, helper failure,
interrupted upgrade, repeated upgrade, reboot, settings restoration and
rollback. Include a changed private address with undo. Prove that failed
permission synchronization is visible and can recover without losing control.

Browser checks must show the actual grant reason and token requirement. HTTP
checks must reject unauthorized reads and writes. Linux packet checks must
verify the control firewall permits the protected client while normal test
client isolation remains effective. Physical acceptance must show a newly
protected device opening the dashboard without separate registration and
retaining that access after reboot.

## Design addendum (9 October 2026)

Decided before coding. Where the plan left a question open the safest default
was chosen and is named here.

### Where each grant lives

- Manual grants stay exactly as they are: the root-owned
  `/etc/jittersim/dashboard-access.json` (format version 1, at most 64
  entries, move and Undo ownership fields unchanged). An older release reads
  the same file unchanged.
- Protection-derived grants are not written to disk. The root helper keeps
  them in memory and the dashboard replaces the whole set with one
  `protected-sync` helper request built from the saved protected list (the
  database is the only durable record of protection). At most 256 entries,
  the same finite limit as the protected list.
- The helper's effective view is the union. An entry that is both carries the
  manual fields plus `"protected": true`. A protection-only entry carries
  `"manual": false, "protected": true`. Missing keys mean manual only, so
  nothing changes for installs without protected devices.
- Until the first `protected-sync` in a helper process lifetime the helper
  grants nothing from protection and reports `protected_synced: false`. The
  dashboard then reconciles at once and asks again. A stale or interrupted
  set can therefore never outlive a restart.

### Limits

64 manual plus 256 protection-derived gives at most 320 effective entries
(plus kept replaced manual entries, which are inside the 64). Every protected
device is either granted or listed as not eligible with its reason (for
example a hardware address that cannot identify one Wi-Fi station). Nothing is
dropped silently. The sync payload is about 31 KB, well inside the helper's
256 KB message limit. Dashboard polls read no extra database rows: the helper
answers from the in-memory set it already has.

### One coordinated operation

Saving protection keeps today's order: the database change and network apply
first (traffic safety), then the access sync. The sync is a full replacement
so it is safe to repeat after any interruption. Syncs are serialised in the
dashboard and each reads the current protected list inside that lock, so a
stale request cannot overwrite a newer one. If the sync fails the response
says protection was saved but dashboard access is not confirmed, the Dashboard
access panel shows the failure with a Retry button and the dashboard retries
by itself (at startup, after the helper restarts, after every later write and
at most every 10 seconds while a failure is pending). It never reports access
as available when the helper did not confirm it.

### Upgrade and rollback

The upgrade step is the first sync after the new release starts: automatic,
bounded by 256, idempotent and it never touches the manual file, so manual
grants are preserved by construction. A crash part-way leaves either the old
or the new in-memory set, and a restart starts from none. Rolling back to an
older release drops the in-memory set with the old helper, so access narrows
to manual grants and nothing widens.

### Removal

- Removing protection removes only the protection-derived grant.
- Removing a manual grant from a protected device keeps protection access. The
  list says "Stays allowed while protected" so the shown state matches.
- An explicit deny for a protected device is not in this first version. The
  owner removes protection instead. This keeps one rule: protected means
  allowed.
- The owner's only verified control route is never removed silently. When the
  device making the request is on the test Wi-Fi and its access comes only from
  protection, removing its protection is refused until the owner chooses
  either to keep dashboard access as a separate saved grant for this exact
  verified device or to remove access as well. A settings import that would
  drop the requesting device's protection keeps its access as a saved grant
  and says so in the response.

### Identity and secrets

Grants use the hardware address that the helper verified from the socket peer
(kernel route, neighbour entry and one associated station), exactly as
before. A changed private address gets nothing by name or IP. The existing
verified address move carries protection and the manual grant together, and
Undo restores both; the protection grant follows the protected list. Records
hold only the hardware address, the label and the grant reason. No token,
password or key is copied into records, activity messages or logs. The admin
token, TLS certificate checks and socket-peer identity checks are unchanged.
No firewall rule changes: the dashboard port was already reachable from the
test Wi-Fi and authorisation stays in the dashboard and helper. SSH, router
administration and public internet access are not affected.

### Panic-reviewed source

`networking/helper.py` changes (one new helper operation, two new status
fields and one client method). It therefore needs a new approved bundle in
`PANIC_APPROVED_SOURCES` after an independent Panic-consumer review.
