# Restore deliberate general-profile starts after Panic

## Review summary

The complete browser check finds that Apply to all remains refused after Panic.
That button is a deliberate start action. Mark that request as explicit while
retaining its original control identity from before any scheduler wait. An
older queued request must remain refused if Panic overtakes it. Stop must never
become an explicit start.

Risk is high because a careless flag change could defeat emergency recovery.
Use the existing request-entry capture and helper revision checks. Change no
Panic producer, helper implementation, signing rule or deployment policy.

## Work and checks

1. Capture general-profile actions before the shared scheduler lock and carry
   the captured identity through the route into the existing apply operation.
2. Refuse changed intent before saving or applying the selection. Mark only
   Apply to all as an explicit start. Keep Stop non-explicit.
3. Add fail-first checks for a deliberate post-Panic start and a queued
   general-profile start overtaken by Panic. Verify Stop does not restart it.
4. Run related general-profile, control-ordering and helper recovery checks.
   Run the complete general-profile browser module at desktop and phone widths.
5. Obtain independent source review. Rebuild the package and execute the full
   final workflow matrix after all browser-discovered repairs are frozen.

Rollback removes this focused repair. No Pi mutation is part of this task.

## Independent plan review

Capture both general-profile routes before the outer scheduler wait. After
capability and usage work, compare the helper revision again and refuse a
changed generation before saving. Commit the short structural save through
the existing current-generation publication guard. Keep all helper calls and
usage sampling outside that guard.

A separate helper Panic can still land between its last revision comparison
and the database commit. Without a new cross-process transaction protocol,
that narrow window can save a selection but cannot apply it. Final apply must
retain the original revision and refuse changed control without success.
Tests cover queued actions, capability/usage preparation, external helper
Panic, older helper refusal and Stop with saved device profiles.
