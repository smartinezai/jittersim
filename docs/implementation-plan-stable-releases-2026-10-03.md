# Stable releases for JitterSim

## Review summary

Give each stable JitterSim version a tested package, a permanent version tag,
clear release notes and a proven recovery path. A commit reaching `main`
starts automatic checks and creates a development candidate. It does not
become an update offered to a Pi until Samantha approves a specific release.
This approval model was selected on 2026-10-03.

Reuse the signed archive and online update work already underway. Keep one
stable release channel and one release owner. Prepare the final numbered
archive before acceptance testing. Publish those same bytes after approval.
Keep the signing key on the owner's Mac for the first implementation.

The first stable release is blocked until the integrated source reaches
`main`, its dependency versions are fixed and the supported Pi upgrade and
recovery paths pass. Main currently has no protection. The repository rules
API reports a paid-plan requirement so the initial design uses an owner-run
publication gate without assuming paid GitHub features.

Risk is high because release mistakes can install privileged code or cut off
network access. The important open decisions are the first supported Pi/OS
matrix, the proven first-upgrade path and whether native repository protection
will be purchased later. Proposed first version: `v0.2.0` if it remains unused
and matches the final integrated package. The number alone proves nothing.

Status: design approved by Samantha on 2026-10-03. Feedback: agreement with
the proposed plan including automatic candidates and manual stable approval.
The design approval is recorded. Samantha authorized implementation takeover
on 2026-10-03 because there is no time for the coaching workflow today. Local
implementation is in the isolated stable-release worktree. The support matrix
and first-upgrade route still need concrete evidence. No GitHub release, remote
tag, repository setting or Pi installation has been changed by this work.

## Current evidence and existing work

Read-only inspection on 2026-10-03 found:

| Area | Observed state | Consequence |
| --- | --- | --- |
| GitHub repository | Private personal repository with `main` as default | The Pi needs repository-scoped read access for online updates |
| Releases and tags | Both API lists were empty | There is no official stable release baseline yet |
| Main | `f373d82d23ec75eaa9c401af32002af1212879e1`, package version `0.1.0` | The integration candidates are ahead of the current product source |
| Main checks | The Tests run for that exact commit succeeded | This proves that workflow passed for that source, not Pi release readiness |
| Protection | Main reports `protected: false`. Rules API returns a plan-upgrade requirement | Native enforcement cannot be assumed |
| PR #72 | Integrates the Pi line into main | Stable publication must wait for the accepted integration |
| PRs #52, #60, #66 and #68 | Signing, version numbering, publishing and online checking | Reuse their accepted combined implementation instead of duplicating it |
| Candidate publisher | Builds a requested commit and creates a release with two assets | It lacks stable-release evidence and approval checks |
| Candidate builder | An existing matching `vX.Y.Z` tag produces `X.Y.Z`. Untagged commits produce a development label | Creating a version tag after building cannot change the archived label |
| Candidate online reader | Requires exactly an archive and its signature | Adding a third release asset currently makes the release unreadable |
| Dependency preparation | Downloads packages before destructive work then installs the prepared wheels offline | Prepared wheel hashes protect that attempt but broad dependency ranges do not fix future resolutions |

Relevant source inspected: `build-release.sh`, `publish-release.sh`,
`sign-release.sh`, `check-release-manifest.sh`, `updates.py`, `install.sh`,
the update preparation plan and both test workflows in the active release
checkouts. The publisher checkout was at `2a2495d51e9c8e997acebf788858241b43d371f2`
when inspected. These candidates are moving and are not evidence of delivery
to main or the Pi. Recheck their accepted source before implementation.

No pull request risk template exists in the inspected main or combined checkout.
Use the high-risk classification above until an adopted template replaces it.

## What stable means

A stable release has all of the following:

- A unique `vMAJOR.MINOR.PATCH` tag resolving to an approved full commit on main.
- Matching package versions and a matching signed archive manifest.
- Fixed runtime and build dependency inputs for each declared supported target.
- Passing automated checks for that exact source and final package.
- Physical acceptance on the declared Pi hardware and OS with management access.
- A tested supported upgrade and recovery path with saved settings preserved.
- Plain release notes stating supported targets, upgrade limits and known issues.
- Recorded approval naming the commit, version, release order and package hashes.
- A published GitHub full release with exactly the two expected update assets.

