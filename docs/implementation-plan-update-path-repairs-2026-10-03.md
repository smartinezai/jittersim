# Repair the update path before the combined release

## Human review summary

Make the existing update path work after sign-in and keep installation,
overlapping uploads and rollback predictable. The real browser reproduced an
online-check failure: after successful sign-in the Check button sends no online
request and the online settings stay disabled. Existing unit checks pass but
miss that sequence. Repair it with a failing regression first.

The other work keeps version labels and badges current, protects an upload
from another request, gives HTTPS waiting one total deadline, restores the
certificate's required access after rollback and prepares expensive work before
services stop. Keep uncertain write results separate from connection recovery.
Do not repeat an uncertain install request or broaden this into a redesign.

Installation and certificate recovery are high risk. Three decisions remain:
how upload ownership survives a dashboard restart or an uncertain helper answer,
how Panic stays usable during root staging and how interrupted backup replacement
keeps a complete recovery copy available. A raw copy of a running database is
insufficient. Prepare immutable code and package work first then take the final
mutable settings snapshot after writes have stopped. Samantha approved this
summary and the independent review on 3 October. Implementation, publication
and merge after passing checks are authorized. Local workflow checks replace
GitHub jobs while billing prevents them from running.

## Sources and ownership

These are separate source snapshots rather than one verified release:

| Component | Head | Worktree under `<home>/Github repos/jittersim.worktrees` |
| --- | --- | --- |
| Online check, PR 68 | `1817898a298a45eefd813119a5d692910d272cdd` | `claude-online-check` |
| Upload and Mac command, PR 66 | `f8f4b393d7925889cece7ddb383d7d1b00e4e136` | `claude-mac-publish` |
| Local HTTPS, PR 65 | `0245124ebb96013a9d2269a7c0a20161135f3654` | `claude-trust` |
| Neutral address | `a857dd3a76daf07f2c33e5f16736424826863d0d` | `claude-one-address` |
| Worker threads, PR 70 | `c31b5cb503e96881c0ba2e33617a5c2abdc68f51` | `claude-perf-server` |

Retain these owners and keep fixes focused. The coordinating chat chooses the
combined release base and owns merging and deployment. PRs 60, 71, 72 and 74
stay with their existing owners. Coordinate semantic-version changes with
PRs 60 and 74 without editing their branches. PR 51 stays with the coordinator.
Keep both coordination files untracked and never force push.

The PR 72 owner also owns the normal helper response/operation-lock timing
repair. Keep that separate from the slow root-staging/Panic defect in this plan.
Use the coordinator's final combined helper source when carrying approved update
repairs forward so the normal response repair and staging repair both remain.

The learning objective is preserving clear ownership and recovery when network
requests and root installation work overlap. Test fixture setup is routine work.

## Evidence and proposed repairs

| Work | Evidence and status | Change and failing checks required first |
| --- | --- | --- |
| Load update status after sign-in | Reproduced in the real browser. `workflows.js:861` reads online status during construction while `server.py:2329` refuses protected reads before sign-in. `/private/tmp/jittersim-online-signin-review.log` records zero online requests after clicking Check | Load status only after valid sign-in and device access. Cover a fresh page, remembered sign-in, enrollment, changed token, revoked device access and a failed status read followed by retry. Reject answers from an earlier token revision. Failed reads leave an explicit unknown state. Do not start a GitHub check merely to load status |
| Installed version and update badge | Source-confirmed. `workflows.js:247` ignores the health version when release status exists with no installed version. Reading a completed daily job does not reload staged status. Updates-page polling follows an existing installation rather than later daily staging. HTTPS script line 212 still compares an installed version with a short commit | Show the current known version with a safe fallback. Read staged status after sign-in and after completed staging. Test semantic versions on both Mac transports, an already installed release, a daily check completed outside this browser and missing progress labels. Compare with the signed manifest version. A failed read must not claim the Pi is current |
| Upload ownership with worker threads | Upload interference is a combination risk awaiting reproduction. `server.py:13880-13905` uses shared upload filenames. Root `updates.py:810-833` also copies shared incoming files before the update lock. A local probe reproduced Panic failing with `helper-busy` after three seconds during held root staging. The shared operation lock is at `helper.py:1225-1241` | Keep ownership through preparation, reading, root copying, staging and cleanup. Refuse a second upload promptly. Cover helper timeout, dashboard restart during root copying, disconnect, cleanup and a later retry. Require health and a successful Panic network release while root staging is deliberately held. Root signature and sequence checks stay authoritative |
| Total HTTPS install wait | Source-confirmed. `deploy-over-https.sh:236-246` counts five-second sleeps but excludes status calls that can take 15 seconds | Enforce one elapsed-time limit including reads and sleeps. Stop a read when its remaining allowance ends. Test slow reads, repeated connection failure, a late success and interruption. Queue installation once. Never retry its POST after an uncertain answer |
| Certificate access after rollback | Source-confirmed. PR 65 `updates.py:535-536` restores every config entry with the directory's owner. `localca.py:350-362` requires dashboard-group access to the CA directory and dashboard key. Its ownership helper suppresses failures. Existing CA preservation applies only when the backup lacks a CA | Restore required ownership and modes before dashboard startup. Keep the current CA identity even when the backup also contains a different CA. Verify that restored dashboard certificates match that retained CA. Keep the signing key and GitHub token root-only. Cover permission-restoration failure, real group permissions, certificate download, dashboard startup and a previous release without local certificate code |
| Preparation before service stop | Source-confirmed. PR 66 `updates.py:1190-1213` stops services before backup and the disk check. Package installation follows during the outage. Error output is discarded at lines 868-896. Restart recovery recognizes only three phases at line 1031 and backup replacement deletes the old copy before renaming the candidate at line 1249 | Verify signed source, disk allowance and package build inputs before stopping services. Prepare a private local wheel set and immutable backup work without changing the live environment. Take a consistent final settings backup once writes stop. Install offline from compatible application and dependency wheels. Keep preparation and recovery within a total time allowance. Collect bounded error details without credentials. Test each interruption boundary and failed install/rollback |

