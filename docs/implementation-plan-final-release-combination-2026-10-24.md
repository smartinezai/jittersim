# Final reviewed release combination

Combine exact prepared-device head `d0d265e36c50b953101f3f9d0f5ad0763a17cd4b`
with CI harness head `96836d05f2c97f9bebf650151b65d1f988986839` and corruption
test head `cd017a927558d0f21a1f5f03cc514d3df51f927c` in one local branch.
The owner explicitly requested the combined release. This writer owns only
isolated combination. The coordinator owns main, publication and release work.

1. Inspect merge-tree before each normal merge and preserve all three histories.
2. Resolve only genuine harness or test overlap. Preserve the documented Docker
   prerequisites and prepared_devices browser registration. Add no feature or
   production source change beyond the reviewed inputs.
3. Freeze the exact commit and tree after whitespace and source identity checks.
   Report conflicts and the smallest meaningful focused checks for the existing
   reviewer and runner. Do not repeat broad gates or kernel tests blindly.

The first merge-tree inspection was clean. Main, original branches, pinned files,
release credentials and real hardware are outside this writer's scope. Local
revert is sufficient recovery before the coordinator's final integration.
