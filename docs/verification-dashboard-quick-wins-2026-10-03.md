# Dashboard quick wins verification

The dashboard changes complete PR 73 on integration 0c8abd4. Public sign-in
uses neutral names. Authenticated branding cannot be restored by an obsolete
response after credentials change. Notifications announce once, retain errors,
respect keyboard focus and leave phone recovery controls usable. Explicit
remembered-token choices preserve the existing 24-hour default. Displayed
profile names retain stable saved identifiers.

Risk is medium. The material change is longer optional credential storage.
Expiry forgets the stored token rather than revoking a currently entered
credential. Network operations and saved profile data remain unchanged.
Reverting the code restores the former interface without data conversion.

## Exact checked source

- Final test source: 71e437abdc8f9173f4ecceea7b711a3001221c0f.
- Application source: a64965b531b316c22a1e507d3c99409d8f72af13.
- Both contain application tree c6ffd67e834e95cee64d347e5af7e752bfed58d6.
- Wheel SHA256: 652a5566c4a801370093888179c5cb1cc817c9d9ad6b04eb95046e6aab183b47.
- All 64 packaged application files match that source and the installed files.

Only browser test expectations changed after a64965b. Fifteen browser modules
passed at a64965b and four passed at baa90bc. Their application, fixture and
command inputs are unchanged. The final capture helper correction is tested
at 71e437a with 61 capture unit checks and both visual matrices. The report
retains each actual source identity and original log rather than relabelling
an earlier run.

## Completed checks

- Linux Python 3.9, 3.11 and 3.13: 3,237 passed with 39 expected kernel skips
  in each full suite. Configured Ruff selection passed.
- All 39 actual Linux kernel checks and the real network smoke command passed.
- All 19 ordinary browser modules passed with the installed wheel.
- Homepage fixture checks and the 61 capture unit checks passed.
- Screenshot and accessibility matrices completed all 40 states each using
  Chromium 153.0.8010.12 with Playwright 1.63.0.
- All 12 Linux release commands passed with their configured permissions,
  including installer permissions, peer identity and actual packet checks.
- Both release and kernel Dockerfiles built without cache from current source.
- Independent source, packaged-file and GUI artifact reviews passed. Final
  command coverage and durable evidence review is recorded in the JSON report.

The final captures have zero recorded console, page, policy or external-request
errors. Accessibility analysis retains 50 repeated observations: 48 moderate
and two minor across skip links, landmarks and empty table headers. These are
unchanged from the reviewed foundation. Another 74 observations need manual
review. These checks do not establish accessibility conformance or actual
assistive speech.

## Build and evidence limits

Linux tests run on arm64 rather than the hosted Ubuntu x86_64 runner. The
browser image supplies a recorded immutable runtime with fresh test dependencies.
Both clean Dockerfile builds install their declared system and Python inputs.
The Docker VM could not resolve the official registry. The declared kernel
base was obtained through the working host HTTPS connection with every blob
hash checked, then its filesystem and runtime configuration were matched after
Docker translated its manifest. The fetch report records the original registry
selection chain. Original registry index and selected-manifest bytes were not
retained. The Docker import manifest is a different document. The original
kernel mode label mentions cached dependencies while its image binding and
clean build log identify the new no-cache image. Shared network settings were
unchanged.

Four stopped verification attempts are retained. Two found phone notification
regressions that were repaired before the application freeze. Two found obsolete
browser wording expectations that were corrected without weakening behavior or
error assertions. Additional phone diagnostics are retained with their failures.

Artifacts and their SHA256 inventory are retained through 10 October at
`test-results/dashboard-quick-wins-2026-10-03/` in the primary checkout.
The detailed JSON report records exact commands, durations, runtime identities,
clean builds, original checks and independent audit results.

GitHub account-limited jobs remain unavailable hosted checks. Direct human
authorization permits these actual local workflow commands as substitutes.
No successful hosted status is invented and no protection rule is changed.

The independent complete workflow and durable evidence review approved this
scope for integration. Main and Pi release
remain pending the update, reconnect and address repairs plus a verified
management recovery connection. No live Pi write or deployment occurred.
