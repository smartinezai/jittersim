# Dashboard concurrency verification

The repair keeps real workers owned until they exit, prevents old network writes from undoing Panic and preserves independent profile and household choices through startup. The current local source is frozen at `6a112f615b09d9a3cc587ab28bdaa04cce18b16f`. Independent source and evidence review approved this exact checkpoint. The full source suite passed 4,089 cases with four existing integration failures owned by the release coordinator. Publication and final combined verification belong to that coordinator.

All tests reported here use `PYTHONPATH=src` with Python 3.12.13 and borrowed test dependencies. They do not establish installed-wheel acceptance, other Python versions, Linux service ordering or physical Pi behavior. No container or live Pi operation was performed for this repair.

## Final behavior

- Request capacity and sockets remain owned until the real worker exits. Unknown startup outcomes fence admission rather than reclaiming a possibly active worker.
- Shutdown closes admission and drains request, router, Wi-Fi, scheduler and address workers before shared state closes. An unresolved owner retains that state and reports incomplete shutdown.
- Manual Start and household intent are captured before their earliest scheduler waits, including Block globally and Unblock. The original server generation and helper revision travel together. An external helper Panic cannot make a waiting request acquire fresh intent afterward. Panic owns result publication and activation. Delayed success or failure cannot replace stopped intent. Failed cleanup remains unconfirmed until a successful fresh Panic.
- Each network write carries one immutable helper control revision captured before reading saved settings or compiling a plan. The privileged network lock rejects old revisions before mutation. New helper processes create a fresh revision. Missing or malformed metadata refuses a live change.
- `/var/lib/jittersim-control/panic-state.json` preserves profile stop, household stop, failed recovery and the permanent control revision requirement outside runtime files and rollback backups. Format 2 uses bounded strict records, private root ownership, link checks and durable publication. Complete known format 1 records conservatively map profile stop to household stop. Both household startup paths inspect household intent before restoring saved rules. Damaged state permits only disabled-access recovery.
- A fresh successful household action clears only household stop after actual policy application and both durable writes. Profile Start clears only profile stop and cannot silently restore a stopped household definition. Failed publication undoes the affected change, blocks recovery and rotates the helper revision. Actual helper reconstruction and both startup consumers preserve a fresh household choice while profiles remain stopped.
- A first upgrade validates the complete old gateway schema including all three known Panic flags before creating persistent state. Missing runtime state or old records without those fields are unsupported. An existing namespace with a missing record requires recovery. Startup never turns an unconfirmed Panic into a successful one.
- Fresh installation uses an explicit producer under the real verified installer lock before package, settings or access point changes. It requires absent installation witnesses and no prior update state beyond the private launcher lock. It never clears or repairs an established guard.
- Every candidate must preserve the established guard, including a currently clear record. An unsupported old consumer can ignore that record during a future Panic. The standalone gate checks the exact reviewed guard and both startup consumers. Candidate files and reviewed source lists have fixed bounds. It does not import a candidate package.
- One explicit update retry crosses the helper boundary with the retained retry identity and no automatic replay after a lost response. A helper with no matching backend refuses the action. The update owner retains retry validation and process ownership in that backend.
- Client scans have one physical flight across invalidations, caller deadlines and generation checks on delivered results. Invalidated identity data cannot reach consumers.
- Saved-plan validation excludes external DNS waits while SQLite holds its transaction. Applying still resolves names outside that transaction.
- Page responses honor explicit gzip refusal and weak conditional validators. JSON retains `no-store`. The per-instance CA page interface was checked separately without editing its owner's checkout.

## Evidence

The retained evidence directory is `/private/tmp/jittersim-pr70-evidence`. Failure receipts remain alongside corrected receipts. The final source manifest includes all Python source hashes and the final focused receipt hash.

