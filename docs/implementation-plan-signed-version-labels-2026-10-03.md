# Keep signed release labels consistent

The Mac must display and verify the version in the signed release. Combine the
reviewed version-number work from PRs 60 and 74 with the current signing and
one-command installer. Keep the signature, commit and increasing release order.
Retain safe older labels for the first upgrade and recovery. New builds use
MAJOR.MINOR.PATCH for a matching tag and NUMBER.dev+COMMIT otherwise.

The main risk is combining an older unsigned build script with the current
signed installer. Do not replace the current signature checks or release-order
floor. A newer signed build must also refuse a lower version number when the
installed release has a known number. Explicit restoration remains available.
The existing implementation approval covers this work. The independent reviewer
reviews the combined changes before publication.

1. Save regressions that fail for signed development labels, mismatching tags,
   version-only output and lower-number installation despite a newer sequence.
2. Merge PR 60 deliberately. Keep legacy label validation for previous records
   and use a separate strict number parser for ordering. Bound labels to 64 bytes.
   Put the downgrade check in the shared installability check so root installation
   rechecks it as well as the dashboard and queue operation.
3. Merge PR 74's deployment intent. Preserve default installation, stage-only,
   signing, private token input and finite waiting. Read the built manifest's
   label and require agreement with the build script before contacting the Pi.
   Compare both SSH and HTTPS results with that label. Commit names remain useful
   for filenames and source identification.
4. Test both transports, already installed releases, explicit staging, fallback,
   signed manifest tampering and legacy labels. Update existing fake Pi fixtures
   to represent a real project version. Do not accept a response merely because
   its short commit matches when its signed version differs.
5. Explain the first upgrade from an older signed manager. Its label validator
   cannot read the new development format. Use the ordinary installer once for
   this transition, then the one-command signed update route.
6. Run focused signing, deployment and rollback tests plus Ruff. Submit the full
   combined diff to the independent reviewer and preserve exact source evidence.
   The coordinator runs the configured workflows on the final combined head.

No new credentials, network endpoint, dependency or saved user data is needed.
Rollback keeps the current signer list, read-only token and release floor. Both
coordination files remain untracked. The original PR 60 and PR 74 branches remain
unchanged. Never force push or push directly to main or the Pi branch.
