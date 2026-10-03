# JitterSim stable release checklist

Template for the stable release design approved on 2026-10-03. The release
system is not implemented yet. Copy this for each numbered candidate once
the system is operational. Mark a conditional
item inapplicable only with its written reason. An unchecked required item
blocks stable publication. A release may proceed only with explicit
approval of the completed record.

## Identity

- Version and final tag:
- Full source commit:
- Reserved release order:
- Previous stable release:
- Required CI run IDs and source commit:
- Runtime/build lock identity for each supported target:
- Archive SHA-256:
- Signature SHA-256:
- Signer public fingerprint:
- Evidence bundle location and SHA-256:
- Supported Pi model, architecture, OS and Python versions:
- Supported upgrade starting versions:
- Supported recovery target and known limits:
- Release owner:

Keep tokens, passwords, device identifiers and recovery-backup details out
of this record. Hashes of release packages and public signing fingerprints
are suitable evidence.

## Prepare

- [ ] The release plan has human review and feedback before implementation.
- [ ] Accepted update and version work is integrated into main.
- [ ] Source belongs to main and has the complete required passing checks.
- [ ] Package versions agree with the reserved final tag.
- [ ] Version and release order are unique and newer than the prior stable.
- [ ] Known supported installations do not have a higher release-order floor.
- [ ] Final archive uses the reserved final label and order.
- [ ] Runtime and build dependencies use the reviewed exact versions and hashes.
- [ ] Release notes state changes, support limits and upgrade/recovery behavior.
- [ ] The retained final archive and signature hashes are recorded.

## Accept the exact package

- [ ] Complete supported Python suites and configured lint pass.
- [ ] Clean installed-wheel build and dashboard assets pass.
- [ ] Every required Linux and browser command passes for the pinned source.
- [ ] Actual installed application comes from the final archive.
- [ ] Signature, manifest, tag and full source identity agree.
- [ ] Unsupported target or altered dependency inputs refuse before stopping services.
- [ ] Physical Pi management access is independent of the test connection.
- [ ] Physical dashboard, shaping, protected-device and reboot checks pass.
- [ ] Upgrade from every declared supported starting version passes.
- [ ] If this is the first release the actual legacy upgrade route is proven.
- [ ] Failed-install recovery and explicit recovery pass with current safety state preserved.
- [ ] Saved settings and current signing trust survive the supported recovery.
- [ ] Required tests are not missing, silently skipped or described as passing when unavailable.
- [ ] Full-change review has no unresolved release blockers.
- [ ] Evidence identifies any permitted local equivalent and its explicit approval.

## Stable approval

Approver:

Approval date and time with timezone:

Approved version, commit, release order and both package hashes:

Evidence reviewed:

Known non-blocking limitations and why the declared support remains valid:

- [ ] Samantha approved this exact record after reading the release summary.
- [ ] No source, package, dependency or target-support change invalidated approval.

## Publish and verify

- [ ] Remote final tag resolves to the approved commit without replacing a tag.
- [ ] Publication starts as a draft with tag verification.
- [ ] Draft has exactly `jittersim-release.tar.gz` and `jittersim-release.tar.gz.sig`.
- [ ] Both downloaded draft assets match the approved hashes.
- [ ] Downloaded signature verifies and the archive manifest still agrees.
- [ ] Release notes include the approval summary and support/recovery limits.
- [ ] The complete draft is published as a full release with deliberate latest selection.
- [ ] Actual latest-release API returns this intended stable release.
- [ ] The real Pi online reader sees and verifies the approved release.
- [ ] A declined installation leaves the Pi's working installation unchanged.
- [ ] Evidence is retained outside temporary CI storage.
- [ ] Native immutability is confirmed or its enforcement limit is documented.

## Release notes outline

What changed for the user:

Supported devices and operating systems:

Supported upgrades and any required migration:

Recovery target and limits:

Known issues:

Validation summary with evidence links:

Version, commit, release order and archive/signature hashes:

Stable approval:

## If the release fails

Keep the retained package identity and the existing stable release. Resume
the same draft after checking its actual state. Do not create another release
to guess whether a timed-out write succeeded.

For a published defect record the warning and verify any latest change.
Prepare a corrected higher version. A latest change does not revoke a staged
package or repair an already installed device. Use only its supported recovery
path with independent management access.
