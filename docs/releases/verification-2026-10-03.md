# Stable release system verification

The local release system is ready for integration review. It has not been pushed
or deployed. No remote tag or GitHub release was created. The first stable release
still needs accepted main source, green hosted checks and physical Pi acceptance.

## Source and scope

Base: `61f65d83df5af0d3fee057cdc5bf6259875e75a4`.
Local branch: `codex/stable-release-system`.

The change adds automatic unsigned candidates, a permanent version/order registry,
owner-signed approval and draft-first publication of the retained archive. It
fixes runtime and build inputs then installs prepared wheels without an index
connection during the service outage. Online tags must match the signed version.

Implementation takeover was authorized on 2026-10-03. Design approval covers the
release system. It does not approve an untested stable package for publication.

## Completed checks

| Check | Result |
| --- | --- |
| All affected online, release, installation and recovery test files | 268 passed and 1 skipped |
| Final release gates after file-input guards | 45 passed |
| Full existing test suite | 4,718 passed and 40 skipped with 4 failures on the first run |
| Two release contract failures from that full run | Corrected and verified by the affected run |
| Two untouched network failures | Reproduced with identical errors on the unchanged base and changed source |
| Clean Linux ARM64 locked package build on Python 3.9 | Passed |
| Clean Linux ARM64 locked package build on Python 3.11 | Passed |
| Clean Linux ARM64 locked package build on Python 3.13 | Passed |
| Python fatal-error lint, shell syntax and whitespace checks | Passed |
| Workflow YAML parsing | Passed |

The fresh container builds used empty temporary build and validation environments.
Docker DNS failed so dependencies were downloaded through the host against the
committed hashes then transferred into disposable containers. Preparation used
`--wheelhouse` with no index access. Each package set passed offline installation,
`pip check` and imports. No running service container was changed.

The unchanged failures are:

- `test_quota_usage_excludes_protection_and_does_not_charge_the_exempt_interval`
  returns HTTP 503 where its test expects HTTP 200.
- `test_reset_name_answers_before_the_restart_and_stores_the_outcome` reports
  failed where its test expects succeeded.

These failures must be resolved in the accepted integration before hosted release
checks can pass. Their owning network code was left unchanged.

The release tests use real Ed25519 signatures and a stateful GitHub stand-in. They
exercise exact final package construction, development labels, package changes,
missing or changed approval, incomplete CI, retired versions, wrong tags, newer
stable versions, interrupted uploads, repeat publication and changed assets.
They also check output limits, descendant cancellation and unsafe file inputs.
The stand-in proves local decisions. It does not prove a real GitHub publication.

## Required before the first stable release

Resolve the existing integration failures and rerun the required hosted workflow
inventory for the final main commit. Inspect the current Pi's release-order floor
before initializing the registry. Select the supported Pi/OS/architecture/Python
matrix and prove the first upgrade and recovery route with the exact final signed
package. Complete acceptance evidence and release notes then obtain the owner's
signed stable approval. Only an explicitly authorized publication should push the
tag or create the GitHub release.

Container checks do not prove physical Wi-Fi behavior or legacy recovery. Native
GitHub protection and immutable releases are not configured by this change.
The local publisher refuses replacement but cannot constrain direct owner edits
through GitHub. The initial history query stops at 100 releases and requires a
policy review rather than deleting old releases.
