# Compose protected-device ads and connection patterns

## Reviewer summary

Combine Delivery A connection patterns and optional protected-device ad blocking
on the reviewed maintenance source during preparation then incorporate the actual
published main before the final freeze. Keep the pattern changes first then the
ad changes in one isolated composition checkout. Patterns change
only two helper behaviors while ads change its startup, stop and DNS paths.
Keep the two feature edits separate and review their final composition before
publication. Both original feature approvals remain evidence for their original
sources rather than approval of the combined result.

The important risks are losing preserved traffic counters, using the wrong MAC
when retaining a browser connection, restarting ad blocking during an automatic
profile update and recording protected-device pattern history. Preserve the
current maintenance deployment and workflow repairs. Review one exact combined
Panic consumer bundle and test the final composed source. If the features are
published separately, each actual pull request needs its applicable release
checks. A successful test of an earlier feature cannot establish the safety of
this composition.

## Exact inputs and ownership

- Ad source: `83e66554d56590894c3e285018657a94f05d4d80` in the protected-device
  checkout. Its final source review is recorded in
  `/private/tmp/jittersim-adblocking-83e6655-independent-review.json`.
- Pattern source: `01bde895f10e5db412e0bf3156041bd591f6ff8a`.
- Actual common ancestor: `f3e882c2685fe3810b1f7b6dcc58a10807509c8b`.
  The ad implementation's scoped base is `c1b709d20972ace146af5b1de2253a00b9358c9b`.
  These are different bases. Do not replace current main with either full tree.
- Reviewed maintenance source: `04c982d6150d225c3be6cc082573afc64a656842`.
  The published maintenance revision is still pending. The coordinator owns
  that release and authorized local preparation on this exact source while
  its final release checks run. The original ad checkout remains frozen.
- Preparation checkout starts at the original pattern head. Record and preserve
  both original feature heads before merging the reviewed maintenance source
  then the intended ad changes. The coordinator owns Docker and remote writes
  until the combined source has been reviewed and a release slot is assigned.
- Composition checkout: `/Users/samantha.martinez/.codex/worktrees/neutral-sign-in/jittersim`
  on `codex/feature-composition`. The original pattern and ad heads remain
  reachable through their original feature branches.
- Ten overlapping feature files were inspected: Wi-Fi workflow, README,
  dashboard HTML and JavaScript, API server, models, storage, browser proxy,
  networking helper and updater.

## Local preparation and final integration

1. Save this plan in the composition repository before implementation. Record
   both input heads and the reviewed maintenance source. Merge that maintenance
   source into the pattern checkout then merge the ad head. Compare each feature
   against its original base so maintenance changes are preserved. After main
   is published, incorporate its actual revision and review any differences
   before freezing the final source, building a wheel or running Linux checks.
2. Apply Delivery A's intended behavior onto main. Keep the current installer
   source repair, release deadlines and process cleanup. In particular its older
   workflow has a ten-minute Linux deadline and removes `--init`. Those values
   must not replace the maintenance release's thirty-minute deadline and init.
3. Apply the ad changes onto that result. Resolve every shared file by behavior.
   Do not choose either entire file during a conflict. Keep ad preferences off
   by default and independent of profiles, quotas, pattern seeds and schedules.
4. Inspect the complete new consumer sources and report their exact hashes
   for the coordinator’s independent review before accepting their new tuple
   in the updater. Preserve the complete original approved tuples needed for the
   intended upgrade and rollback paths. Keep the producer and version unchanged.
   Never create every possible combination of helper and access digests.
5. Freeze the composition for a full review and release checks. Any subsequent
   fix invalidates affected source evidence and needs appropriate rechecking.

## Shared behavior that must survive

