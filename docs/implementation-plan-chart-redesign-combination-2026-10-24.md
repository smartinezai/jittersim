# Combine the traffic chart with the Operate shell

## Review summary

Combine the reviewed traffic chart changes with the completed Operate shell in
one isolated local branch. Keep both original histories and their behaviour.
Resolve overlapping dashboard source and tests with the smallest changes needed.
The owner has authorised implementation and parallel verification for this task.
No main update, push, pull request, deployment or Pi operation is included.

The source base is PR240 at `b44b38ccb000e7cd9ad6c4056e0f994663e0f60e`.
Merge PR237 at `415d4595c268eff59dce15653a7a49f4ebd12ca7` rather than copying
its files. Their common base is `ec403d0f`. Main remains reserved for release.

## Implementation and review

1. Commit this plan before merging. Inspect every conflict against both parents.
2. Preserve Operate navigation, compact controls, safety bar and all existing
   dashboard features while bringing across the reviewed chart data scale,
   reference markers, inspection and fresh counter time handling.
3. Measure the assembled page with the existing GUI statistics tool. Preserve all
   seven strict records, metric definitions, checks and the two percent tolerance.
   Submit exact measured non-strict record changes for coordinator review before
   changing them. Do not choose either parent's JSON as final evidence.
4. Run the focused chart, assembly and GUI budget units plus applicable lint under
   the shared wrapper using a private environment. Freeze source and provide the
   exact combined commit for independent composition review.
5. Leave browser and GUI checks to the coordinator's scheduled slot after that
   review. Record inherited evidence separately from new composition evidence.

## Boundaries and risks

No new design, dependency, API, backend or production network change is needed.
Do not touch panic_guard.py, networking/helper.py or networking/access.py.
Chart source and shell markup may overlap and page size records will change.
Review the assembled output and verify preserved controls rather than assuming a
clean merge establishes compatibility. Existing bounded polling and chart data
limits remain intact. Authentication, data retention and deployment are unchanged.
Recovery is a local branch revert until a separate publication decision is made.
