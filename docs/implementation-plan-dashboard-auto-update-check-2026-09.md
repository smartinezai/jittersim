# Implementation plan: check staged updates on dashboard open

## Reviewer summary

Check for a verified release staged on the Pi when the dashboard opens. Add
an on/off preference that defaults to on and save it on the Pi so the choice
works from different admin browsers. The check runs once in the background
and never downloads or installs a release. If a different verified release
is staged, mark the Setup tab and show details in the existing updates card.
If checking fails, the rest of the dashboard still works.

This uses the existing staged-release check. It does not contact GitHub or
search the internet. The main risks are hiding a useful update signal and
making the preference behave differently than the owner expects. **Risk:
low.** The feature adds one saved display preference and one bounded,
read-only preference lookup and at most one automatic status check per
dashboard opening. It does not change network rules, Pi services or release
installation.

### Decisions

1. Save the preference on the Pi so it follows the owner between phone and
   computer.
2. Add a small **Release staged** badge to the Setup tab with no pop-up.
   Release labels are not ordered by the current system, so the badge must
   not claim a release is newer.
3. Keep the check limited to releases already staged on the Pi. Checking
   GitHub would need a separate decision about a trusted release source and
   safe access to the private repository.
4. After the feature works and its checks pass, use Impeccable to refine the
   dashboard control. Keep the current dark navy theme and existing visual
   system.

The human has asked for the implementation and for the visual polish pass to
come after functional checks.

## Current behavior

- The **Check for updates** button is in the Setup tab's **Updates and
  recovery** card.
- The button reads the existing `/api/updates` status route. The privileged
  helper reads the installed and staged release metadata on the Pi. This
  status read does not contact the internet.
- The status only refreshes after the owner presses the button or after an
  install or rollback action.
- The dashboard page is served with other tabs hidden. Its update controls
  are installed when the dashboard scripts load, so they can check without
  waiting for the owner to open Setup.
- Update status and installation already use the admin-protected dashboard
  route and helper. Installation requires a separate confirmation.

## Proposed behavior

1. Add a preference labeled **Check for staged updates when the dashboard
   opens** to the existing updates card. Use a clear on/off control and save
   each change immediately. Show whether the change was saved. Restore the
   previous value and show an error if saving fails.
2. Default new and existing installations to on. Save this dashboard-only
   preference in its own small SQLite setting. Do not add it to network
   settings exports or bump the network configuration version.
3. When the dashboard opens, read the preference and continue loading other
   dashboard sections without waiting. If it is on, read update status once
   for that page opening. If it is off, skip the automatic status request.
4. Keep **Check for updates** available in both modes. A manual check can
   refresh status even when automatic checking is off. Reuse an in-flight
   request rather than starting two status reads at the same time.
5. When a verified staged release has a different label from the installed
   release, show a small **Release staged** badge on the Setup tab and keep
   version details in the updates card. Remove the badge when no different
   staged release is ready. Do not try to order release labels. Do not show a
   modal, install automatically or repeat a pop-up on every dashboard opening.
6. If the automatic check fails, keep the rest of the dashboard usable. Show
   an understandable status in the updates card and leave manual checking
   available.

## Data, security and scale

- Store one boolean on the Pi. Initialize it as on when the preference table
  is first created. Make table creation safe to repeat for both new and
  existing databases.
- Keep the preference out of settings export and import because those files
  describe network profiles and rules. The normal SQLite backup will include
  it.
- Add read and write routes using the existing admin authentication. Accept
  only true or false. Do not expose the update status route to signed-out
  users.
- The only status information comes from the existing local helper. Do not
  add internet requests, credentials, update downloads or new log data.
- Limit automatic checking to one status request per dashboard opening. Do
  not add a timer or check again when the owner switches tabs. Reuse a status
  request that is already in flight. A browser refresh is a new opening and
  can check again.
- If the preference cannot be read, do not silently override a saved off
  choice. Skip the automatic check for that page opening, report that the
  setting is unavailable and leave manual checking available.

## Work sequence

1. Add the SQLite preference with an on default and focused storage coverage
   for a new database, an older database and saving either value.
2. Add the authenticated preference routes and validate their inputs and
   saved status without changing network configuration versions.
3. Add the preference control and saved/error states to the existing update
   card. Trigger one background update-status read when enabled.
4. Add the Setup tab badge when the staged release label differs from the installed one.
   Keep the existing confirmation required for installation.
5. Update the README and deployment notes only if they explain update
   discovery or dashboard preferences. No system service or installer change
   is expected.
6. Review the complete diff and run the existing Python, Ruff, Linux and
   browser checks. Keep the work on a feature branch until reviewed. Do not
   deploy to the Pi. The earlier request authorizes a push after this work is
   complete and tested.
7. After functional checks pass, run one Impeccable polish pass on the
   changed dashboard controls. Preserve the dark theme and current color
   palette. Run the Impeccable detector once on the finished UI.

## Verification and recovery

- Check that a new database and an older database both start with automatic
  checking on. Save off then on and confirm the value persists after a new
  dashboard session.
- Verify the preference routes require admin sign-in, reject invalid values
  and do not change the network configuration version.
- With the preference on, confirm one status read happens after dashboard
  open and that it does not delay normal dashboard controls. With it off,
  confirm no automatic status request occurs and the manual button still
  works.
- Verify a different staged release label shows the **Release staged** badge
  and a matching label does not. Check an older staged label and an
  unversioned installation to ensure the UI does not claim version ordering.
  Also check the no-release and helper-error states.
- Make the preference lookup fail. Confirm automatic checking is skipped and
  the manual check still works.
- Confirm no release is downloaded or installed by an automatic check. Keep
  the existing confirmation step for install and rollback.
- Exercise the toggle and badge at desktop and phone widths. Confirm the
  badge has a readable label and does not rely on color alone.
- Run `python -m unittest discover -s tests`, the repository Ruff command,
  `tests/kernel/run-in-docker.sh` and the browser journey. CI runs these
  checks on clean Linux runners across Python 3.9, 3.11 and 3.13.
- Deployment is a code-only update with an additive table default. No
  backfill or service restart change is planned. Reverting the code leaves
  the extra table harmless and retains the saved preference for a later
  install.