Stable means tested for the declared use and supported targets. Keeping a
`0.x` version does not promise the long-term interface compatibility of `1.0`.
Move to `1.0.0` only after the CLI, saved-data formats and supported installation
contract are deliberately established. Do not invent physical acceptance
from browser fixtures or an emulated ARM build.

## The release path

| Stage | Trigger and owner | Output | Allowed on normal Pi updates? |
| --- | --- | --- | --- |
| Main candidate | Automatically after a main commit. CI owns checks | Unsigned development bundle and evidence tied to the full commit | No |
| Numbered candidate | Samantha chooses a version and commit after review | Final archive with reserved version and release order, local signature and acceptance record | Only by explicit controlled staging for testing |
| Approved draft | Samantha approves the final tested package | Exact tag and complete GitHub draft with both assets | No |
| Stable | Owner's publishing command verifies the uploaded draft then publishes it | Full release explicitly marked latest and verified through the update API | Yes with the existing installation approval |
| Superseded | A newer approved stable release is published | Previous release retained for history and supported recovery | Only through an explicit supported recovery path |

Normal main commits never create a published GitHub full release. Development
candidates live in Actions artifacts with limited retention. There is no new
release branch per feature and no fleet management service.

### 1. Automatic main candidates

Run the accepted test workflow on PRs and main. Reconcile the current unittest
main workflow with the pytest Pi workflow so no pytest tests are omitted by a
test runner change. Use a single explicit inventory of required jobs and
browser/Linux commands. Update that inventory when a feature adds a journey.

Build a development bundle from the exact committed source in a clean Linux
environment. Store its source commit, tool versions, resolved dependencies,
archive hash and check results. Mark it as development and unsigned. Candidate
creation uses read-only repository permissions and has no signing key.

Cancel superseded main candidate checks to bound work. Keep one current run
per main commit and retain routine artifacts for 14 days. A cancelled or
skipped required check cannot qualify a release. The final numbered package
receives its own acceptance record even when source checks can be reused.

### 2. Prepare the numbered package

First merge a focused release preparation PR containing matching package
versions, reviewed dependency locks and proposed release notes. Then choose
the full main commit. Main may advance afterwards but the selected commit
must remain in main history and retain its exact required results.

Reserve a unique version and increasing release order in a small reviewed
release registry at `docs/releases/registry.json` before building. Store each
reservation's version, order and status. Evidence and final source identities
live in the separate release record so adding them does not change the commit
under acceptance. One owner serializes reservations and
publication. Bootstrap the registry above all known signed release orders
on the supported installations. Do not use the wall clock as the authority
for release order. Reject missing or inconsistent registry state.

Create a local annotated final version tag at the chosen commit in a clean
release checkout before building. This supplies the label the current builder
expects. Retain the resulting archive and signature in a private staging
folder. Record their SHA-256 hashes. Do not rebuild during publication.

A rejected numbered candidate retires its reserved version and release order.
Do not move its tag or quietly replace its bytes. Prepare a new version when
source, dependencies or the package must change. This avoids ambiguity about
which candidate was approved. Development previews do not consume official
release orders and should be tested on disposable targets.

Store the candidate signature locally until stable approval rather than in
routine CI artifacts. Use the reviewed signing tool with no publishing token
in its environment. The signature authorizes the package's integrity. It does
not itself certify test quality or prevent a repository writer from misusing
an already signed package. Restrict signed-candidate access accordingly.

### 3. Test the final package

Run clean package checks against the final archive with its final label and
release order. Tests may come from the same full commit in a separate harness
but the installed application comes from the archive. Do not import a warmed
source checkout by accident. Confirm installed module locations and hashes.

