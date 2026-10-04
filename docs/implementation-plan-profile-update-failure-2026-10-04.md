# Profile update repair

## Human review summary

Fix a reproduced cause of failed profile updates: a statistics read holds the
helper's network-operation lock while the profile client checks supported
features. That check is refused immediately so the actual apply request is
never sent. The dashboard then reports `network helper apply failed` even
though the settings were saved.

Allow the helper's fixed capability flags to be read without taking that
lock. Actual network changes keep their existing lock and bounded wait.
There is no automatic retry, database change or new endpoint. Keep the
connection limit and Unix socket permissions unchanged.

The original log was lost when the Pi restarted so this repair cannot prove
the cause of that exact historical attempt. The failure was reproduced with
the installed package and current integration source. The main risk is
accidentally allowing mutable network state to be read without its lock.
Limit the exception to the existing static `capabilities` response.

Review this summary and the review below before implementation. The owner
has already requested the fix and final push. Plan feedback is still required
by the personal working agreement.

## Evidence and scope

- Management SSH now works and Ethernet has carrier.
- The Pi journal is volatile. Only the boot starting at 00:52 on 4 October
  Berlin time is available. The original failure predates that boot.
- The current journal records a `gateway-status` request refused with
  `helper-busy`. This shows contention exists but does not prove which
  request caused the screenshot's failed apply.
- Installed helper, renderer and dashboard blobs match repository snapshot
  `08a93e5dbd3ff23446b232ae5cbf7cc521f686b4`. The reproduction uses a private
  copy of the actual installed package rather than assuming a release ID.
- Holding a statistics read through the real helper socket makes
  `HelperClient.apply` fail before any network write. Installed code reports
  that the helper needs updating. Current integration source reports busy.
- Base `0a41c2faf2441c8dadcbd029c8a4458cffd89d9f` still contains the defect.
  Work is isolated on `codex/profile-update-failure`.
- Screenshot profile values applied successfully in the separate Linux
  experiment. A failure with several individual duplication queues is a
  separate issue and is excluded from this repair.

## Proposed change

1. Add `capabilities` to the request handler's existing immediate-response
   actions in `src/jittersim/networking/helper.py`. Its service response
   contains fixed flags and performs no kernel command or state read.
2. Add meaningful concurrency coverage in `tests/unit/test_helper.py`.
   Confirm that shared-profile and service-list applies can finish their
   capability checks during a held statistics read then wait for that read
   to finish before changing the network.
3. Check that capabilities remain available during a held write. Mutable
   statistics and traffic reads must still be refused immediately.
4. Keep the existing exception-safety test meaningful by using an action
   that still takes the operation lock. Update the existing expectation
   that capabilities are refused during a write.
5. Run the focused helper suite before and after the production change.
   Then run applicable API/profile integration checks, the full suite and
   configured lint. Run the focused socket tests in Linux as well as macOS.
6. Review the complete diff. Make focused local commits then push once and
   open a repair PR against `integrate/main-into-stack`. Read the actual CI
   result and report any infrastructure blocker.

## Review of the proposal

- The capability response is constant for a helper process. It reads no
  active plan, queue counters, address sets or credential files.
- The exception removes an unnecessary prerequisite failure. It does not
  relax serialization of apply, reset, access changes or scheduled changes.
- An older helper still has to advertise the required feature. The client
  compatibility checks remain in place.
- The existing eight-connection limit bounds the number of request workers.
  The repair adds no queue, polling loop, fan-out or retry.
- Tests must exercise a shared or service-list plan. The existing slow-read
  apply test uses an individual plan and never checks capabilities.
- The existing test that expects capabilities to be busy during a write
  encodes the defect. Replace that expectation with constant-read access
  while retaining refusal checks for mutable state.

No blocking design issue was found in this focused proposal. Human feedback
approved implementation and the final push on 4 October.

## Implementation and verification

The helper now answers its fixed capabilities without taking the operation
lock. Mutable reads, network writes, request limits and socket permissions
retain their existing behavior.

The three new socket regressions failed before the production change and
passed afterwards. They cover shared-profile apply during a held statistics
read, service-list apply during the same read and capability access during
a held write. The exception-safety test now uses a locked action. Mutable
statistics and traffic reads remain covered by refusal tests.

The full macOS Python 3.12 suite passed with 3,240 tests and 39 expected
Linux-only skips. Configured Ruff checks and `git diff --check` passed.
Private receipts are `/private/tmp/jittersim-profile-update-full.log`,
`/private/tmp/jittersim-profile-update-full.xml` and the corresponding
`jittersim-profile-update-regressions-before.log` and
`jittersim-profile-update-regressions-after.log` files.

Linux matrix checks remain part of coordinated release verification. The
shared Linux runner is already allocated to that work. No live Pi change
or independent deployment was made. Integration into the composed helper
must preserve its additional immediate actions, policy-socket restrictions
and Panic ordering while adding this static capability exception.

## Delivery, security and recovery

No PR template is present on the base branch. Treat this as a network-control
reliability repair with a small code change but important concurrency checks.
The Python 3.9 and 3.13 CI matrix and Linux socket checks apply. There is no
shared build configuration change, migration or backfill.

This change uses the existing protected local Unix socket. It adds no
permission, public route or credential handling. Private source copies and
reproduction files stay outside the repository.

Deploy the helper and dashboard through the established release path after
the release owner integrates the repair. Do not merge or publish the broader
unfinished release as part of this fix. Before live installation verify an
independent management connection and private recovery backup. Restart the
helper with the verified package. A rollback restores the previous package
and helper service with saved settings preserved. A rollback also restores
this contention defect so it is a recovery measure rather than a resolution.

The live Pi currently needs a sudo password for installation. Do not ask for
that password in chat or copy credentials into logs. A push alone does not
prove the Pi is repaired. Report the installed revision and a repeated
profile update only after live verification actually completes.
