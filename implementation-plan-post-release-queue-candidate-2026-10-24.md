# Local post-release queue candidate

Build one isolated local candidate from frozen aa561623 with only the completed queue fixes approved by the coordinator. Preserve each original commit through local merge ancestry. This candidate does not change the running release, main, reserved state or remote branches. The separate main-owned aggregate65bba3a4 contains unrelated UI work and excludes these inputs, so it is not a source for this candidate.

## Inputs and composition

Use local branch codex/post-release-queue-candidate. Add name24aeb0aa, token eaa022f3, router48708fbe, headings3ef58fe9, phone7d345450, token-browser141f5bb1 and callback44f27527, then peer63597e01. Router already includes token. Phone already includes headings. The callback already includes token-browser. Keep original commits immutable and use local merge commits. Await the separately reviewed router-browser preparation commit as a final input.

Resolve only actual shared-path conflicts. Preserve token error helper/state while introducing phone manual-intent parameters and queued intent. Keep router error lifecycle and heading lifecycle unchanged. In the combined tests move Remember reset before the accepted token check closes Credentials and relax the token unit extraction marker for the parameterised checkAdminToken signature. Inspect the supplied compatibility patch against the actual composed file before applying it. Do not import unrelated UI aggregate changes, baseline edits, release-fixture c52f194d, chart/protected/PR209 inputs or shared docs.

## Freeze and validation

After all inputs and bounded compatibility fixes are present, record exact HEAD, tree, input ancestry and changed blobs before running gates. Ask the coordinator for independent final shared-hunk, auth/security and ancestry review. Run focused shared-path regressions through the mandatory wrapper with a private existing offline runtime, no downloads or owner-runtime changes. Include names, token/router errors, phone handoff, authentication/credential refresh, brand/navigation, shared access security and fixture/static asset checks. Preserve known failed receipts and do not claim slice receipts validate the combination.

Browser DOM journeys require the coordinator to open the permitted queue. Use actual served source and synthetic local fixtures for dashboard access, token errors, router errors and phone handoff. Retain explicit registration, focus departure and privacy checks. No full suite, Docker or urgent release-fixture duplicate run unless newly assigned. Reserve broader final applicable verification for the final frozen source rather than repeating partial gates.

## Risks and rollback

The concrete risks are shared token check code and browser fixture timing. Merging ancestry and reviewing bounded conflict resolutions avoids replacing newer source with whole older files. This local branch has no deployment or data migration. Abandoning the candidate restores the prior state without touching original commits. Main coordinator remains the only main/release/GitHub writer. No push, PR, publication, signing or hardware contact is authorised here.
