# Let the Linux test job finish its existing checks

## Summary for review

The Linux datapath job on main `3d60f8a8` is canceled by its 10-minute GitHub
limit while tests continue to pass. Increase only that job limit to 25 minutes.
Keep application deadlines, test cases and failure assertions unchanged.
This is tooling-only risk class A with no household network effect.

Run 40199902199, job 121188668157, lasted 10 minutes 12 seconds before GitHub
canceled it. Four crash-matrix cases completed successfully before the
cancellation. A retained earlier complete local suite took about 16 minutes
18 seconds, including its smoke check. That older run is runtime evidence
only and does not establish correctness of current main. The current suite
contains additional tests. A 25-minute limit allows the full suite to run
while keeping a finite ceiling on a genuine stall.

## Work and verification

1. Independently check the full job log and the workflow's existing limit.
2. Obtain human feedback on this plan before implementation.
3. Change only the Linux job timeout. Add a short explanatory comment if
   needed. Avoid unrelated workflow cleanup or application timeout changes.
4. Check the workflow with the existing available YAML validation tools and
   review the complete diff independently. No unit test is needed for one
   static job setting. No clean build input changes.
5. After PR publication follow the real Linux job to its terminal result.
   If billing blocks execution use the existing local kernel runner through
   the shared resource wrapper. A longer limit is not proof the tests pass.

No data, permissions, dependencies, release assets or Pi settings change.
Rollback restores the old job limit. A remaining failure must be diagnosed
from its actual test verdict instead of hidden by more time.