The two failed Mac transport-choice tests were separately reproduced. They read
the real Mac default route because the fixture replaces `ifconfig` but omits
`route`. Both passed with a temporary route stand-in. Fix that test isolation
before using local suite results to judge transport selection.

Retain the handoff's Mac-command requirement: installation is the default and
an explicit stage-only option remains available. Add failing checks for both
transports before changing that behavior. Keep this work with PR 66 and the
existing version owners.

## Decisions before the high-risk repairs

1. Upload ownership must cover both dashboard and root work. A dashboard process
   lock alone is insufficient after timeout or restart. Recommend isolating
   each accepted upload's input until root has finished using it. The chosen
   bounded handoff must establish completion before cleanup or another upload
   can replace those inputs. Specify recovery after helper and dashboard
   interruption. An uncertain result must not leave an indefinite lock or permit
   early reuse of the same files.
2. Root staging must not hold the shared network-operation lock during slow
   verification or extraction. Recommend separate bounded staging ownership
   while retaining the update lock and signed validation. Prove that Panic
   actually releases the network during a held stage operation. Merely getting
   an HTTP reply is insufficient. Panic waits up to three seconds to acquire
   the shared network-operation lock at `helper.py:58-59`.
3. Define recovery at each backup boundary before reordering installation:
   before service stop, during the final settings copy, after the candidate is
   complete and while replacing the last recovery copy. Recommend retaining a
   discoverable complete old or new copy at every boundary. Recovery must know
   whether the live installation changed and whether services need restarting.
   Preparation needs one total allowance inside the service's existing
   15-minute limit, including time reserved for installation and recovery.

These recommendations need design agreement within the approved scope. They
do not authorize a new background-job framework or changes to the signing
protocol. Check the existing manager's older recovery records when choosing
the final rules.

## Sequence and integration

1. Human feedback and implementation approval received on 3 October.
2. Add focused failing tests for each repair on its owning source. Preserve the
   actual page and recovery functions under test. Stand-ins belong at network,
   process and filesystem boundaries.
3. Repair the UI and test isolation first. Repair upload ownership with the
   threaded source present. Carry the certificate fix from PR 65 into the
   neutral-address branch. Agree the backup consistency boundary before changing
   installation order.
4. Combine against the coordinator's selected release base using merge commits.
   PRs 66 and 68 currently share PR 52 as their base. A final branch must contain
   both and the required access/performance changes. Review every conflict by
   behavior and check the exact resulting commit.
5. Make focused local commits then publish ready changes together once. Keep
   draft PRs until the workflow checks pass on their exact heads. Run those
   checks locally when GitHub limits prevent jobs and preserve exact-commit
   evidence. The coordinator handles final merge and installation.

## Verification and deployment

- Run the full Python suite, requested Ruff rules and every Python browser
  journey named in the final workflows. Use Playwright 1.63.0.