| Owning code | Required composition and concrete risk |
| --- | --- |
| API apply publication and helper apply result | Keep `counters_preserved` handling in the helper response, plan count, quota rebasing and scheduler activation. Keep ad configuration synchronization and explicit Start's ad resume after the durable Panic write. Replacing either block can discard accounting or grant permission against an older stop record. |
| Helper automatic updates and recovery | Scheduled pattern changes must not resume ads after Panic. Their queue updates should not rewrite the private guard when no explicit start occurs. Startup's `recover-reset` must remove only profile state while leaving current ad permission unchanged. Deliberate Reset and Panic must still pause ads. |
| Browser proxy | Keep ad checks before public DNS lookup and keep household rules ahead of ad allowances. Delivery A retains valid existing tunnels after changes. Its identity check must include ad blocking when profiles and household MAC rules are off. Otherwise a previously identified ad client can lose its identity during reevaluation. Preserve current verified MAC, selected mark, assignment and destination permission checks. Test the intentional existing-connection limitation without treating it as retroactive filtering. |
| Storage and saved settings | Keep profile `random_mode` validation and serialization alongside both ad preference tables and saved-settings fields. Preserve all transactions and automatic table creation. Export, import, history restore and legacy imports must preserve both features. Root run permission must remain outside these saved copies. |
| Protection and pattern history | Delivery A deletes device, assignment and quota pattern events when a device is protected. Keep that behavior alongside ad-choice preservation during rename, removal and private-MAC move or undo. Enabling ads must not add protected targets to pattern history, traffic counters or domain observations. |
| Pattern session state | Ad edits bump the shared configuration version. Pattern session replacement currently compares its source profile values and target rather than that version alone. Keep that distinction. An ad switch or allowance edit must not choose a fresh seed or restart unrelated pattern sessions. |
| Dashboard authentication and private data | Keep both private-data clearing hooks, token revision checks and late-response guards. Ad lost-reply readback must remain read-only and never repeat its mutation. Pattern history requests and ad replies must not repopulate private data or overwrite a newer recovery message after sign-out. |
| Workflow and packaging | Retain both `patterns` and `ad_blocking` browser commands plus native ad DNS checks. Discover the final configured commands and capabilities instead of assuming previous counts. Build the final source wheel and clean Wi-Fi image, verify every packaged file and run the supported Python versions. Preserve bounded containers, owned cleanup and nonzero failure results. |

## Meaningful combined checks

- Protect one client with ads on and another with ads off. Run fresh and saved
  patterns for a third client and the general profile. Only the third client
  and other affected clients should receive changing limits or pattern events.
  Selected protected DNS should block the controlled ad domain and allow useful
  DNS without saving query history.
- Change ad selection and allowances during an active fresh pattern. Verify
  session identity, seed, quota totals and existing queue counters are retained.
  A scheduled reroll can change its expected settings without resuming paused
  ad permission or clearing ordinary access rules.
- Put pattern apply and ad enable in queues before Panic. Exercise dashboard
  Panic and helper or CLI Panic. Both stale operations must refuse activation.
  Cover failed recovery, helper restart, a replaced older approved Panic record,
  dashboard startup without the helper and the eventual settings copy.
- Reset, explicitly enable ads and restart the dashboard and helper. Profiles
  stay stopped and ad permission bytes remain unchanged. A later Reset or Panic
  stays authoritative. Removing protection while stopped must remove the old
  cached MAC before it can be advertised as the saved selection.
- Keep a valid protected browser tunnel while pattern queues change. Confirm
  identity changes and household denies still reject it. Verify ad checks for
  new tunnels with all profiles stopped and through subsequent plan changes.
- Export, import and restore a fresh-mode profile together with two protected
  ad choices and allowances. Cover legacy files without ad fields, explicit
  empty ad fields and private-MAC move or undo. Imported configuration cannot
  restore permission to run after Panic.
- Delay pattern history and ad replies during sign-out and token replacement.
  Verify private tables, fields and status remain cleared. On phone and desktop
  check both controls together with normal and enlarged fonts.
- Test queue changes and ordinary DNS fallbacks in Linux. Kill or stall the
  blocker and controller under the deployed service permissions. Check routing
  expires within the measured five-second bound while household rules remain
  effective. Include traffic-counter preservation and protected exclusions.

Run these targeted checks before the final complete local pipeline. The final
pipeline must include all configured browser and Node journeys, visual capture,
Python 3.9, 3.11 and 3.13, clean package and container builds, installer permissions,
privileged network scripts, kernel checks and locked package preparation. Retain
exact source and artifact identities, test skips, failures, copied reports and
owned cleanup results. Physical Pi throughput and multi-client acceptance remain
separate checks with a verified management route. This plan authorizes no Pi write.

## Recovery and boundaries

The feature owns no new external administration endpoint or DNS history store.
Pattern records are bounded configuration choices, with protected-target removal
preserved. Ad choices and allowances are saved configuration. DNS caches stay in
bounded memory and controller files retain no query histories. Owner APIs retain
existing authentication and input limits.

Before publishing, verify upgrades and rollback through each approved consumer
bundle. Retained ad run permission must be invalidated by newer Panic even if
its JSON bytes match an older record. A failed ad settings copy remains visibly
unconfirmed. Package recovery must stop the optional service and remove only its
routing without preventing ordinary profile or household cleanup.

CI may be replaced by complete local counterparts only when actual hosted CI
is blocked solely by billing. A real test failure, unrun command or unexplained
skip remains a blocker. Decide whether to use one composed feature pull request
or two separately verified pull requests after reviewing this composition.
