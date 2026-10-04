# Preserve evidence for JitterSim outages

## Human review summary

Keep useful Pi logs after a restart and match failures with Apply, Reset,
scheduled changes and Wi-Fi operations. Reuse systemd's journal and the existing
500-entry action history. Add safe start and outcome records plus a root-only
manual export. Do not add another service or automatic router contact.

For an otherwise default journal configuration propose a 64 MiB disk target,
at least a 256 MiB free-space reserve, seven days of retention and smaller
individual files. Preserve larger existing reserves and administrator settings. Journal cleanup does not impose an
exact byte ceiling or guarantee seven days of evidence. Export must have its
own strict size, entry and time limits. A logging failure must leave Apply,
Reset, Panic and management recovery usable.

The main risks are changing shared system logging, filling a small SD card,
retaining sensitive failure text and overlooking the separate update path.
Review existing failure messages before making them persistent. Export only
validated fields and safe event categories. Do not export arbitrary exception,
command, kernel or network message text. The proposed first version uses a local
command run as root so no new dashboard route or journal-read permission is
needed.

This improves future evidence. It does not identify or repair the cause of the
two Vodafone outages and cannot recover already lost logs. A controlled reboot
of a disposable Linux VM is required before claiming reboot persistence.

Review status: source review complete with concrete coordination feedback.
Samantha's direct 24-hour authorization was verified in the coordination chat
turn `01a10087-c3bd-73f0-aef4-998f9305bd8c`, message
`01a10087-c3eb-74e1-9480-33c35f9fc687`. It waives repeated plan pauses through
06:51:13 UTC on 4 October. Local implementation is proceeding. Implementation, one final push, PR review and merge
are authorized after this plan review. Live Pi installation and network changes
remain outside this chat.

## Source and ownership

- Feature branch: `codex/outage-logging` in the separate managed worktree.
- Feature base: frozen release source
  `44538ddad13611a91379b1030d6f063d5c54b419`.
- Documentation was carried over from `0f7503b97caf9343d13d67b50bd3ecf78c37fbfa`
  without taking the incident branch's production changes.
- The release owner controls the serial Docker runner and the next accepted
  integration source. Do not use Docker while that final pipeline is active.
- On inspection the current PR route is `integrate/main-into-stack`, followed by
  main PR 72 and Pi-line PR 45. Integration is
  `0a41c2faf2441c8dadcbd029c8a4458cffd89d9f` and main is
  `f373d82d23ec75eaa9c401af32002af1212879e1`. Recheck before publication.
- Keep this feature in a later batch. Review and check any source changes needed
  to bring it onto the release owner's accepted base before the final push.

## Evidence and review findings

The incident investigation could read only the Pi's current boot journal after
both the Pi and Vodafone router had been reset. During the failure the router's
Wi-Fi was off and the Pi advertised the copied Vodafone name. Healthy recovered
settings cannot establish the earlier connection path or identify the trigger.
The incident findings remain in `docs/router-incident-findings-2026-10-04.md`
on the incident worktree and are evidence rather than a repair claim.

1. `config/storage.py` already stores the newest 500 action records on disk.
   Each contains wall-clock time, action path and HTTP status. It has no start,
   operation identity, elapsed duration, boot identity or configuration version.
   Keep that existing format compatible. Do not add a database migration merely
   to duplicate the journal.
2. `api/wifi_operations.py` holds a single unrecorded completion. The server
   consumes it during a later request. A crash or lack of follow-up reads can
   lose the final persistent record. Consuming this history can also raise a
   storage error before the next Wi-Fi action starts. Safe diagnostic outcomes
   must be written at completion and a failure to save action history must not
   block the next recovery action. Preserve settings-save failures separately.
3. `engine/runtime.py` records scheduled results but important failures can be
   known only through a log message. Add safe operation records at actual apply
   boundaries and distinguish failure, cancellation and uncertain completion.
   Do not record every unchanged scheduler tick or client observation.
4. The helper currently includes command failure details and standard-error
   text in an apply failure message. Other services include exception text.
   Review these paths for request data, network destinations and credentials
   before enabling persistence. New records must never accept free text from
   exceptions, request bodies, subprocess output or user labels.
