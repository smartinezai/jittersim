# Carry saved protection to the resolver

Add the two backend hooks requested with the separately owned protected resolver
change at 82cf49e5. Saved protection must reach the access plan and address moves
must refresh the active resolver before claiming their network update succeeded.
The current source differs from the proposed patch because it already has the
access-plan cache, so apply the same small changes to the current methods.

Include unaffected_devices in access_settings. This also makes protection edits
invalidate cached plans by content. In _address_saved_response first refresh the
household resolver. If that fails preserve saved response fields and report
live_applied false, using the existing error. On success retain the existing
ad-blocking sync and network response sequence. Test both result paths and
protection cache changes. No network, pinned source or helper change belongs to
this commit. Test the composition with the separately reviewed resolver change
in another isolated worktree after the backend commit is frozen.
