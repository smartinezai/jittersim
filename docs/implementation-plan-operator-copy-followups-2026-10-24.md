# Correct stale operator-facing instructions

## Review summary

The router-outage note names Apply although the action is Start. Local-CI help and documentation also retain older module counts. Correct these verified text defects without changing any behaviour. Avoid new hardcoded counts by referring to the configured module list.

## Scope and decisions

- Change only Apply to Start in the existing router-outage policy note.
- Describe browser and release checks by their configured jobs and point to local-ci list rather than duplicating counts.
- Mark the corresponding recorded text fixes as prepared on this branch rather than delivered to main.
- Keep command names, options, workflow lists, request handlers and safety logic unchanged.

## Risks and verification

This is a low-risk text change. Read the exact source before editing and check that only intended prose changes. Run existing local-CI, dashboard-source and documentation-link tests plus lint through the shared wrapper. No new unit tests are needed for two simple text corrections. Obtain independent source review before a held draft.

No schema, data, interface or deployment change is needed. A source revert reverses the change. Do not contact hardware or use credentials. No pinned source changes, signing, publication or merge. The known-good candidate remains unchanged.

## Scope review

The coordinator reviewed the current source and the existing planned-fixes entries before creating this plan. Autonomous implementation and documentation corrections are expressly authorised in the current session. Implement only the bounded copy changes described above.