5. `deploy/raspberry-pi/install.sh` and `updates.py` have separate installation
   paths. The update manager also keeps a recovery inventory. The owned journal
   configuration must be installed, preserved or removed correctly through both
   paths, interrupted updates and rollback to a release without this feature.
6. The installer requires NetworkManager 1.50 or newer but does not pin a
   Raspberry Pi OS or systemd release. Check journal capability and configuration
   rather than assuming an OS version. Do not modify the live Pi to find this.
7. Journald configuration applies to the system journal, including other
   software. Preserve explicit administrator storage and retention choices.
   Report an evidence gap if persistence is disabled or policy cannot be safely
   interpreted. Do not silently overwrite it or broaden journal access.
8. No disposable Lima VM is currently configured. Docker tests cannot establish
   survival of a real reboot. Prepare an isolated Linux VM after plan feedback
   and coordinate resource use with the release owner. VM availability and the
   next accepted feature base remain release prerequisites.
9. `cli.py` already has `diagnostics` and `logs` commands. Keep their current
   invocation compatible. The logs command clips output after a complete
   subprocess capture, so reuse its service names rather than its collection
   method for the bounded export.

## Proposed work

### 1. Bounded local journal retention

Use a clearly named project-owned configuration file. For otherwise default
settings propose `SystemMaxUse=64M`, `SystemKeepFree=256M`,
`SystemMaxFileSize=8M`, `MaxRetentionSec=7day` and `MaxFileSec=1day`.
Keep a larger effective free-space reserve, including systemd's default, rather
than lowering it to 256 MiB. Set limited memory fallback storage too. Avoid changing administrator choices
for storage, access, message forwarding or unrelated service rate limits.
Apply bounded repeat suppression to JitterSim's own records. Do not describe a
journald burst value as a strict message cap because systemd adjusts it for
available disk space.

Inspect the effective main configuration and drop-ins before writing. Preserve
explicit settings, masks and stricter limits. If configuration is ambiguous or
incompatible, skip the optional change and report the gap. Use safe root-owned
paths, atomic replacement and bounded commands. Repeated installation must be
safe. Restart or flush only the logging service when required, never the Pi or
network services as part of preparing diagnostics.

Removal must affect only the owned configuration. Do not remove the shared
journal directory or vacuum other software's logs. Document that removing the
configuration does not delete previously retained logs. Update recovery must
preserve the administrator's current policy and accurately handle the owned
file across older releases.

### 2. Safe operation records

Use a small fixed set of actions and outcomes. Record operation identity,
configuration version when already available, phase, duration and safe failure
category. Add a local sequence and elapsed timestamp so ordering remains useful
when wall-clock time changes. Read boot identity once or use the journal's
trusted boot field. Do not perform extra database or network queries to enrich
records.

Emit a start at the actual operation boundary and an outcome when work finishes.
Cover dashboard Apply, Reset and Panic, scheduled helper changes, Pi Wi-Fi
changes and manual router Wi-Fi changes. Do not add router reads or scheduled
probes. A start without an outcome means completion is unknown. It is not proof
of success or a specific crash cause.

Writing must be best effort with bounded messages and nonblocking delivery.
Missing journal sockets, full buffers, disk errors and malformed diagnostic
state must not change action results or hold action locks. Repeated failures
must not create unlimited messages, workers or pending records. Keep existing
action history behavior compatible.

### 3. Limited manual export

Provide `sudo jittersim diagnostics export` through the existing local command
path. Require root before reading private files or journals. Do not add the
dashboard user to a journal-reading group or create a remote export endpoint.
Use a fixed set of relevant JitterSim units plus selected kernel and
NetworkManager event categories from the current and previous available boot.

For the first version propose a strict 1 MiB result limit, at most 1,000 input
entries per boot and a ten-second overall read deadline. Bound each input line,
field, child-process output and stderr. Select journal fields before reading
where supported. Stop and report truncation or unavailable evidence. No shell
execution and no user-supplied unit name, journal path or arbitrary filter.

