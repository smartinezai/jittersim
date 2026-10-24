# Group selector empty-state fit

The connected device group selector places a full instruction inside a narrow native select. Keep the select concise with “No groups” and place the existing instruction in a visible wrapping note in the existing device details column above the row controls. Associate the note with the select for assistive technology. Preserve group choices, saved selections and all action rules.

## Evidence and boundaries

- Frozen base: ec403d0f8f347b430398ee5cc281add1aa309318.
- The current empty option is “Create a group on the Profiles page”. The desktop control is 12rem wide and the phone layout shares its row with an action button.
- The existing backlog records clipped text. Two static unit checks pin the long option wording. Fresh browser geometry remains to be checked when the shared browser window is available.
- Only group selector markup and related tests change. No group behavior, backend, profile editor or traffic rendering changes.

## Change

1. Preserve the baseline source evidence and reproduce its option width in the first permitted browser batch at desktop and phone widths.
2. Change only the empty option to “No groups”. When the list is empty add the full existing instruction as a wrapping form note with a stable per-device ID and aria-describedby.
3. Update the existing wording checks to require both the concise state and the full instruction.
4. Add focused browser coverage for an empty list, a populated list and a saved selection, including 320px width and a narrow zoom-equivalent viewport.

## Verification and review

Use the shared wrapper and browser locks. Start with focused unit checks. Perform at most two visual batches. Confirm that the option text fits, the instruction wraps within the row and existing selected values survive refresh. Run the Impeccable detector once against the final changed UI source. Record evidence and limitations in ignored test-results/group-select-fit/report.md. Review the final diff before committing. No push or pull request is included.

This is a presentation-only change with no data migration, new storage, request, authentication or deployment behavior. Rollback is a source revert. Group and device counts retain the current behavior and no new per-item work beyond one small note is added.

## Review state

Plan reviewed and approved on 24 October 2026 within the existing explicit authorization for ongoing implementation. The approved scope is concise empty copy with the full visible helper in the existing details column and aria-describedby. Fresh geometry checks will wait for the reserved hardware trial to finish.


## Completion

Implemented the approved details-column helper with “No groups” in the native select. Focused browser checks confirmed the original clipping and final fit at 1440px, 390px and 320px plus 200% CSS zoom. Saved group labels and values survive re-renders. All 28 focused unit checks pass. JavaScript syntax and diff checks pass. The final design detector has no findings. Scoped review found no consequential issue. Evidence and limits are in ignored test-results/group-select-fit/report.md. Full GUI gates and hosted CI remain outside this local check.
