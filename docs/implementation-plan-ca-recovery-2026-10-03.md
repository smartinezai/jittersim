# Preserve certificate identity through install and rollback

## Human review summary

Bring the existing trusted HTTPS and neutral dashboard-name features into the
current release candidate. Keep the Pi's current certificate authority through
rollback even when an older backup contains a different authority. Restore safe
ownership and access before any dashboard restart. An incomplete, damaged or
unreadable existing authority must stop the operation with a useful error
instead of silently creating a replacement that devices do not trust.

Risk is high. A failed restore can lose trusted dashboard access and expose a
private signing key through wrong permissions. Build a bounded durable recovery
copy before replacing configuration. Test interruption and permission failures
with an older package that has no certificate module. Preserve existing names,
addresses and credentials. Fresh settings use the neutral dashboard name only.
Physical certificate trust and live discovery still need separate acceptance.

The coordinator confirmed implementation scope and source identities on
3 October. The main reviewer considers this concrete plan before production
edits and independently reviews the complete final patch. The approved owning
plan is `implementation-plan-update-path-repairs-2026-10-03.md`.

## Exact sources and ownership

- Frozen update base: `169e5423f675ef6f71bad626d88a52e02722096b`.
- Original trusted HTTPS feature: `0245124ebb96013a9d2269a7c0a20161135f3654`.
- Original neutral-name feature: `a857dd3a76daf07f2c33e5f16736424826863d0d`.
- Isolated branch: `codex/ca-recovery-repair`. Preserve the identity repair
  handoff and documentation audit without modifying their commits or evidence.
- This change owns certificate and local-name modules, trust API/routes/assets,
  discovery feature integration, certificate installer blocks and dedicated
  standard-library certificate preservation functions in `updates.py`.
- The update owner retains package preparation, backup publication, signing,
  upload, semantic version, online-check and broader install orchestration.
- The Wi-Fi owner retains the installer AP/password block and neutral fresh
  SSID. Agree actual ordering and every shared-file conflict with both owners.

## Findings to reproduce

1. Original rollback keeps the current authority only when the old backup has
   none. Restoring a backup with another authority changes device trust.
2. Recursive directory ownership restoration gives certificate files the
   configuration directory's group. The dashboard loses access to its key or
   CA directory. Certificate permission errors are silently suppressed.
3. `ensure_authority` treats a damaged or mismatched existing identity like an
   absent first installation. It replaces the key and certificate separately.
4. The old keep-directory path is not a complete durable identity recovery
   record. An interrupted configuration replacement can leave no safe identity
   source for the next restore attempt.
5. The recovery manager must work when the restored package has no `localca`,
   `localname` or trust-download API. It cannot import them to restore access.

## Proposed behavior and bounds

### Existing authority and first creation

Distinguish a truly absent authority from any existing authority directory or
file. Reject symlinks, partial file sets, mismatched keys, invalid constraints
and unreadable or expired identity. Keep all existing bytes on refusal.
Renew a leaf only with a verified existing authority. Generate a new authority
only on a genuinely fresh installation. Publish a complete new directory in
one rename after syncing its files and containing directory. Coordinate first
creation with a private lock so concurrent calls do not publish two identities.

Permission changes must fail visibly. Use the configured dashboard group for
the authority directory and dashboard private key. Keep the authority key
root-only. Public certificates stay readable. Validate final access before
reporting success. Do not print private material, token values or credentials.

### Durable rollback identity

Before destructive configuration replacement, capture current authority and
matching dashboard certificates/key in a separate private recovery namespace
under the update directory. Keep a complete immutable generation with a
bounded manifest recording file presence, hashes and format version. Publish
its pointer atomically only after files and directories are synced. A retry
uses that complete generation even if live configuration is missing or partial.
An interrupted candidate cannot replace or delete the last complete generation.

The record includes an explicit absent-authority state so rollback does not
silently resurrect an authority from an older backup. Preserve a complete
current legacy dashboard certificate/key pair separately from a CA identity.
Reject a partial pair. The active recovery pointer belongs to one restore
transaction and remains active until the owning update flow confirms health.
Only that success marks it inactive. A subsequent independent restore captures
the then-current identity rather than reusing an old completed transaction.
Agree the explicit completion hook with the update owner before editing.

Limit the recovery set to known certificate files and the necessary saved
neutral-name setting. Reject links, special files, overlarge inputs and unknown
manifest entries. Use descriptor-based reads with no-follow flags and a
64 KiB maximum for each known file. Retain only the complete active generation
and one pending generation. Keep cleanup bounded to this owned namespace.

Exclude preserved certificate files from backup-to-live configuration copying.
Restore from the complete current-identity record with atomic file replacement.
Validate key/certificate matches and signatures using bounded OpenSSL commands
without importing installed application code. Restore ownership and modes
before restart. Failure leaves recovery evidence intact and services stopped
rather than treating recovery as successful.