- Add real-browser coverage for fresh load, sign-in, Check, daily staging,
  version text and badge updates. Keep authenticated reads behind the existing
  token and device checks. Preserve focus and the dark theme.
- Test upload limits, signature rejection, sequence rejection, concurrency and
  interrupted/uncertain operations. Memory, temporary disk usage, pending work,
  retries and error output must stay bounded.
- Check clean wheel/package installation and installer/update parity in owned
  CI or an isolated container. Never start or stop unrelated Docker containers.
  An offline install must prove there is no source rebuild or package download.
  Include build requirements as well as runtime dependencies and match the
  Pi's Python version and architecture.
- Check all supported Python versions and Linux permission tests. A warm local
  environment cannot establish a clean deployment.
  GitHub limits must not halt verification. Run every intended workflow command
  locally with matching runtime versions and isolated application installation.
  Record wheel and source hashes, dependencies, image identities, capabilities,
  command results, skips and material platform/cache differences. Do not mark
  unrun GitHub checks as passing.
- Before live installation verify the installed revision and independent
  recovery. Use the installer for the first integration upgrade if the existing
  update manager lacks the new installation steps.
  PR 60 `473d6e1` builds an untagged version such as `0.2.0.dev+473d6e1`.
  PR 52 `4330aa21` rejects `+` in its version check. The first combined upgrade
  therefore uses the normal installer or `--use-installer` until the installed
  manager supports the combined version format. Do not rely on the older
  README's `--install` promise for that transition. PRs 52, 60 and 74 require
  deliberate deployment-script and test conflict review on the combined source.
  The coordinator's current Mac route uses `192.168.50.1` on `en0` and a known
  SSH check to `192.168.0.17` timed out. The overnight handoff does not establish
  current management recovery. Keep deployment preparation local until an
  independent recovery connection is confirmed.
- On the Pi check signed install and rollback, the actual installed version,
  certificate trust and the neutral address from both networks. Exercise failure
  recovery with private backups. The coordinating chat owns these actions.

## Data, compatibility and rollback

No new device history or user data is introduced. The browser receives update
status only after authorization. It never receives the GitHub token. Owner
signatures still govern installation. The token remains read-only for this
repository and is entered through stdin.

Prepared files need private permissions, bounded size and cleanup after failure.
Keep a complete recovery copy discoverable throughout replacement, including
an interruption between filesystem operations. Keep current trusted keys, the
release floor, current token and CA
identity through rollback. Preserve configuration, access rules and the running
Wi-Fi. Check compatibility with older schedule formats and previous releases
before choosing a rollback target. No database migration is proposed.

The repository has no PR risk template in the inspected `.github` directory.
Treat installation, root file permissions and upload concurrency as high risk.
Require independent review of those changes before readiness. Reverting a
code change alone does not undo an installation, which needs its verified
recovery copy and service restoration.

Neutral sign-in and neutral discovery are separate existing work. The combined
release must satisfy them before deployment. This plan adds no new visual
design, framework or runtime dependency.

## Open decisions

- Human feedback and implementation approval received on 3 October.
- The final combined base and branch owners for any conflict resolution.
- The consistent snapshot boundary for mutable database and configuration data.
- Safe upload ownership when a helper response times out while staging may
  still be running. Do not release ownership on an uncertain result without
  establishing that later requests cannot replace its input files.
- Separate bounded staging ownership that keeps Panic usable during root work.
- Interruption recovery and backup replacement rules, including the total
  preparation/install/recovery time allowance.
- Root certificate permission restoration when rolling back to code that does
  not include the local CA module.
- Complete local workflow coverage for the final combined commit while GitHub
  checks are unavailable. Billing is not a verification blocker.

## Independent plan review on 3 October

The read-only source review supports all six repair scopes and considers this
plan suitable for human review. It found three decisions that must be resolved
before high-risk implementation: durable upload ownership, Panic during root
staging and interrupted backup replacement. Those requirements are incorporated
above. This was an independent review during the current continuation rather
than an earlier external review round.

The review also requires stale-token and revoked-access checks, recovery from a
failed online-status read, daily staging outside the current browser, signed
manifest versions on both transports, current CA preservation and visible
permission failures. Prepared install inputs must work offline on the actual
Pi platform. Bound command output while reading it rather than truncating an
already unbounded buffer.

No production file, remote branch or live Pi state changed during that review.
Human approval is complete. The concrete ownership and recovery designs are
reviewed before their patches as recorded below.

## Upload ownership design