| Gate | Required evidence |
| --- | --- |
| Code and review | Whole-change review of reliability, security, data safety, limits and compatibility |
| Python | Complete suite on declared Python versions. Current intended matrix is 3.9, 3.11 and 3.13 |
| Packaging | Clean wheel build, installed dashboard assets, package import checks and dependency consistency |
| Linux | Every accepted kernel, release permission, installer and network command |
| Browser | Every configured journey including sign-in, update state, failed-update recovery, Wi-Fi restart and phone layout |
| Signature and source | Archive tampering refused, expected signer, manifest/tag/source agreement and correct release order |
| Physical Pi | Actual supported OS/architecture, dashboard access, shaping, protected devices, reboot and rejoining clients |
| Upgrade | Previous stable to candidate with settings, access state and current trust preserved |
| Recovery | Failed installation and explicit recovery to the supported previous package with current safety state preserved |

Initially qualify one concrete Pi/OS combination instead of claiming every
Raspberry Pi OS release. Confirm the current Pi's actual architecture and OS
under a separately authorized read-only session before setting that matrix.
WPA3 support and physical multi-client claims require their own real tests.
Expand the matrix deliberately when capacity and evidence allow.

Keep the management connection independent of the Pi test network. Do not
claim that a candidate is stable while its supported update route can lose
the only way to manage or recover the device.

### 4. Approve and publish

Approval names the final identity and the applicable evidence. Approval for
a branch name or an earlier package cannot approve a later package. At the
approval checkpoint recheck tag absence/conflicts, main membership, existing
latest release and the reserved version and order. Compare versions numerically
rather than as strings. A maintenance release for an older line must never
replace the newer stable line as latest.

After approval push only the exact reserved tag. Verify its remote target.
Create a draft with tag verification enabled. Upload the retained archive
and signature using their existing names:

- `jittersim-release.tar.gz`
- `jittersim-release.tar.gz.sig`

Use release notes for hashes, approval identity, supported targets and the
compact test summary. Preserve fuller evidence as one private Actions evidence
bundle and an owner copy. Do not attach a checksum file or a wheel as a third
asset while the Pi requires exactly two assets.

Download the draft assets through GitHub again. Verify both hashes, the
signature, archive manifest and tag target. Publish as a full release with
an explicit latest setting only when the whole draft is correct. Then read
the latest-release endpoint using the same scoped access model as the Pi
and confirm the intended tag and assets are returned. Treat publication and
latest selection as separate observable facts. Do not infer success from a
lost reply. Read status before a bounded retry.

Enable release immutability if the repository supports it. Draft-first
publication lets all files be verified before locking the assets and tag.
If native locking is unavailable the owner command must refuse replacement
of published versions and the policy must clearly state that this does not
prevent an administrator from bypassing it.

Publishing stable makes the version discoverable. Installation stays under
the existing explicit Pi update approval. This plan does not enable automatic
installation or redesign the current check/download interface.

Coordinate a narrow contract check with the online-reader owner: an official
GitHub release tag must be `vX.Y.Z` and its number must agree with the signed
archive version. Development labels remain invalid for this stable source.
Keep existing manual staging for explicitly selected test packages. Refuse
an inconsistent GitHub release rather than silently treating it as stable.

## Dependencies and deployment compatibility

The current package permits broad dependency versions and an open-ended
setuptools build requirement. The candidate Pi manager uses `pip wheel` before
stopping services then hashes what it obtained. A later attempt can obtain a
different dependency set even from the same signed source archive.

Add reviewed exact-version and download-hash locks for runtime and build
dependencies under `deploy/raspberry-pi/release-locks/` so the current archive
layout includes them. Separate supported Python/architecture variants only
where required. Lock creation may resolve packages. Release installation may
only fetch already approved inputs and must verify their hashes.

Apply these inputs to the actual direct installer, remote deployment and root
update preparation. Include isolated build requirements rather than fixing
only application dependencies. Prepare the complete verified offline set
before stopping or replacing services. Reject unsupported variants, missing
wheels or altered hashes before destructive work. A dependency update requires
a new reviewed package version. Do not add an undocumented fallback that
resolves the newest dependencies from the internet.

Record the base image digest and build tool versions for acceptance. Shared
build changes require clean builds without a reused dependency cache. The
package size must remain within the existing online archive limit. If bundled
dependencies would exceed it keep verified preparation before installation
or deliberately review a format change with all readers together.

