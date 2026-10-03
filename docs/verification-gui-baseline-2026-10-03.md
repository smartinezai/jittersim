# Dashboard GUI baseline verification

## Summary

The Phase 0 test baseline captures all 40 dashboard states with screenshots,
source statistics and local accessibility reports. Production files are
unchanged. The first release gate is real green CI on the final combined
source. GitHub currently refuses to start jobs because of account billing or
spending limits. Both 40-state matrices passed in the inspected cached Linux
browser environment. A fresh image build remains pending registry availability.

## Local evidence

The installed dashboard wheel uses integration base
`cf2a6fe44ec245061839a44b09a57e0b28782a6a`. Python 3.12.13 and Playwright 1.63.0
with Chromium 153.0.8010.12 were used on macOS. The manifests distinguish the
installed package from checkout source and record the actual served HTML hash.

- Complete unit suite on checkpoint `91dc652`: 2,872 passed in 85.2 seconds.
- Final focused report, failure and container checks: 61 passed with warnings
  treated as errors.
- Existing browser journeys: passed against the installed wheel.
- Screenshot matrix: all 40 states completed with 40 PNGs and 40 statistics files.
- Accessibility matrix: all 40 scans completed with findings preserved.
- Both matrices recorded zero external runtime requests, console errors,
  script errors or security-policy errors.
- Pinned axe-core hash, version and both upstream license notices checked.
  The installed dashboard wheel contains neither axe-core nor its test files.
- Required Ruff checks, Python 3.9 syntax checks, workflow YAML parsing and
  whitespace checks passed.
  The unmodified upstream third-party license notice has a narrow exception
  for its two existing trailing spaces.
- Internal review found a partial sign-in failure could be dismissed by a
  broad success-text match. The capture now requires the exact successful
  token-check result even if its toast has already disappeared. A visible
  toast also requires the success class and exact message before dismissal.
- A real synthetic 503 response from `/api/action-history` caused the second
  state to fail with an incomplete manifest. It did not produce accepted
  results. This checked the corrected sign-in guard through the actual runner.

The initial full reports occupy about 4.5 MB for screenshots/statistics and
190 KB for accessibility. The two measured request windows are 15 seconds
each. They expose continuing background reads and provide evidence for the
separately owned polling work.

The served fixture page is 555,666 decoded bytes and 115,541 bytes when
compressed with gzip level 6. It contains one style block and two inline
script blocks. CSS text is 64,063 bytes and inline JavaScript text is 406,736
bytes. These figures describe this integration base rather than the final
combined release or actual transferred response bytes.

## Existing accessibility observations

The local baseline preserved 50 violation nodes across three rules:
`skip-link`, `landmark-unique` and `empty-table-header`. Occurrences across
pages and preferences repeat the same underlying elements. They are not 50
separate defects. Incomplete checks include `aria-allowed-role`,
`aria-prohibited-attr` and `color-contrast`. Those require manual inspection.
Neither the observations nor a complete scan constitute WCAG certification.

Screenshots were inspected for phone safety controls, desktop content and the
signed-out state. The known sign-in success toast is dismissed through its
button for stable page captures. Password fields are masked. No fixture
credential token was present in saved JSON.

## Release gates and next work

