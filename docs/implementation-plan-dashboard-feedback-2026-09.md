# Implementation plan: clear dashboard action feedback

## Reviewer summary

Make dashboard actions visibly respond so it is clear when work starts, finishes
or fails. Add a **Check token** button beside the control-token field. It will
check the token using the existing read-only diagnostics endpoint and show the
result without saving or displaying the token. Use a small number of focused
changes and commits. No networking behavior or stored settings should change.

## Decisions and risks

- Put **Check token** beside the token field. While checking, disable the button
  and show a checking label. On success, show that the token is accepted and
  load diagnostics. On failure, show a useful error and allow another attempt.
  Checking will happen only after this explicit click, not silently when the
  token field changes.
- Do not save the token to browser storage, include it in messages or log it.
  Keep the current in-page-only handling.
- Show one accessible toast that stays visible while the page is scrolled.
  Keep it in progress until the request finishes, briefly show successes and
  leave failures visible until dismissed or replaced. Do not steal keyboard
  focus. Disable the action button while its request is running to avoid
  duplicate submissions, then restore it on success or failure.
- For apply/reset, report success as soon as the action request succeeds.
  Refresh dashboard details afterward without making the action appear stuck
  while unrelated status panels load. Report a later refresh problem separately
  from a successful network change.
- Apply the same feedback style to network apply/reset and other dashboard
  actions that currently provide little or inconsistent feedback. Keep
  operation-specific details near their controls where that is more useful.
- This is a dashboard-only change using the existing authenticated endpoints.
  Main risks are stale or misleading status and exposing sensitive values in a
  notice. Verify each result comes from the completed request and never show
  the submitted token.
- Deployment has no data migration. The updated dashboard must be installed on
  the Pi before the change appears there. Rollback is a code revert and
  redeployment.

## Work slices

1. Add the adjacent **Check token** control and explicit checking/success/error
   states. Remove the silent token-change request so one click owns each check.
2. Add the shared toast and consistent pending, success and failure feedback
   for network apply/reset. Do not wait for the full dashboard refresh before
   confirming a successful action.
3. Extend the same behavior to other actions across the dashboard, keeping each
   change small and restoring buttons after errors.
4. Update the concise README dashboard description if needed. Add focused
   checks for the new control and behavior, run the full Python suite, check
   embedded JavaScript syntax and review the diff. Commit in small slices.

## Verification and recovery

- Check a valid token, an invalid token and a failed/unreachable request.
- Check apply/reset and representative save, scan and router actions for
  immediate pending feedback, one final result and protection from repeated
  clicks. Confirm errors remain understandable and retries work.
- Verify the messages work without scrolling, at narrow screen sizes and with
  keyboard navigation. Confirm tokens are not persisted or shown in messages.
- There are no database, service or network-rule changes. If feedback causes
  confusion or an action can no longer be retried, revert that focused commit
  and reinstall the prior dashboard.