Export safe generated summaries and validated metadata. Do not copy arbitrary
`MESSAGE` text. Omit names, IP or MAC addresses, Wi-Fi names, credentials,
request identifiers used for authentication, DNS names, URLs, browser history,
traffic contents and protected-device observations. Include only a bounded
selection of existing actions with known action names. Unknown event text is
omitted with a count. Avoid retaining an extra export copy inside the database
or update recovery snapshot.

## Verification and release

- Add focused checks for real risks: fixed-field validation, recognizable fake
  secrets in errors and command output, operation completion without polling,
  failures during logging, cancellation and uncertain completion.
- Check bounded repeat handling, export bytes, entry count, oversized journal
  fields, child-process deadline, denied access, missing boots and clock changes.
  Verify logging and export cannot block Apply, Reset or Panic.
- Check default journal setup, explicit administrator policy, masks, unsafe
  links, low space, repeat installation, update, interrupted recovery, older
  release rollback and removal. Use a clean Linux environment for deployment
  changes and preserve unrelated settings and files.
- Use a disposable Linux VM with journald. Save a recognizable safe marker,
  confirm storage, reboot the VM and read that marker from the previous boot.
  Test configured rotation and age cleanup separately. Record exact systemd
  version, source commit, configuration and commands. Do not call a container
  restart proof of reboot persistence.
- Run the appropriate configured Python, lint, package and Linux checks against
  the exact final feature source. Use the release owner's shared-runner order.
  Changes to shared build or update behavior require clean-package checks.
- Review the complete final diff for privacy, recovery, concurrency, compatibility,
  bounded work and future growth. Resolve blockers before one final push.
- Recheck the accepted integration source and PR requirements. Create and attach
  the focused PR, inspect reviews and jobs then merge through the agreed route.
  Hosted jobs stopped by account limits remain unavailable checks. Use complete
  configured local equivalents only within Samantha's verified authorization.
- Hand over the final commit, actual test results, evidence gaps and removal
  commands to the release owner. Do not install on the live Pi, activate a profile,
  restart household equipment or reproduce the outage in this chat.

## Documentation sources

The reviewed systemd 252 sources describe persistent and memory storage,
configuration precedence, flush behavior, size limits and cleanup of archived
files. They also document that changing back to memory storage does not remove
existing persistent logs and that rate limits change with available disk space.

