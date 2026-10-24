# Names added to the discovery list in the latest scan

Discovery already reads saved observations every five seconds on Traffic and Access. It does not identify which names were absent from the displayed list before a scan. Add a small “New to list” marker for names captured by the latest successful scan that were not listed when that scan started. Show the number of marked names in collapsed app groups and one persistent summary. Preserve manual scanning, existing refresh frequency, actions, counts and the dark interface. DNS observations never establish website success.

## Meaning and bounds

Compare device address plus domain, not domain alone. Keep the current successfully rendered list keys in memory and invalidate that snapshot when the current list is unavailable. Copy that bounded snapshot when Scan now is pressed. Compare only the scan response's captured pairs with the snapshot, deduplicating pairs. This adds no pre-scan request and accurately describes what was absent from the displayed list when the user started the scan. The API limits visible names per device, so the marker deliberately does not claim a name was never observed historically.

Keep the successful comparison through ordinary refreshes and navigation in the same browser tab. Replace it only after the next successful scan. If the list snapshot is unavailable, retain the captured count and explicitly say the new-name comparison is unavailable. Failed scans preserve the previous successful comparison. Clear temporary comparison state when private data is locked and invalidate pending scan completions so a late reply cannot restore it. Do not persist observations or keys in browser storage.

## Implementation and safety

Limit changes to discovery render/load/scan helpers, small discovery markup and associated tests. Use existing badge styles and the existing quiet polled list. Reuse the page's announcer for scan completion. Keep the scan button busy until its follow-up list load completes. Show group markers while groups are collapsed. Count only marked names actually present in the displayed list. Do not add automatic capture, background collection, new API fields, storage changes, network rules, dependencies or pinned source changes. Revert the change to remove the temporary presentation without changing settings.

## Verification

First verify the combined PR229/231 base and record its exact identity. Commit this unique plan before source edits and review it independently. Test repeated names, a new name on an existing device, the same domain on another device, duplicate captured pairs, zero additions, unavailable initial list, failed scan, successful replacement, refresh persistence, a private-data lock during a scan, failed follow-up reads and captured pairs omitted by the visible-list cap. Test existing polling and page navigation without extra Scan calls. Browser checks use routed local fixtures and exercise collapsed groups, persistent summary, ordinary refreshes and narrow fit. Run appropriate lint, unit, browser and GUI checks on the final clean head through the shared four-slot wrapper. Independent review must clear the complete diff before normal push and one draft PR. Keep PR229 and PR231 frozen.

Selected base: independently reviewed combined PR229/231 head 74ac8455a0e4b7ccb691be76b80ceae1254a52be, tree 50bda9897568a8eaa97d5517f52b1bd5bb8b2387. Initial main was verified at 53ec72bb94f09a33aa5b55971c5446163c24e419. No changes to either frozen PR branch.
