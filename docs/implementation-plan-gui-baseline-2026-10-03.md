# Dashboard GUI baseline implementation plan

## Reviewer summary

Add a test-only baseline for the existing dark dashboard. Reuse the Python
Playwright fixture and add a pinned local axe-core file. Capture nine signed-in
pages plus the sign-in state at 375×812 and 1440×900 under both colour
preferences. Record screenshots, accessibility findings, CSS/page statistics
and a complete manifest. No production GUI, API, network or stored data changes.

The important risks are incomplete reports that look successful, test artifacts
containing credentials, unbounded capture and fixture cleanup. Existing
accessibility findings are observations rather than failures of the runner.
Broken scans, missing states and external runtime requests fail the runner.

## Scope and approval

The owner authorized proceeding with the recommended GUI plan on 3 October
2026 at 00:55 Berlin. The 24-hour authorization ends on 4 October at 00:55.
Local verification and green CI on the exact combined source remain release
gates. The release coordinator owns final merges and deployment. The broader
GUI research plan remains uncommitted in the main checkout.

This work uses `codex/gui-baseline` in its own worktree, starting at integration
`cf2a6fe44ec245061839a44b09a57e0b28782a6a`. Record that base in the reports and
repeat on the final combined release before accepting its baseline. The active
chart, router-copy, polling and sign-in branches stay with their current owners.

## Work and limits

1. Add a small shared capture helper with an explicit 40-state matrix.
   Reuse fixture startup/sign-in/navigation. Give each state a fresh browser
   context. Wait for known content instead of network idle.
2. Add screenshot and CSS/page statistics capture. Define text-scan counts
   precisely. Record rendered/served bytes separately from package sizes.
   Measure requests in a fixed 15-second window on selected states only.
3. Vendor unmodified axe-core 4.13.0 with its license, upstream source and
   SHA-256. Inject it locally. Record violations and incomplete checks.
4. Save commit, browser version, matrix state, viewport, colour preference,
   artifact names and completion/failure information. Use only synthetic data.
   Do not export browser storage, headers, passwords or credential fields.
5. Wire a separate bounded CI job so current journeys keep their existing
   timeout. Keep uploaded artifacts for seven days. No additional Node tools.
6. Verify focused failure handling, asset provenance, lint, existing unit
   checks and the real 40-state browser run. Build/test an installed wheel and
   a clean container when the shared Docker host is free.
7. Obtain an independent complete-change review, repair findings and record
   evidence. Commit locally as a checkpoint. Publish only under the owner's
   authorization and coordinate the final CI/merge with the release owner.

Fixture startup has a 15-second deadline. A state has a 30-second deadline.
Each runner has a 12-minute overall limit. Artifacts are capped at 100 MB and
the screenshot matrix at 40. Failed runs cannot claim completeness. Shut down
fixture children and browser contexts even after exceptions or cancellation.

## Verification and recovery

- Treat the source checkout and installed wheel as separate checks.
- Validate fixture readiness, malformed startup output and process cleanup.
- Test the independent report completeness check and timeout/failure paths.
- Verify the axe version/hash/license from the vendored files.
- Review screenshots for unexpected secrets, clipping and missing content.
- Keep accessibility findings visible. The baseline is not WCAG certification.
- Test-only assets remain outside the runtime wheel. No Pi deployment or
  database migration is needed. Revert the scoped test change for rollback.
  Container checks use a resolved base-image digest, unique owned resources
  and explicit host source identity because the browser image omits Git.

## Open environmental condition

Recent GitHub jobs have no executed steps. CI must actually run and pass before
the release coordinator merges. Local checks cannot substitute for this gate.