| Receipt | Source and result |
| --- | --- |
| `pr70-final-source-unit.xml` | `b4114db`: 3,985 passed and nine failed. Four belong to the combined GUI boundary. Five newly exposed helper capability and transport fixture failures were corrected. |
| `pr70-persistent-source-unit.xml` | `3199a47`: 4,012 passed and four failed in 228 seconds. The remaining device-check fixture, sidebar wording, runtime wording and saved-note wording belong to coordinator integration. |
| `guard-final-consumers.xml` | Final `2a9df5c` production source: 146 passed with zero failures, errors or skips in 22.394 seconds. Covers the helper intent, persistent guard, candidate gate, restart latch, service-address, outage and capability consumers. |
| `helper-retry-final-consumers.xml` | `0c927e9`: 154 passed with zero failures, errors or skips in 25.953 seconds. Independent review approved this adapter source for local integration. |
| `four-flag-final-callers.xml` | `6a112f6`: 146 passed with zero failures, errors or skips in 31.162 seconds. Covers strict producer admission, format migration, both real startup consumers, independent household persistence, failed publication, eager queued Start and household cancellation plus helper retry. |
| `pr70-four-flag-full-source-unit.xml` | Frozen `6a112f6`: 4,089 passed and four failed with zero errors or skips in 239.824 seconds. Exactly the existing coordinator-owned device-check token fixture, sidebar wording, runtime wording and saved-note wording. No new failure from this repair. |
| `panic-independent-intent-three-before-source.xml` | Three actual failures before independent intent and eager capture: household re-enable lost during reconstruction and queued Start plus household apply accepted after external Panic. The earlier receipt without `PYTHONPATH=src` was a collection error and is not causal evidence. |
| `queued-global-household-before.xml` | Actual Block globally and Unblock both returned 200 after an external helper Panic while waiting for the scheduler lock. The final caller receipt verifies their refusal. |
| `clear-guard-old-consumer-before.log` | Frozen `b4114db` old consumer accepted Panic with failed SQLite writes and returned 503. Its persistent clear guard stayed unchanged. After volatile file loss the actual new helper and dashboard replayed the obsolete Apply once. |
| `clear-guard-candidate-before.xml` | The all-false candidate exemption accepted an unsupported package. The final caller receipt verifies refusal before replacement. |
| `guard-legacy-migration-before.xml` | Four reproducible failures at `3199a47`: malformed existing gateway records permitted enabled household restoration or replaced failed recovery flags. |
| `guard-legacy-migration-fixed.xml` | 138 passed after complete old-schema validation. Includes failed gateway persistence followed by runtime loss and helper reconstruction. |
| `persistent-runtime-loss-before.log` | Actual `b4114db` helper and dashboard reconstruction restarted an old Apply after failed SQLite Panic writes and gateway file loss. |
| `control-intent-wire-corrected.log` | 36 passing cases include real serialized stale helper writes, reconstruction and invalid revisions. Earlier transport fixture failures remain in their original logs. |
| `scheduled-external-reset-fix.log` | 111 passed after moving helper reset outside the short Panic publication lock. The before receipt reproduces blocked Panic admission. |
| `ca-instance-transfer-corrected.log` | 12 passing transfer cases against the CA owner's frozen page snapshot. Covers two actual instance pages, gzip, weak validators, cache eviction between selection and send and unchanged JSON behavior. |

The configured workflow Ruff selection passes. `git diff --check` passes. The new standalone guard and candidate test parse with Python 3.9 syntax rules. This is a syntax check rather than Python 3.9 runtime acceptance. Unrestricted repository lint still reports existing findings and is not the configured workflow gate.

The independent review recorded in review chat `01a0feb7-8d05-72d1-bbd2-b1493e62d455` read the complete `0c927e9` to `6a112f6` change and all changed tests. It checked all 69 source hashes against both Git and the working tree. The frozen manifest is `pr70-four-flag-source-freeze.json` with SHA-256 `4a90c7f98802fc07dc9a57f72a37f4021a385a0d7d6cb503057ed6866622960f`. The focused final XML has SHA-256 `6308f7a1664a2941506af5f0fa7e65fe6f288ac9f143fd383b8201e18c651017`. This approval covers the isolated source and evidence. Actual installer ownership, stopped-writer proof and the final combined compatibility bundle remain separate release checks.

## Required combined release checks

1. Preserve the reviewed `6a112f6` source while resolving integration conflicts. Require a new independent review of the final combined source. The local source unit receipt above remains separate from installed package acceptance.
2. The update owner must copy the standalone guard module outside the replaced virtual environment. Run the strict fresh or upgrade producer before installer mutations under proven operation ownership. Read current guard state after writers physically stop. Check candidate compatibility before destructive replacement or service startup. Never restore an old guard or clear its permanent requirement.
3. Test both explicit rollback and failed-install recovery through the actual update callers. Include a Panic racing early eligibility checks, failed SQLite writes, runtime loss and a running installation after a fresh Start. Unsupported candidates must leave current safe source and state available.
4. Freeze the final combined format 2 guard, helper and access source hashes. The isolated three-file bundle is not the final combined bundle because other helper work changes its source. Require reviewed source bundles rather than labels or learned hashes. An unsupported first upgrade or old rollback requires a separately proven recovery route before live deployment.
5. Resolve the four combined GUI failures on the coordinator's source, then run the actual configured workflow checks on the final installed package. Include the required Python versions, browser journeys, clean builds and Linux checks with the intended privileges.
6. Retain hosted billing failures as hosted failures. Report equivalent local workflow evidence separately. Push and merge only after the coordinator's final exact-source release gate succeeds.

The worker reserves do not guarantee unconditional Panic admission. Incomplete requests can occupy them until their finite deadlines expire. Storage failures can also prevent durable acknowledgement. Both outcomes are reported honestly with a bounded retry path. Passing this repair's focused tests does not remove those practical limits.
