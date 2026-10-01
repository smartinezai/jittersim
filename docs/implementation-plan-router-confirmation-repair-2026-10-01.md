# Router Wi-Fi confirmation repair

## Human review summary

Fix the failed router-on action seen during the real Pi Wi-Fi acceptance test.
The dashboard exception passed while both router radios were off. Turning the
router back on returned an uncertain result and needed independent recovery.
The current release includes the live switch but retains that write path.

First identify the failed step using bounded diagnostics that contain only
fixed step names, elapsed time and radio flags. Keep passwords, settings bodies
and session values out of logs. Reproduce the failure before changing the
protocol. Correct the observed cause while preserving one write per action,
the total deadline, router session protection and verified state reporting.

Risk is high because the action changes network access. Keep the Pi's Ethernet
link connected and use independent recovery for every physical radio test.
Preserve Wi-Fi credentials, saved dashboard access and the settings database.
No migration or new stored credential is needed. There are no open product
choices. The owner requested immediate takeover after the failed acceptance
test and authorized implementation, tests, publication and deployment.

## Work and verification

1. Confirm the current checkout, installed release and Mac network path. Keep
   unrelated changes untouched. Read the actual firmware behavior and isolate
   the failure without switching router radios until recovery is armed.
2. Review this repair plan and the proposed failure handling before production
   edits. Use the existing approved router design and the owner's instruction
   to proceed without another approval pause.
3. Add useful failure details with fixed values and reproduce the observed
   behavior. Implement the smallest protocol or confirmation correction.
4. Add regression coverage proving the failure before the change and success
   afterward. Cover preserved settings, one write, deadline exhaustion,
   authentication loss, unknown or partial radio state and sensitive log data.
5. Run focused checks, the full existing suite and the clean Linux package
   checks used by CI. Review the complete change before publishing.
6. Build the exact tested release. Save a root-private recovery copy and use
   the existing installer. Verify installed files, services and authenticated
   dashboard reads. Restore the preceding release if installation fails.
7. Run real off and on through the dashboard with independent recovery armed.
   Verify both radios, usable Mac dashboard access and unchanged saved settings.
   A recovery-only success does not count as a repaired on action. Restore the
   starting Wi-Fi names, connections and VPN before reporting completion.

## Bounds and recovery

One router action may run at a time. Do not queue work or automatically repeat
a settings write after a lost response. Read-back and any renewed sign-in stay
within the original operation deadline. Keep result retention and diagnostics
bounded. The repair must not expose credentials or widen dashboard access.

Code rollback restores the release and service settings. Router radio state
needs the independent wired recovery path. Record a failure accurately if
physical acceptance cannot finish. Do not mark the repair complete from local
checks, a dashboard health response or a successful backup recovery alone.

## Findings and chosen correction

The installed live-switch release was `9ffca9b`. Its real router-off action
returned 200 and its later router-on action also returned 200. The preceding
release recorded off 200 and on 502. The older test retained no underlying
confirmation failure detail. It does not establish whether a competing router
session or a temporary restart response caused that failure.

The current helper already prevents overlapping dashboard router sessions.
Keep that protection. A firmware fixture demonstrates another failure: a 404
or temporary non-JSON settings page after the single write stops confirmation
immediately. Retry these read-only responses within the existing eight attempts
and original deadline. Also let an unreachable read after the one allowed
authentication refresh use that same remaining retry budget. Never retry a
settings write or switch to the older write protocol after sending a change.
Tampered responses and repeated authentication failures still stop safely.

Failure logs contain fixed step and reason values, requested state, attempt
count, elapsed time and three parsed radio flags. They contain no response
body, password, Wi-Fi name or session value. Replace guidance requiring a Mac
Ethernet connection with guidance to wait for a fresh verified router status.
The fix covers demonstrated restart failures. The exact cause of the earlier
hardware failure remains unproven and must not be claimed as diagnosed.