The full matrices were repeated on clean local checkpoint
`44ff8650c41ed992d337461fc6e56d65902c8415`. Both manifests recorded a clean
checkout and all 40 completed states. The screenshot run took 94.2 seconds
and the accessibility run took 74.0 seconds. Both matrices also passed on
clean checkpoint `76e6366ad073b8f9a075a541277c21a8972677ae`. Its screenshot
run took 94.6 seconds and its accessibility run took 65.7 seconds.
The final container portability checkpoint
`91dc652079526bc116aaa4d62fc6c84f6b988d94` also passed both complete macOS
matrices with clean Git identity. Screenshots took 94.2 seconds and
accessibility took 67.2 seconds. All 40 PNG dimensions matched their requested
viewport. Every statistics and accessibility file was present. Both matrices
again recorded zero browser errors, policy errors or external requests.
The container handoff then identified that the browser image has no Git
checkout. Explicit supplied source metadata and a bounded container command
were added to support that path. The command uses only tracked files from
the recorded commit with a base image fixed by its digest and owns its temporary
context, image tag and container. A second path supports an inspected cached
browser image when the registry is unavailable. It installs a fresh portable
wheel offline in an owned container and checks every app file hash plus the
import locations of the app and current tests. The report distinguishes this
cached runtime check from a fresh image build.
An initial cached attempt stopped before importing tests because Colima could
not see host temporary bind paths. No baseline was accepted. The owned
containers were cleaned up. The cached path now copies tracked inputs into
the container and copies reports back after execution.
The corrected path reached Linux runtime verification on `91dc652` before
the release coordinator reassigned Docker to the combined release. The
owned container was deliberately stopped. Partial artifacts were copied
back with an incomplete report and container absence was checked afterward.
This interruption is not an accepted Linux baseline.
That partial run verified 63 installed app files against the new wheel on
Linux aarch64 with Python 3.13.15, Playwright 1.63.0, Chromium 153.0.8010.12,
requests 2.34.2 and pycryptodome 3.23.0. The Docker host is Colima on Ubuntu
24.04.4 LTS. The cached image ID is
`sha256:c84c35705a9b65f48fcbc676f6653514bab9086e746c09035c8dbc695a60ab16`.
The installed dashboard source hash is
`40b6ea966445f898606d35fe5c48585ee5d88550e3ea5ae431d973fca2ecbbf3`.
The complete Linux matrices then passed on clean checkpoint
`f696f9f63043cbbb509e05a9c0de7e7ccc81a853` after the Docker slot returned.
Code, tests and workflow files at that checkpoint match the passed macOS
checkpoint exactly. The screenshot matrix completed all 40 states in 66.0
seconds and accessibility completed all 40 scans in 47.0 seconds. All PNG
dimensions and output files were checked. Both matrices recorded zero browser
errors, policy errors or external requests. The same 50 existing violation
nodes were preserved as observations.
The 380,221-byte wheel hash for this complete Linux run is
`80a49d925fcab521ac2b758aa628e905c95eb4c2ea50024c03ae7274003169ba`.
The runtime again verified all 63 installed app files. Those file hashes also
matched the clean host source. Both manifests identify supplied source
metadata and the installed wheel. Cleanup removed only the owned container.
A subsequent inspect confirmed its absence and the cached image's unchanged
immutable ID. The reports remain explicitly labeled `cached-runtime`.
The final combined release needs another baseline after chart, router-copy,
polling and sign-in work is integrated by their current owners.

The latest reviewed code has no remaining blocking review findings. One
future report improvement is to preserve selected safe computed details such
as contrast ratios. Current reports retain rule links and affected selectors
while removing text and credential attributes.

Do not merge or deploy until executed CI passes on the exact combined source
and its Linux verification is available. The cached result does not prove
fresh dependency resolution or replace the remaining fresh image build.
Existing stopped-before-start CI
jobs do not count as test failures in the code or as passing evidence. The
release coordinator owns final integration, merge and deployment.

Reproduction commands and output definitions are in
[the GUI baseline guide](../tests/browser/GUI_BASELINE.md). Outputs stay under
`test-results/` or a selected empty directory and are not committed. CI keeps
available artifacts for seven days. Reverting this scoped test change removes
the baseline tooling without a data migration or Pi rollback.

## Standard unit environment correction

Final combined verification of
`dce11298928d160bce0d7b7a7a8a9c518416010a` exposed two test environment
assumptions. The malformed fixture port test failed without Playwright before
it could check startup cleanup. The callback failure test still ran
`git status` after mocking the commit lookup. It failed in the clean Linux
archive environment where Git was absent.

Both failures were reproduced locally before their respective repairs.
The first now supplies temporary fake Playwright modules within the single
unit test. Its browser startup entry point immediately fails if reached.
The second mocks the complete source identity for its callback failure check.
The existing rejection, cleanup, failed report and secret omission assertions
remain unchanged. Dedicated source identity tests also remain unchanged.

All 61 focused tests in `test_gui_capture.py`, `test_gui_axe_report.py` and
`test_gui_container.py` passed in 0.19 seconds on Python 3.12.13 using the
standard development environment without Playwright. The process could not
find a Git executable. After the tests, both temporary Playwright modules were
absent from the same process and the optional package remained unavailable.
Required Ruff checks plus import ordering, Python 3.9 syntax and
`git diff --check` passed.

This isolated repair changes no production source, dependencies or CI jobs.
The release coordinator must repeat the final combined checks after carrying
the repair. These focused results do not replace the full Mac, Linux, browser
or executed CI release gates.