- [Journal configuration](https://github.com/systemd/systemd/blob/v252/man/journald.conf.xml)
- [Journal reader](https://github.com/systemd/systemd/blob/v252/man/journalctl.xml)
- [Configuration precedence](https://github.com/systemd/systemd/blob/v252/man/standard-conf.xml)

## Central learning objective

Correct operation results and useful incident evidence are separate checks.
After the authorized implementation, evaluate what can be concluded from a
start record without an outcome and why clock time alone cannot order events
across an unsynchronized reboot. Keep that check focused on these decisions.

## Later release safety gate

The update manager admits exact reviewed Panic producer and consumer file
identities. Changing the helper for logging changes that identity even when
Panic behavior is intended to stay the same. Before admitting the new helper,
review the complete producer and consumer files, run recovery regression checks
and obtain an independent complete-file review through release coordination.
Keep the original production identity accepted. Do not add a new identity merely
because its hash was calculated. This gate follows the accepted main source
and does not change the current frozen release.

## Implemented local design

The first local implementation keeps project policy in
`/etc/jittersim-outage-journal` and its journal link under
`/usr/local/lib/systemd/journald.conf.d`. A small root-owned policy utility is
retained at `/usr/local/lib/jittersim-outage-journal.py`. It is outside the
application replacement and signing-trust recovery paths so an older release
can restore the prior journal storage behavior. It adds no service.

If persistence was previously off, removal retains a small project-owned
`Storage=volatile` compatibility file. Deleting that file while the shared
journal directory remains could turn persistence back on through
`Storage=auto`. If persistence was previously on, removal deletes only the owned
link and policy files. Neither path deletes previous logs. A new administrator
policy or edited owned file is preserved and causes optional setup to stop.

Messages use native nonblocking journal writes on Linux. Fixed log templates
keep numeric details but omit string arguments and exception text. Unknown
templates are omitted. Scheduler and ordinary messages have a separate bounded
budget from manual operations and recovery. Diagnostic identities use a bounded
local sequence and process seed, not authentication identifiers or random-token
generation. No extra database or network reads enrich operation records.

The VM reboot proof is prepared in
`tests/integration/linux_outage_journal.py`. It records safe markers under a
known unit, records module hashes, requires a changed kernel boot identity and
checks both markers in the previous boot after reboot. It has not run yet. The
release owner's active pipeline still owns the resource slot.

Local macOS checks do not establish Linux installation or reboot persistence.
The final Linux checks, accepted-main update, independent complete-file Panic
review, source admission, final push, PR review and merge remain required.

## Local verification checkpoint

The local implementation is complete but is not ready to install or merge.
The exact source identities and check results are recorded in
`docs/verification-outage-logging-2026-10-04.json`.

The first frozen macOS run passed 5,492 tests, skipped 41 and failed 12. Eleven
failures came from the unchanged Panic source review gate. The other phone
help-tip assertion also failed on an isolated archive of the untouched base.
No frontend source or assertion was changed for this feature. Earlier focused
checks passed 800 tests. After the vendor-policy and repeated-removal corrections
181 focused checks passed. The exact final diagnostic and policy modules passed
60 focused checks. Error lint, shell syntax, compile and whitespace checks passed.
A locked package build from a fresh source directory passed with fresh build
and checking environments. All packaged modules and dashboard assets matched
the final source. These macOS checks do not replace the configured Linux jobs.

Local review also closed raw socket-server traceback paths and unhandled
service or thread failure messages. Safe logging is configured by access
recovery and ad-blocking entry points. Unexpected failures use a fixed message
without claiming that a whole service stopped. Explicit main journal policy
is preserved in vendor and runtime locations too. Repeated removal avoids an
unnecessary second logging-service restart.

The release owner still controls the accepted base and serial resource slot.
No VM was started. The prepared VM configuration passed Lima validation.
Independent complete-file approval, final source admission, Linux checks, an
actual reboot, rotation and age cleanup remain prerequisites. No remote push,
PR creation, merge, Pi installation or household network change occurred.

## Disposable retention proof preparation

`tests/integration/linux_outage_retention.py` follows the actual reboot proof.
Both scripts refuse containers, physical hosts, unrecognized virtualization
and missing disposable-VM permission. The retention script also requires a
separate `JITTERSIM_DISPOSABLE_RETENTION_PROOF=yes` permission inside the guest.
It cannot run through the Docker test route. No VM or real clock was changed
while preparing or checking these scripts.

The reboot verifier saves a private result and filtered export. The retention
proof requires that same-source result before proceeding. It preserves up to
64 archived journal files within a 96 MiB total bound in a fresh private guest
evidence folder. It refuses an existing folder or insufficient space.
It keeps the actual one-day file rotation and seven-day retention policy.

After saving clock and time-sync recovery information it advances only the
verified VM guest clock by two days. A new safe marker must cause automatic
rotation of the earlier active journal. The proof then advances the guest by
eight days and requires automatic removal of that old archive. It issues no
manual age-vacuum command and does not shorten the retention setting. It checks
that size and free-space limits cannot explain the earlier archive removal.
Safe exports and the saved original journals remain available for review.

The helper has a 120-second work deadline, fixed command timeouts, bounded
archive copies and a separate five-second restoration allowance. Normal
completion and raised failures restore the original guest clock and time-sync
choice. Abrupt termination can interrupt restoration, so discard the disposable
VM if the proof is killed or its state is uncertain. Never run this test on the
Pi, a host system or a container. The host and shared Docker clock remain outside
the proof. Actual rotation and cleanup evidence is still pending.

## Unsupported export host correction

Export now refuses non-Linux hosts or unavailable root identity support with a
clear Linux/Pi requirement before reading evidence. The existing Linux root
check and all privacy limits remain in place. A controlled child process with
`os.geteuid` removed receives exit status 1 without a traceback or private
reads. The focused export and CLI run passed 44 tests. The new exact export
and CLI hashes are recorded in the verification file. Earlier full-suite and
wheel evidence keeps its original source scope. Final accepted-source checks
and the independent admission gate remain pending.
