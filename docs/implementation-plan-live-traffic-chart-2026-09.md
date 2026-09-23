# Implementation plan: live traffic graph

## Reviewer summary

Add a small live graph to the dashboard showing upload and download traffic for
assigned test devices. Reuse the Pi's existing firewall byte counters, expose
the directions separately and calculate rates from samples taken every two
seconds. Keep only the latest 60 seconds in the browser. The graph reports
traffic seen by JitterSim, not a separate measurement of internet speed.

## Decisions and risks

- Keep the existing total byte counters unchanged because quotas and scheduled
  behavior depend on them. Add direction-specific byte and packet totals for
  the graph.
- Protect the new counter endpoint with the admin token. Start polling only
  after the token is checked and pause while the dashboard tab is hidden.
- Show all assigned devices or one selected device. Do not imply unassigned
  clients are included.
- Use browser-native canvas drawing with no added package. Keep a bounded
  60-second window and compute rates from counter differences and elapsed
  time. Clear the graph baseline if counters reset after a network apply.
- Firewall counters count traffic handled by JitterSim. They are useful for
  comparing activity before and after a profile, but they are not an end-device
  speed test or a direct measure of experienced latency or packet loss.
- No database migration or stored samples are needed. The installer and helper
  protocol change together and will require a Pi redeployment.

## Work slices

1. Add direction-specific byte and packet totals to the helper response while
   preserving the current cumulative counters. Validate and test the new shape.
2. Add an admin-protected traffic snapshot route and a dashboard graph with a
   device selector, current rates, bounded history and clear unavailable/error
   states.
3. Update the README and roadmap, run helper/API tests, the full test suite,
   embedded JavaScript syntax checks and deployment-file checks. Commit each
   slice separately, then push the tested changes.

## Verification and recovery

- Confirm upload and download counters remain separate and their sum matches
  existing quota totals.
- Confirm the traffic endpoint rejects missing and incorrect tokens and works
  with a valid token in hardware mode.
- Confirm chart polling is bounded, stops when the tab is hidden or the token
  changes and recovers after a fresh token check.
- Confirm a profile reapply or counter reset does not create a false traffic
  spike. Check the graph using a connected client that generates both upload
  and download traffic.
- Revert the focused commits and redeploy the previous package if sampling
  affects helper responsiveness or reports misleading rates.
