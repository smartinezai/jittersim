# Implementation plan: readable traffic chart

## Human review summary

Keep the existing dark chart and its line inspection controls. Scale the plot
from measured traffic so a slow test remains readable beside a fast ISP plan.
Keep offscale limits explicit instead of making them look like measurements.
Add a quiet text view of the same bounded samples and a manual sampling pause.
The main risks are misleading limit positions and showing an average across a
paused interval. Neither the API nor packet handling changes.

## Evidence and scope

At ec403d0f the axis includes every reference and rounds to powers of ten.
The existing chart retains 30 samples for 60 seconds and supports hover, legend
focus, pinning and Escape. It has no manual pause or sample table. This work
implements item 2 of the 16 October backlog audit. The owner authorized
independent implementation. D4 option b still needs owner approval to merge.

## Decisions

- Use a 1-2-5 axis ceiling from the measured upload/download peak with 20 percent
  headroom and a 0.1 Mbit/s minimum. Reference values never stretch the axis.
- Draw only reference portions within the plot. Mark above-axis references at
  the upper edge and list their full labels and actual values beside the chart.
  Preserve their real values for legend inspection and pinning.
- Add a latest interval sentence and a collapsed table of the last 30 measured
  intervals with upload/download rates. Refresh on the existing two-second
  sampling path, not pointer redraws. No live region.
- Pause keeps the last samples and freezes their time window. Resume starts a
  fresh graph and counter baseline so the gap cannot become a measured interval.
  The manual pause survives page and tab changes. Private-data reset still clears history.
- Reuse existing colors and native controls. Keep the current canvas, legend
  and touch behavior. Scope CSS to the added chart content.

## Verification

1. Focused Node-backed chart tests cover step boundaries, zero/missing data,
   offscale references, sample bounds and pause/resume behavior.
2. Extend the existing browser journey for 2 Mbit/s with a 100 Mbit/s ISP plan,
   truthful offscale labels, quiet table semantics, a five-second pause with no
   new graph polls, resumed sampling and desktop/phone fit. Browser work waits
   for the shared hardware slot coordinator.
3. Use the required local CI wrapper for selected gates after scoped review.
   Run syntax checks and diff whitespace checks. Record evidence under ignored
   test-results/traffic-axis/report.md. No full suite before scoped review.

## Deployment, security and recovery

This changes static dashboard assets only. No new endpoint, dependency, stored
setting, log or external request is added. Existing sign-in and private-data
clearing apply. Memory stays bounded at 30 samples. The normal dashboard asset
packaging delivers the change. Reverting the focused commit restores the prior
chart with no migration or backfill. Risk class is B. Household risk is none
because packet handling is untouched. Presentation correctness needs focused
review. No Pi, router or pinned networking file is touched.


## Local verification checkpoint

The focused chart tests passed 38 cases and asset assembly passed 17 cases.
The local lint gate, JavaScript syntax check and whitespace check passed.
The chart tests include a 2 Mbit/s measurement using 40 percent of its 5 Mbit/s
axis while the 100 Mbit/s ISP reference remains explicit.

Browser verification waits for the shared hardware coordinator. The intended page-size
growth was measured and the existing updater refreshed four non-strict limits.
Strict counters and all other limits remain unchanged. No new colors, font sizes or margin values were added.
This checkpoint is not a claim of completed browser or full release testing.


## Focused correction after independent review

A new baseline must wait for any older shared device-workspace request to finish
then request fresh counters. Check the pause, sign-in and sampling revision again
before that new request. This prevents Resume from using counters obtained
during the paused interval and prevents a second Pause from starting a request.

Use the API's sampled_at timestamp for rate intervals and the text table's
recording time. Keep browser monotonic time for chart positions and retention.
Ignore repeated server timestamps. Reset the baseline on reversed or invalid
timestamps while preserving counter-reset behavior. Tests must exercise the
served poll function with delayed responses and repeated or reversed timestamps.

The backlog's D4 option b still needs the owner's approval before merge. Current
authorization covers implementation and concrete verification, not that merge.