Current signing trust, the release floor and GitHub token retain the update
owner's existing independent preservation rules. This repair must not rewrite
their keep records or reuse their identity namespace.

### Feature import and neutral names

Import the original trusted HTTPS and one-address features as reviewed source,
resolving against current authentication, branding, reconnect and upload code.
Review all original feature changes rather than assuming those old heads are
approved. Keep trust downloads behind the current token and registered-device
checks. Keep private keys out of API responses. Public status exposes only the
approved neutral information.

Preserve explicitly configured names and all existing Wi-Fi settings. The
original name import discards custom legacy `foo.home.arpa` values under its
new `.local` validator. Retain such existing aliases in DNS and certificate
names while introducing the neutral `.local` announcement. Reject damaged
explicit settings rather than treating them as absent. Do not silently erase
custom legacy bookmarks or rotate a constrained authority to admit a new name.
Announce
the neutral dashboard address on Ethernet and test Wi-Fi with matching leaf
names. Inspect Avahi configuration, bounds, shutdown, dispatcher ordering and
failure behavior. No broad dashboard redesign is included.

The original features still include a branded legacy certificate alias. For a
fresh installation use neutral certificate defaults. Preserve needed old
aliases only as an explicit compatibility rule for an existing installation.
Confirm that rule with the main reviewer and owning coordinator.

## Work and verification

1. Review this plan and settle legacy/no-authority behavior, compatibility
   aliases and durable record lifetime. Agree shared installer/update blocks.
2. Import original features into the isolated base. Record all conflicts and
   inspect the full combined change. Preserve current fixes in shared files.
3. Add failing meaningful cases before repair: distinct current/backup CAs,
   interrupted copy/publication/restore, damaged live CA, mixed leaf/key, failed
   chmod/chown, absent state and package-independent recovery.
4. Repair authority creation, permission errors and durable identity recovery.
   Verify retained authority fingerprint and matching dashboard leaf/key.
5. Run focused CA, name, trust API, discovery, deployment and update suites.
   Test actual TLS connections trusting only the retained authority and
   authorization rejection. Check visible errors and bounded work.
6. Run configured lint and broader relevant existing checks against the final
   source. A real Linux root/group fixture is required for permission claims.
   Use only explicitly allocated tooling. No new or unrelated Docker operation.
7. Main reviews the entire source and evidence independently. Address findings
   then freeze hashes and local commits for the update owner and coordinator.
8. The coordinator runs final combined workflows, clean images and installed
   wheel checks. Hosted jobs blocked by account limits remain unavailable while
   the configured checks run locally. Do not call hosted checks green.

## Deployment, data and recovery

No database migration or new retained client history is introduced. The current
root CA key remains on the Pi and only private recovery copies contain it.
Copies are root-only with bounded retention. Public downloads return only the
authority certificate or profile. Normal OS trust installation is an owner
action and is outside this local repair.

The standalone manager restores certificate identity before restarting an old
release. Recovery must remain retryable at every copy/publication boundary.
Rollback restores old application code while preserving current identity and
trust decisions. Complete source checks do not prove physical mDNS, real iPhone
profile installation or trusted HTTPS on the Pi. Live acceptance requires a
verified installed revision and independent management recovery.