## First release and existing installations

The first stable release has no previous official stable package. Inventory
the currently installed version and its state formats privately. Qualify the
exact legacy-to-first-stable path rather than assuming the future normal
stable-to-stable path works there.

The active update work changes recovery records and preserves a standalone
manager. Its safety-state work also affects whether older code can start after
recovery. Require first-upgrade eligibility checks before stopping services.
Missing safety fields cannot be interpreted as permission to restart shaping
or household restrictions. Unsupported legacy state must leave the existing
working installation intact with a clear refusal.

Do not publish the first version as an update-capable stable release until a
reviewed route from the actual installed Pi and independent recovery have
been demonstrated. If that needs a rescue package or a manual migration,
write and review its own bounded plan. Do not slip that infrastructure work
into the publisher. Fresh installation is a separate acceptance case.

Release notes must identify supported starting versions and the limits of
recovery. Do not imply every earlier branch build can upgrade or recover.
The declared supported upgrade set is part of the stable release contract.

## State, access and recovery rules

| State or data | Release-system treatment |
| --- | --- |
| Committed source | Built from the pinned commit. Untracked local files and credentials excluded |
| Release manifest | Existing signed format binds source, version and release order. Changes need reader compatibility review |
| Dependency locks | Included in the signed archive and checked before preparation |
| Signing key | Stays on the owner's Mac. Never in repository files, CI secrets or published artifacts |
| GitHub access | CI candidate reads only. Publisher writes releases. Pi reads only this repository |
| Pi configuration and saved data | Never uploaded as release evidence. Upgrade/migration/recovery owned by the accepted update implementation |
| Release evidence | Sanitized results with hashes and target details. No tokens, passwords, device identifiers or recovery paths |
| Current signing trust and access state | Preserve through recovery. No old backup may restore removed trust or clear a newer safety stop |

No new product endpoint or database schema is required just to publish
releases. Existing migrations must run through the actual accepted installer
and update route. Release-specific migrations must be bounded and safe to
retry. Destructive changes require explicit recovery compatibility evidence.
Never replace an already applied migration just to make a release look tidy.

For a bad published release stop promoting it and move latest to a known safe
release only after verifying what the Pi endpoint actually returns. Add a
warning to the release notes and prepare a corrected release with a higher
version and order. Changing latest does not uninstall the bad version or
invalidate a package already staged. Use the updater's explicit supported
recovery path. If revocation of staged packages is needed design a signed
revocation mechanism separately. Do not promise that editing GitHub metadata
revokes a cryptographic signature.

## Limits, retention and operating cost

Use one serialized numbered release operation for the repository. Never cancel
an approved publication because main advanced. The owner command checkpoints
the selected identity and draft ID so retries inspect the same draft instead
of creating another release. Bounded network calls and at most two retries
after read-only reconciliation leave a resumable draft on persistent failure.
Proposed job limits are 30 minutes for each Python/browser job, 20 minutes
for each Linux job and 10 minutes for a candidate build or publication attempt.
Limit the Python matrix to three concurrent jobs. A deadline leaves the
release blocked with retained evidence rather than skipping remaining checks.

Keep stable assets for the supported lifetime. Keep release evidence and the
owner copy while a version is supported plus one year. Routine candidate
artifacts expire after 14 days. Use one evidence bundle per numbered attempt
with a proposed 250 MiB cap. Retain compact summaries permanently in release
notes. Record deliberately omitted screenshots instead of silently truncating
a required test result. Review storage use every ten stable releases.

Reuse source checks only when the exact source and check configuration match.
Always test final packaging and supported physical update/recovery. GitHub
billing or unavailable runners leave the gate blocked. A documented local
equivalent may be considered with explicit owner approval and retained exact
commands. An unavailable hosted job must never be described as green.

Start with release-on-readiness. Review whether accumulated changes merit a
release about once a week and publish urgent fixes after the same relevant
gates. This is a working habit proposal, not a scheduled automation.

## Implementation work in reviewable steps

1. Design approval is complete as of 2026-10-03. Confirm the
   support matrix and first-upgrade responsibility. Recheck the moving update
   branches with their owners before editing shared files.
