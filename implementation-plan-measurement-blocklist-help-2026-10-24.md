# Measurement and block-list help

Two small dashboard copy changes explain existing behavior. The first gives the upload-test route from Check connection. The second discloses the global block list's existing 4096 resolved-address limit beside its editor. Risk class B, low household risk because only help text and its accessible association change. No open product questions remain after checking the existing controls, guide and compiler. Both tickets are explicitly approved for implementation by the coordinator.

## Ticket 1

Change only the explanation in `connection-check.js`. Keep the download-side limitation, name Test speed and its existing Where the test data comes from picker and tell people to select an external speed-test site and run it on the actual test device. The dashboard choice measures download only. Add one focused Node rendering test through the existing connection-check harness. Open the actual dialog and verify its help plus its sole local read request. Fail on measurement starts or external requests.

Run the existing dashboard connection-check and speed-test tests through the local CI wrapper with a private cached Python 3.12 environment. Commit this ticket before starting ticket 2.

## Ticket 2

Change only the global block-list editor's HTML/help hunk. Add a permanent note linked with `aria-describedby`. State that while JitterSim runs address blocking covers up to 4096 resolved addresses. With household access rules on DNS still refuses every listed name. With them off some listed sites may remain reachable above the cap. Describe a general limit without claiming it is currently reached. Reuse the existing served-HTML parser for one focused help/accessibility check and rerun the existing compiler cap tests.

## Boundaries and delivery

No API, measurement, DNS, engine, helper, storage, Apply or layout changes. No new counters, status or backlog rows. This copy adds no work proportional to list size and reads no new data. The existing dashboard assembly and wheel packaging deliver the text. No migration or deployment configuration changes are needed. Revert the corresponding commit to roll back without changing saved settings. Run focused native tests and gating lint only. Do not run browser, Docker, hardware or external services. Keep two local atomic commits with receipts for independent review. No push or publication.