OpenSSL verification behavior is checked against its
[official verification documentation](https://docs.openssl.org/3.0/man1/openssl-verification-options/).
Use explicit authority verification and key matching. Do not confuse a PEM
parser or filename with proof of a valid certificate chain.

## Independent plan review on 3 October

The plan review accepted the owned import and repair direction before source
edits, with these concrete requirements:

- With no CA, a complete verified legacy certificate/key is an explicit legacy
  identity. Preserve it and optional matching Wi-Fi certificate through rollback
  along with explicit CA absence. Reject partial, mismatched, linked, unreadable
  or unknown-issuer state before changing bytes.
- Adopting the CA feature on a verified legacy installation is an explicit
  forward transition under the coordinator's scope. Keep recoverable legacy
  evidence first. This does not permit replacing a damaged existing CA.
- Fresh defaults use only neutral certificate names. Existing required aliases
  stay reachable. Custom `.home.arpa` bookmarks retain DNS/certificate support
  without claiming mDNS support for that suffix. Verify actual authority
  constraints before issuing any new alias and refuse unsupported changes.
- Capture identity immediately before destructive restore. Keep one active
  transaction record through interruption and retire it only after confirmed
  health and recovery metadata. A new transaction captures fresh live identity.
- Check concrete CA constraints, key usage, path length, matching keys and leaf
  server purpose/names using only the retained authority. Give all verification
  steps one total deadline. Private bounded records require strict owner checks.
- Use a real Linux fixture with distinct dashboard UID/GID before claiming
  permission correctness. No live OS trust change is part of that fixture.
- Final review still covers the full original feature import, authentication,
  private-key privacy, discovery bounds and all repair evidence.

## Reviewed adoption and command ownership decisions

The first CA adoption has its own original identity and verified candidate.
Publish a fresh transition identifier and exact file hashes before changing
live identity. An interrupted owned candidate may resume or restore its exact
original. An unrelated manual replacement must be refused. Completing one
transition must never retire a later transition after a delayed health check.

Keep the candidate unchanged until it passes health. If DHCP changed the
Ethernet address while adoption was interrupted, retire that complete owned
transition first. Then renew the leaf under the same authority and key and
check final health. Report success only after the final address is covered.
Do not create another authority to handle that address change.

The root manager loads certificate and name modules from the verified signed
target. Alias their update helpers to the currently executing root manager
rather than relying on the installed package. Call their functions directly.
All certificate commands inherit one owning runner and the remaining update
time allowance. A direct bounded health callback controls adoption completion.
Nested Python command managers could leave detached certificate descendants
running after the outer command stopped. Remove that nesting before freezing
the final update path. The manual CLI must use the same bounded health code.

The direct certificate APIs are `prepare(..., budget=None, run=None)` and
`complete_adoption(..., health=None, budget=None, run=None)`. The health callback
returns a boolean without taking arguments and retains recovery evidence on
failure. Unknown command ownership errors propagate without recovery or
completion. The manager owner supplies the reviewed bounded HTTP health check
and signed module loader. This checkout does not claim their integration until
their exact source and tests are included.

Manual dashboard renaming uses the same public `manual_identity_operation`
context from the update manager across certificate preparation, name saving,
resolver publication and service restarts. That manager owns queued-update
refusal and durable records for commands that have not stopped. The CLI calls
certificate and resolver functions directly and rechecks the saved name after
confirmation under the lock. A manager without that ownership API must refuse
the change. The installer and supervisor share the same verified file lock.
Only a verified inherited descriptor may reuse it. A live command retains the
lock and a durable record blocks later changes until its entire group ends.
The update owner supplies and tests this shared policy before final integration.

Every standalone certificate CLI command uses that same operation context,
including ensure, interrupted-adoption recovery, completion and health checks.
No public bypass argument is added. The installed command must borrow the
installer's verified inherited lock rather than taking a second lock that
would refuse its own parent. Unit fixtures with isolated locks prove command
entry behavior only. The final shared manager still needs actual Linux proof
of inherited ownership and refusal while a retained command can be running.

## Final documentation reconciliation

The current README, Pi deployment guide and unticked acceptance checklist are
reconciled separately from the historical PR 54/75 audit. The reference for
ordinary full restore behavior is the coordinator's reviewed combined commit
`cc58b78c59807570be0ec5fe005b8756badcaee3`. A complete restore returns ordinary
data to its selected recovery point. Do not promise that later profiles,
protection settings, saved device access or usage survive. Separately protected
signing trust, token, certificate identity and Panic state have their own rules.

Deploying only checked official main is the coordinator's release policy.
The general ref option does not enforce it. Native browser trust, actual Pi
discovery, radio behavior and physical recovery remain unverified. SQLite
backup examples use its consistent online backup API. A disposable WAL fixture
checks committed rows, file privacy and refusal to overwrite an existing copy.

The independent complete import review also required malformed announcement
reports to return the stopped fallback and registration to report readiness
only after Avahi confirms it. Trust text describes local name suffixes and
configured network address ranges. A nonprivate Ethernet network may contribute
its configured `/24` range, so the text must not claim every range is private.

## Verification receipts and limits

The allocated Linux permission fixture completed using the already present
image `sha256:0bee9c34c2f23d34c1bbce9cb88702f5956589070cc1be0117489ddcc7fccbac`.
Its result is `test-results/ca-linux-proof-2026-10-03/result.json` with SHA-256
`9dc835ec3bcf8085cb47e6a95d8ddaf3cee856d7b1c4dc5a9a3c3ef97067501c`.
Distinct dashboard and unrelated users confirmed key access restrictions and
the restored dashboard user loaded its TLS key. This proves the pinned
permission subset in that receipt. It predates later command API edits and
does not approve the final complete source. The allocated container was removed.

The full historic-base source run recorded 4126 passing cases and 33 failures.
Those failures include newer shared version and GUI behavior already corrected
on the coordinator's reviewed branch. Correct this checkout's authentication
test setup and weak ETag expectation without duplicating the other owners'
changes. The final combined candidate still needs its configured workflows.
Focused checks and local receipts must not be described as hosted CI passing.
