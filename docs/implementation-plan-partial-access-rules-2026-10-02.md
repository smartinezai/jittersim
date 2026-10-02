# Preserve omitted access-rule sections

Saving website rules currently clears the device block list. Saving device
rules also clears the website rules. Preserve a section whenever the request
omits it. Keep the existing meaning of a supplied section, including an empty
section that explicitly resets that section. The main risk is accidentally
weakening a saved restriction or changing the dashboard's existing full saves.
There are no open product decisions for this bounded fix.

## Scope and review

The reproduced defect and the requested fix have been reviewed. Proceed under
the explicit request to implement, test, publish and merge this fix. Use a
separate branch from the current Pi line. Existing integration and deployment
work stays with its current owner.

## Changes

1. Add regression cases for website-only saves, device-only saves, no sections,
   explicit resets and malformed sections.
2. Parse every supplied section before any write. Write only sections named in
   the request. Do not copy old values back into omitted sections. Group
   supplied writes in the existing transaction so a failed second write
   cannot leave only the first section changed.
3. Keep full saves, response contents and validation compatible. Supplied
   sections still replace their whole section. This does not add field-level
   patching or change the storage format.
4. Review the complete diff then run focused tests, the full Python suite and
   the existing lint checks before publishing once.
5. Open a focused PR and wait for its published checks before merging.

## Data, limits and release

Risk is medium because this changes writes to network access rules. It adds no
endpoint, new stored data, credentials, log contents or background work. The
existing dashboard authorization still protects the route. Saving an omitted
section performs no write and does not bump the saved configuration version.
The work remains bounded to the existing two sections and their validations.

No database migration or build change is needed. The existing installer ships
the changed Python module. CI supplies clean installs on Python 3.9, 3.11 and
3.13 plus the existing Linux and browser checks. Rollback is a normal code
revert and does not require a data conversion. It cannot recover rules that
were already lost before this fix.

The active integration branch uses a newer combined access-rule save. Its
owner must carry the same preserve-omitted-section behavior forward while
keeping its version check. Verify the combined release before deployment.
Do not replace the integration build currently under live acceptance with
an older archive from this Pi branch.
