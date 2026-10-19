# Release tooling safety plan

The release command must preserve remote branch history and retain all upgrade and safety notes. Check the full release section before committing it, use the signed archive section for acceptance and refuse oversized stable archive notes. Keep the existing changelog content unchanged in this task.

## Changes

- Replace the release record force push with a normal push. Save the record commit before pushing so a repeat reuses the same commit. Refuse unexpected branch changes.
- Apply the 15000 byte early limit to the combined Unreleased entries, supplied or generated notes and release heading.
- Read acceptance notes from the retained archive so publication uses the same complete section the Pi sees.
- Make direct stable builds fail above the Pi 16384 byte allowance instead of cutting notes.
- Update the release instructions and add focused regression checks for combined limits, archive limits and repeated record branch pushes.

## Risks and verification

A remote record branch changed by someone else must cause a normal push rejection. Existing incomplete state may need inspection rather than automatic rewriting. No tags, acceptance evidence or remote state will be changed during verification. Run the focused release script tests with temporary repositories and throwaway keys. The parent task owns full local checks, final release notes and publication.
