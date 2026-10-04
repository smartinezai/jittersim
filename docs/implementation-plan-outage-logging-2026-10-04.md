# Preserve evidence for JitterSim outages

## Human review summary

Keep useful Pi logs after a restart and make it easier to match failures with
profile changes, resets and Wi-Fi operations. Use limited local storage and a
filtered manual export. Reuse the existing journal and action history before
adding another background service. Diagnostics must not log credentials or
browsing history and must not create automatic contact with the router.

Risk is high because installation touches a privileged Pi with limited storage
and household network access. Inspect the existing system logging policy before
selecting limits. Preserve administrator settings and make the logging change
easy to remove. Logging failure must not block Apply, Reset or recovery access.
This improves future evidence. It does not identify or repair the cause of the
two Vodafone outages and cannot recover already lost logs.

The owner requested a fresh chat to implement, test, push and merge this feature
as soon as possible on 2026-10-04. The current release candidate is frozen at
44538ddad13611a91379b1030d6f063d5c54b419. Keep this follow-up separate and
coordinate its merge and eventual installation with the release owner.

Review status: draft. The fresh chat must review this proposal and present its
summary and findings for the owner's feedback before implementing production
code, as required by the personal working agreement. Independent inspection and
verification preparation can continue while that feedback is pending.

## Evidence and scope

- The investigation could read only the Pi's current boot journal.
- The user had reset both the Pi and Vodafone router before those checks.
- The user reported router Wi-Fi off and the Pi advertising the copied Vodafone
  name during the failure. Healthy post-reset checks do not establish that state.
- Existing action history identifies several profile failures but does not
  identify the physical outage trigger. Preserve both start and outcome evidence.
- Read the [incident findings](router-incident-findings-2026-10-04.md) before
  choosing additional fields or claiming what logging could prove.

## Proposed work

1. Inspect the supported Pi OS, installed logging configuration, service units,
   installer, update path and existing action records. Verify the documented
   journal behavior against official systemd documentation. Choose a supported
   way to retain the previous boot without weakening an administrator's policy.
2. Set reviewed limits on local disk use, minimum free space, retention age and
   repeated messages. Install only project-owned configuration through the
   normal installation and update path. Avoid dumping full journals into Git.
3. Add only missing operation evidence. Prefer time, boot identity, operation
   identity, configuration version, action, duration and a safe outcome category.
   Cover profile apply/reset, scheduler failures and Wi-Fi changes without
   recording request bodies, passwords or full command output. Keep ordering
   useful when the Pi clock has not synchronized.
4. Provide a bounded manual export of selected service, kernel and network
   events from the current and previous boot. Filter and redact before export.
   Enforce access checks. Exclude traffic contents, browsing history and
   protected-device observations. Report gaps or limits clearly.
5. Document what survives a normal restart, what an abrupt power loss may lose,
   how to export evidence and how to remove the project's logging configuration.
   Keep failures quiet and bounded. Do not add a recurring router login or a
   network probe that could reproduce the incident.

## Verification and release

- Review every retained and exported field for credentials and personal data.
  Test with recognizable fake secrets and verify that they do not appear.
- Test size and age limits, repeated failures, low disk space, missing previous
  boots, unsynchronized clocks and denied journal access. Logging problems must
  leave application actions and recovery usable.
- Verify installation, repeat installation, update and removal in clean Linux.
  Confirm previous-boot persistence through a controlled reboot of a disposable
  Linux environment. A container without journald does not prove this behavior.
- Run appropriate application and installer checks against the exact feature
  source. Review the complete change before one final push and the feature PR.
  Merge only through the release owner's current checked integration route.
- Preserve the frozen candidate and other owners' work. If this feature changes
  the release source then repeat its required checks for the new source.
- Hand over the final commit, test evidence and removal steps for installation.
  No Pi restart, router write, profile activation or live deployment is part of
  this chat's setup action. The release owner manages physical acceptance and
  deployment with the user's existing authorization and recovery requirements.
