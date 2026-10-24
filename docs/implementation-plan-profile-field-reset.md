# Profile field reset

Add a small reset button beside each profile setting. Use the default declared in the form so zero values, 100% chances, optional blank values and select defaults remain distinct. Reset changes the draft only. The person must still save the profile. Keep names, IDs and the preset chooser outside this action because they identify or populate a profile rather than configure its network behavior.

The existing editor already saves cleared caps and seeds, zero chances and empty allow-only lists. Add regression checks through a second edit that compare the full saved profile. Existing Delete refuses profiles with references and Stop clears the general assignment. Keep these semantics and cover cancellation, refusal and unused deletion.

## Implementation

- Keep the current dark fields and add one adjacent 44-pixel reset target with an SVG icon and a field-specific accessible name. Include upload/download and feature context where labels repeat.
- Read input and textarea defaults from defaultValue. Read a select's explicitly selected default or its first option. Do not reset identity fields or the preset chooser.
- Trigger the existing input and change events so draft tracking, range validation and previews update. Preserve every other setting and hidden randomness. Match reset availability to the setting's disabled state and hide controls beside hidden labels.
- Limit production edits to the profile editor functions in dashboard.js and .profile-field-reset rules in dashboard.css. No API, network, protected-device, update or pinned-file changes. Household risk is low and risk class is B.
- Use bounded DOM initialization once per page. No added requests or polling.

## Verification and review

Compare current form defaults with model defaults using the existing full-profile round trip check. Add focused checks for numeric, optional and select defaults plus draft preservation and explicit saving. Run saved_profiles at desktop and phone widths with target geometry and screenshot checks. Run applicable unit, lint and GUI checks on the exact commit through the shared wrapper and respect the coordinator's queue. Obtain independent source review before requesting broader gates or opening a draft PR.

Rollback is a revert. Stored profiles need no migration because reset only changes an unsaved form field.
