# Dashboard GUI baseline verification

## Summary

The Phase 0 test baseline captures all 40 dashboard states with screenshots,
source statistics and local accessibility reports. Production files are
unchanged. The first release gate is real green CI on the final combined
source. GitHub currently refuses to start jobs because of account billing or
spending limits. The shared Docker slot is available for the cached Linux
browser check. A fresh image build remains pending registry availability.

## Local evidence

The installed dashboard wheel uses integration base
`cf2a6fe44ec245061839a44b09a57e0b28782a6a`. Python 3.12.13 and Playwright 1.63.0
with Chromium 153.0.8010.12 were used on macOS. The manifests distinguish the
installed package from checkout source and record the actual served HTML hash.

- Existing unit suite: 2,833 passed.
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
The container handoff then identified that the browser image has no Git
checkout. Explicit supplied source metadata and a bounded container command
were added to support that path. The command uses only tracked files from
the recorded commit with a base image fixed by its digest and owns its temporary
context, image tag and container. A second path supports an inspected cached
browser image when the registry is unavailable. It installs a fresh portable
wheel offline in an owned container and checks every app file hash plus the
import locations of the app and current tests. The report distinguishes this
cached runtime check from a fresh image build. Actual cached Linux execution
is the next verification step.
An initial cached attempt stopped before importing tests because Colima could
not see host temporary bind paths. No baseline was accepted. The owned
containers were cleaned up. The cached path now copies tracked inputs into
the container and copies reports back after execution.
The final combined release needs another baseline after chart, router-copy,
polling and sign-in work is integrated by their current owners.

The latest reviewed code has no remaining blocking review findings. One
future report improvement is to preserve selected safe computed details such
as contrast ratios. Current reports retain rule links and affected selectors
while removing text and credential attributes.

Do not merge or deploy until executed CI passes on the exact combined source
and clean Linux verification is available. Existing stopped-before-start CI
jobs do not count as test failures in the code or as passing evidence. The
release coordinator owns final integration, merge and deployment.

Reproduction commands and output definitions are in
[the GUI baseline guide](../tests/browser/GUI_BASELINE.md). Outputs stay under
`test-results/` or a selected empty directory and are not committed. CI keeps
available artifacts for seven days. Reverting this scoped test change removes
the baseline tooling without a data migration or Pi rollback.