The dashboard takes a nonblocking filesystem lock on
`/run/jittersim/release-upload.lock`. The root helper creates this regular
root-owned file once with mode 0640 and group `jittersim` in its protected
runtime directory. The dashboard opens it read-only and may take the lock.
It cannot replace the file or its parent. Cleanup never deletes or recreates
the anchor. Runtime-directory replacement is allowed only after all dashboard
and helper lock holders have stopped. Rollback replaces application data after
those services stop and does not replace this runtime anchor.

The dashboard uses its private spool for request inputs. It
records an upload identifier before creating that request's folder then streams
the archive and signature into unique input files. The lock stays held through
body receipt. A completed body publishes a ready handoff with a bounded start
allowance before the dashboard releases the lock and calls the helper.

The helper accepts only a validated identifier. Root takes the same filesystem
lock before checking the handoff identifier and start allowance. It holds the
lock through copying, signature checks, staging and cleanup. Root copies into a
unique private incoming folder using regular files without following links.
The signed manifest and sequence checks remain authoritative.

A second upload is refused promptly while receipt or root work holds the lock.
A ready handoff also reserves the input until root starts or its allowance ends.
Expiry only decides whether root may start. It never permits cleanup while
root owns the physical lock. Lease timestamps use the machine's monotonic clock
shared across processes. Wall-clock corrections have no effect. Ready lasts at
most 120 seconds. The record includes the machine boot identity from the kernel.
Root refuses a handoff from a different boot even when monotonic intervals
overlap. Inactive old-boot records may be cleared under the lock. A record with
a future start or more than 120 seconds remaining is invalidated instead of
extending ownership.

After an uncertain helper answer the dashboard may discard input only by
reacquiring the lock and invalidating that exact handoff. Any delayed helper call
must check the identifier under the same lock before it reads. If root still
owns the lock, the dashboard retains the inputs until root finishes. No uncertain
install or upload is replayed automatically.

A dashboard restart leaves a durable receipt record before any UUID directory
is created. Each input file is flushed and synced before the ready record is
published atomically and its parent synced. A partial body with no
physical owner may be removed. A ready handoff remains reserved for its start
allowance. Orphan cleanup is bounded to the known upload folders and validates
their types. Abandoned root incoming folders have a bounded cleanup path while
root owns staging. Legacy calls without an identifier cannot run while an
identified handoff exists. Mixed versions refuse the unsupported handoff
capability visibly instead of reading a shared filename.

All metadata is untrusted. Read at most 1024 bytes through a no-link descriptor.
Accept exactly a lowercase 32-character hexadecimal identifier, the known phase,
the machine boot identity and finite monotonic timestamps within the lease ceiling. Open directory
components and input files relative to validated directory descriptors with
`O_NOFOLLOW`. Root copies only regular files into a root-owned 0700 folder.
The existing 25 MiB archive and 16 KiB signature bounds remain.

At most eight known request folders may be scanned or retained. Scan at most
32 directory entries then refuse further work visibly. Cleanup touches only
the two expected input leaves and known records without recursive traversal.
Root incoming folders use their own stable staging lock, unique names and the
same bounded orphan cleanup. That lock is held until root actually finishes.
If cleanup reaches a cap, refuse admission while preserving any active input.
Crash after receipt publication permits cleanup before a later admission.
Crash before ready leaves partial files. Crash after ready retains the start
lease. Crash after staging but before cleanup permits bounded cleanup on the
next root operation without replaying installation.

Keep this small filesystem handoff in an internal `release_uploads.py` module
with no new dependency or background worker. The standalone recovery manager
keeps its package-independent install and rollback functions. Its upload entry
is used by the installed helper and may call this internal package module.

Two new tests already fail against the threaded server: a second upload replaces
inputs while root is held before its copy and a lost helper answer deletes the
input before root reads it. Add restart, expiry, cleanup, unsafe-file and
mixed-version checks before implementing those boundaries.

## Additional failing evidence

A temporary local probe on PR 66 used the real helper server, client and network
applier with fake network commands. It held staging work while keeping the
actual helper request handler then sent Panic through a second helper connection.
Panic failed after 3.0
seconds with `helper-busy`. No network-release command ran and household access
was not disabled. This reproduces the helper-lock defect without claiming a
real Pi result or replacing the request handler with a stand-in. The test
deliberately fails its requirement that Panic succeed during held staging.

Probe: `/private/tmp/jittersim_staging_panic_probe.py`.
Result: `/private/tmp/jittersim-staging-panic-review.log`.
It changes no repository source and uses no actual token or signing key.
