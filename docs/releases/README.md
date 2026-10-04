# Releasing JitterSim

Main changes produce automatic unsigned development candidates after all required
checks pass. A stable release is a retained signed package with owner approval
and evidence from the supported Pi. Publication uploads exactly that package.
Installation remains a separate choice on the Pi.

## First release prerequisites

Merge the accepted Pi integration and this release system into main. Run the
required main checks from `deploy/raspberry-pi/release-policy.json`. Select the
supported Pi model, operating system, architecture and Python version. Test the
first upgrade from the current installed Pi release and recovery to that release.
Record the supported starting version explicitly in the release notes.

Before initializing the release registry inspect the highest installed, staged
and remembered release order on every supported Pi. Use the largest value as the
floor. Existing development archives used time-based orders. Guessing zero could
make official releases impossible to install. Do not change an installed Pi's
high-water mark to make a package appear newer.

The signing key stays on the owner's Mac. Its public key must already be trusted
on every tested Pi. The key never goes into CI or the repository. The first
implementation uses the `smartinezai` GitHub account for approval and publication.

## Reserve a version

Run from the repository root. Replace the example floor with the inspected value.
Initialize once then commit the registry as part of release preparation.

```sh
python3 deploy/raspberry-pi/release-system.py initialize --floor 2000000000 --confirm-inspected
python3 deploy/raspberry-pi/release-system.py reserve 0.2.0
```

Update the package version and release notes in the same preparation change.
Commit the reservation to main through the normal reviewed change process and
wait for the required checks for that exact commit. These commands do not push.
Version comparison is numeric. Failed versions and their release orders stay used.

```sh
python3 deploy/raspberry-pi/release-system.py retire 0.2.0
```

Retirement does not remove a local tag or retained package. Reserve a higher
version for the replacement. Commit the registry change before the next build.

## Prepare the final package

Use an absolute path outside the checkout for the retained package. The output
folder must not exist. Use a full source commit accepted in main with passing
checks. The command creates a local annotated tag and builds from an isolated
committed checkout. It creates no remote tag or GitHub release.

```sh
python3 deploy/raspberry-pi/release-system.py prepare \
  --ref FULL_MAIN_COMMIT --version 0.2.0 \
  --output /absolute/private/releases/0.2.0 \
  --key "$HOME/.ssh/jittersim_release_ed25519"
```

Retain all files. Test `jittersim-release.tar.gz` with its matching signature.
Do not rebuild the package after testing. The manifest contains the final version,
source commit and reserved release order. The record also binds every dependency
lock. A changed package needs new testing and approval.

## Record acceptance and approve

Complete `acceptance.json` in the retained folder using the
[release checklist](../release-checklist-template.md). All six checks must pass:
physical Pi behavior, upgrade, recovery, saved settings, management access and
current safety state. Include concrete evidence locations and the tested
Pi/OS/architecture/Python targets. In `release_notes` record the changes, supported
starting version, any setup steps, known limits and tested recovery route.

The command below is the human stable approval step. Run it only after reviewing
the exact retained package and completed acceptance. It checks main and CI again
then signs the approval with a separate signature purpose.

```sh
python3 deploy/raspberry-pi/release-system.py approve \
  --bundle /absolute/private/releases/0.2.0 \
  --evidence /absolute/private/releases/0.2.0/acceptance.json \
  --key "$HOME/.ssh/jittersim_release_ed25519" --confirm-version 0.2.0
```

Keep the signed approval unchanged. Approval cannot be reused for a different
package. If the approval step loses its response inspect its two output files.
Do not delete and repeat a signed approval to conceal a changed package.

## Publish the approved package

Preview verification first. This checks GitHub but creates no tag or release.

```sh
deploy/raspberry-pi/publish-release.sh \
  --bundle /absolute/private/releases/0.2.0 \
  --trusted-key "$HOME/.ssh/jittersim_release_ed25519.pub" \
  --confirm-version 0.2.0 --dry-run
```

Publication requires explicit owner authorization. Run the same command without
`--dry-run` only when ready to publish. It pushes the exact local tag, creates a
draft, uploads the two expected files, downloads and checks both then publishes
as latest. It checks the published bytes again before recording completion.
Missing approval, failed CI, a retired version, a changed tag or mismatched assets
stop the operation. A newer stable release prevents promotion of an older one.

If interrupted rerun with the same retained folder. Existing files must match.
The tool never replaces uploaded assets. A mismatched draft needs investigation
rather than an overwrite. A published mismatch needs release recovery and a new
version. Review history before the 100-release limit. The tool fails safely at
that limit and does not delete history.

After success verify the real Pi's online check offers the expected version.
Install only through the tested approval path. Commit the registry's published
state and retain `publication.json` with the complete approval and test evidence.
Back up the private evidence for the supported release lifetime plus one year.
Enable native immutable GitHub releases and repository protection when available.
The local command alone cannot prevent repository owners editing GitHub directly.

## Dependency changes and offline verification

Runtime locks contain all runtime dependencies with exact versions and wheel
hashes. Python 3.9 has its own compatible requests and urllib3 pins. The build
lock fixes pip, setuptools, wheel and packaging. CI verifies these inputs on
Python 3.9, 3.11 and 3.13. Changing any lock requires fresh package acceptance.

The Pi prepares and tests the complete wheel set before stopping services. Stable
packages without the locked preparation step are refused. Installation during
service downtime uses the prepared wheels without contacting a package index.
Older development archives keep their existing preparation path.

For a clean offline build use wheels downloaded independently against the same
hashes for the target architecture and Python version:

```sh
python3 deploy/raspberry-pi/prepare-locked-wheels.py \
  --source . --output /absolute/empty/wheels --wheelhouse /absolute/downloaded/wheels
```

The wheelhouse option still enforces exact versions and hashes. It does not allow
source packages or bypass a missing dependency. Physical Pi tests remain required
because a clean Linux container does not prove Wi-Fi or legacy recovery behavior.

## Source integration when hosted checks are unavailable

This publisher does not decide whether a source change may be merged. An explicitly
approved source merge can use the configured checks run locally when GitHub limits
prevent hosted execution. Record the actual commands, source identity and results
as local evidence. Do not describe them as green hosted runs.

Automatic candidates and the stable publication gate currently require successful
hosted main workflow evidence. There is no local-evidence override in this tool.
A future manual stable route would need its own reviewed acceptance contract.
Source merge approval never supplies physical Pi acceptance or approval of an
untested final package. The inspected Pi order floor and owner-signed exact-package
acceptance remain required before stable publication.
