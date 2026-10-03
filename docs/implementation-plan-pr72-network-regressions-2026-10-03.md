# Queue statistics and household access repairs

## Reviewer summary

PR 72 contains three confirmed networking defects. Statistics for supported
queues 10 through 15 are rejected because their identifiers contain
hexadecimal letters. The managed browser can also block a device using a
saved denylist while that list is disabled or a different list is active.
A profile restart after Panic can also restore disabled household device
blocks because experiment rules copy the persistent MAC policy.

Accept the queue identifiers the renderer already produces and make browser
access honor the selected policy mode. Keep the existing input bounds and
active access restrictions. Add regressions that fail on the current source
before changing it. These repairs do not change settings or require a data
migration. The separate DNS worker repair remains with its current owner.

Risk is medium because these paths handle privileged helper responses and
network access. The main risks are accepting malformed statistics or letting
a currently blocked device connect. No open design question remains for
these three defects. CI is currently blocked by GitHub billing. Merge and
release remain gated on passing checks for the combined source.

## Starting point and scope

- Base: cf2a6fe44ec245061839a44b09a57e0b28782a6a from PR 72.
- Isolated branch: codex/pr72-network-regressions.
- Queue validation: src/jittersim/networking/helper.py.
- Browser access decision: src/jittersim/networking/browser_proxy.py.
- Separate rule ownership: src/jittersim/networking/renderer.py.
- Reuse the helper and browser regression suites.
- No repository PR risk template is present in this checkout.

## Decisions

1. Match the queue handle format used by the renderer and Linux. Accept
   lowercase hexadecimal digits within a small explicit bound. Continue
   rejecting punctuation, suffixes, whitespace and oversized values.
2. Apply a saved denylist only when denylist mode is active. Keep allowlist
   mode enforcement and required client identity checks. Preserve protected
   devices' exemption from shaping while enforcing active global blocks.
3. Use focused tests across disabled, allowlist and denylist modes. Check
   both general profiles and the protected-device path. Cover queue marks
   near the decimal-to-hexadecimal boundary and the maximum supported mark.
4. When a persistent access plan exists keep MAC restrictions exclusively in
   its own firewall table. Preserve standalone experiment policies and the
   documented website address blocks. The browser must enforce the currently
   applied access file rather than the older policy embedded in a profile.
   Require verified identity whenever an active applied MAC list needs it.
   Existing file-generation checks protect concurrent access changes.

## Verification

- Write regression tests first and prove they fail on the unchanged source.
- Verify the normal queue identifiers still work and malformed values fail.
- Verify an active denylist blocks its listed device and an active allowlist
  blocks an unlisted device. Disabled or inactive lists must not block one.
- Run the affected unit and browser-proxy integration tests after each repair.
- Prove Panic then Start preserves disabled household MAC rules. Disable
  household controls during an active profile without reapplying its queues.
  Check both nft rules and real browser tunnels with stale embedded policy.
  Active applied blocks must still win over disabled or pending saved edits.
  Cover reset/no-profile state and failed or unavailable applied policy.
- Run the full Python suite and the required Ruff rules on the final source.
- Reinstall the project wheel before browser tests to avoid mixed versions.
- Run the protected-client and general-profile browser journeys.
- Verify the final source in a Linux container. Run only one Docker job at
  a time and preserve the MAIA containers.
- Review the complete repair diff before handing off local commits.
- The coordinator must verify the exact combined source and all published
  CI checks before merging or creating an official release.

## Deployment and recovery

The package and dependencies stay the same. The existing build includes the
edited source files. No database, stored setting, service unit or installer
change is needed for these repairs. The first combined main/Pi release still
needs the normal installer because the larger integration changes install
steps and dependencies. The coordinator owns that release decision.

Rollback uses the existing official-release recovery path. These repairs
leave stored configuration unchanged so restoring the previous source does
not require a reverse migration. Local development does not touch the Pi.

## Security and growth

No new data category is copied, retained or logged. The browser already reads
the applied access file. Resolve client identity for an active applied MAC
list as well as for general profiles and protected clients. No endpoint or
permission is added. Statistics keep their entry, counter and string bounds. Client access
still depends on verified identity and the active global rule mode. The
changes add constant work per existing queue or connection and introduce no
threads, retries, network requests or growing state.

## Work order

1. Review this plan and reproduce the defects with focused tests.
2. Repair queue validation and verify positive and negative cases.
3. Repair browser policy selection and verify every policy mode.
4. Repair MAC rule ownership and verify Panic, live disable and applied-policy
   changes without resetting profile queues.
5. Run final local checks and review focused atomic commits.
6. Hand the tested commits to the coordinator for combination and CI.
