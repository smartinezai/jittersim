# Reuse unchanged saved access plans

Repeated engine builds validate the same saved access rules every second. With
2000 blocked names this costs about 4 ms beyond reading the settings on this Mac.
Keep one successful settings value and validated access plan per repository.
Continue reading the settings on every lookup and reuse the plan only when their
values match. The main risks are returning stale staged changes or sharing mutable
data. Focused checks cover both. There are no open design questions.

## Scope and bounds

- Add one repository accessor under the existing repository lock. Keep only its
  last successful settings value and plan. Clear them when the connection closes.
- Use it from the scheduler, general plan builder and stored plan builder.
- Keep the pure builder and every untrusted payload check unchanged. Other API,
  CLI and settings-import entry points keep their current behavior.
- Do not cache payload dictionaries or full network plans. DNS lookups, live
  device checks, random choices and quota work still run on every engine build.
- Read settings before comparing on every call. A changed value replaces the
  single entry only after validation succeeds. Failed validation keeps the prior
  entry. Rollback and nested staged reads need no version checks or extra state.

## Compatibility and limits

The repository produces normalized value objects with tuple collections. The
validated plan has frozen fields and returns fresh payload dictionaries. Each
repository owns its entry so equal versions in different databases cannot mix.
The current repository lock covers only one connection. Another connection can
still commit between the existing settings queries. This change preserves that
limitation and does not claim a consistent database snapshot.

Risk class C because the engine and scheduler change. Household risk is low:
policy checks and wire formats stay the same and no hash-pinned file changes.
There is no schema change, migration, new endpoint or deployment step. A normal
package build includes the existing changed Python files. Reverting the change
restores uncached builds without changing saved settings. Existing repository
subclasses inherit the accessor. Check test doubles before replacing callers.

## Verification

- Repeated large-list builds reuse the same validated plan. Relevant policy,
  device, group and automatic address changes return the newly compiled value.
- Settings replacement, failed saves and nested transaction rollbacks cannot
  retain a stale entry. Invalid staged policies still fail validation.
- Independent databases and two connections to one database remain separate and
  see changed settings. A same-repository read/save check verifies lock coverage.
- Payload edits cannot change the retained plan. Closing releases the entry.
- Engine builds reuse access rules while DNS and other live inputs still change.
- Run focused storage, access and engine tests plus the narrow lint check. Repeat
  the offline measurement against an uncached read and build with the exact
  source change recorded. Full local gates wait for review and a reserved slot.