2. Establish the policy and release registry through a documentation PR.
   Add reusable release notes and approval records. Confirm the registry seed
   against known installations without printing private device state.
3. Reconcile CI into a complete required inventory and add read-only automatic
   main candidates. Test a clean installed package. Pin third-party workflow
   actions to reviewed full commits and document update maintenance.
4. Add hashed dependency locks and enforce them through every supported install
   path. Coordinate this with the current update owner. Prove mismatch refusal
   before stopping services and successful offline installation afterwards.
5. Extend the existing builder/publisher with separate preparation and
   approval-bound publication. Keep dry-run useful. A prepare or dry-run must
   never silently publish stable. Use fake GitHub responses and an isolated
   disposable repository for any explicitly authorized API rehearsal.
   Coordinate stable tag/manifest agreement with the online-reader owner.
6. Verify the complete change including race, interrupted-upload, tag-conflict,
   missing-evidence, wrong-commit and asset-tampering cases. Rehearse first
   upgrade and recovery on supported hardware with independent access.
7. Review the first concrete release record. Obtain Samantha's stable approval
   then publish only that package. Verify GitHub latest and the real Pi online
   reader before calling the release system operational.

Implementation takeover is authorized for this release-system task. The
selected comprehension check is deferred until the next natural readiness
checkpoint: why stable approval must identify the final package and dependency
inputs rather than only a passing source commit.
Remote pushes, settings changes and live device tests require their existing
scope of authorization. This design request does not authorize them.

## Definition of done and review questions

Done means a main commit automatically produces a clearly marked candidate,
missing or mismatched evidence blocks publication and an approved stable
release reaches the Pi through its real update reader. The package identity
stays unchanged from acceptance to upload. Upgrade and recovery are proven
for every advertised target and starting version.

Review the proposal for two distinct checks: correctness of the gates and
understanding of the central decision. Be able to explain why a green main
commit cannot approve a rebuilt archive with newly resolved dependencies.

Internal design review addressed these specific risks:

- Matching the visible release tag to an archive built before that tag existed.
- Accidentally publishing a development label as a stable numbered release.
- Publishing an extra asset the current online reader refuses.
- Depending on paid private-repository approval features that are unavailable.
- Letting test-time dependency resolution differ from installation-time inputs.
- Treating GitHub latest changes as recovery or package revocation.
- Calling a supported future upgrade path proof of the initial legacy upgrade.

This is an internal design review rather than an independent external review.
Have a reviewer assess the shared installer and publisher changes before merging
and publishing the first stable release. Remaining questions: supported physical matrix,
first-upgrade route, repository protection purchase and evidence storage cost.

## Local implementation checkpoint

The release system is implemented on `codex/stable-release-system` from the
accepted combined checkpoint `61f65d83df5af0d3fee057cdc5bf6259875e75a4`.
See the [operating guide](releases/README.md) and
[verification record](releases/verification-2026-10-03.md). Physical acceptance,
main integration and actual stable publication remain separate gates.

## Source links

- [Current main source](https://github.com/smartinezai/jittersim/tree/f373d82d23ec75eaa9c401af32002af1212879e1)
- [Current main Tests run](https://github.com/smartinezai/jittersim/actions/runs/36759715931)
- [Integration PR #72](https://github.com/smartinezai/jittersim/pull/72)
- [Signed releases PR #52](https://github.com/smartinezai/jittersim/pull/52)
- [Version numbering PR #60](https://github.com/smartinezai/jittersim/pull/60)
- [Publishing PR #66](https://github.com/smartinezai/jittersim/pull/66)
- [Online checks PR #68](https://github.com/smartinezai/jittersim/pull/68)
- [GitHub release CLI](https://cli.github.com/manual/gh_release_create): explicit draft, latest and tag verification controls
- [GitHub latest-release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release): full-release discovery and scoped read permission
- [Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases): draft-first publication and locked assets
- [GitHub approval feature availability](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments): private-repository limits for required environment reviewers
- [Version numbering](https://semver.org/): meaning of version zero and later compatibility promises
