# Device name display hygiene

The saved-name and import formats currently accept hidden characters. Changing the shared model validator would also reject existing saved rows, so this task will preserve those formats. The existing display resolver already ignores unusable device names. It misses C1 controls and the Arabic Letter Mark, allowing those characters into the displayed title and into names chosen automatically from a hostname.

## Scope and decisions

- Extend only the existing hidden-character predicate in `config/device_names.py` to cover C1 and the missing Arabic direction mark.
- Keep ordinary Unicode text, accents, emoji and emoji joiners intact.
- Keep existing stored names, API input acceptance and versions 1 to 3 of imported settings unchanged.
- Add uniquely named regression tests for all three display-name sources and the automatic name helper. Verify saved names remain readable and export/import round trips preserve their values.
- Use only bounded offline checks through the shared wrapper. Do not run browser, Docker, release or hardware checks during the owner's release work.

## Risks and verification

Risk class B, household risk low. This changes which supplied device name is selected for display or for the automatic name of a newly saved device. Existing fallback order and address-based unknown titles remain unchanged. A name containing one of these controls will fall through to the next usable source. There are no migrations, additional dependencies, network operations or changes to privileged files. The check remains a bounded linear scan of existing name strings.

Verify the defect with focused failing tests, apply the predicate correction then rerun the new tests and existing device-name and label tests. Also check current API model construction, saved-row loading and settings export/import without opening an HTTP server. Reverting the source change restores the previous display behaviour without rewriting saved data.

## Remaining policy decision

General profile, group, scenario and other label input rejection is intentionally unresolved. Options are to preserve data and sanitise presentation, reject only new or renamed labels at explicit write boundaries or version the format with an explicit migration and legacy-import policy. The present source and backlog do not authorise inventing that wider contract.
