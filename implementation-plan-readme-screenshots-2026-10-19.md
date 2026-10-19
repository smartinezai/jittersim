# Update README screenshots for address changes and speed tests

## Summary for human review

Refresh the README visuals so they show two recent dashboard features: reviewing a saved device's new private Wi-Fi address and opening the device speed-test controls. Generate both images from the existing local browser fixture. Update the README, screenshot generator, user guide and documentation index so every image is described and can be recreated without a real Pi or network.

## Goal

Make the README and screenshot set match the merged dashboard features and keep screenshot generation deterministic.

## Scope

- Add docs/screenshots/address-changes.png from the existing address-follow fixture flag. Show the Address changes card, its Ask setting and one pending sample suggestion before any settings move.
- Add docs/screenshots/device-speed-test-controls.png from the existing seeded fake phone. Show the initial speed-test dialog with the site, duration, label and Start measuring controls. Do not start a measurement or open the external site.
- Extend scripts/docs-screenshots.py to generate both images alongside the existing five. Start each fixture locally and terminate it in a finally block. Use fixture-only helper, router and prober implementations.
- Update README.md: clarify the speed-test and address-follow bullets, then show both new images with alt text that describes only visible sample controls.
- Update docs/user-guide.md: place the address image in its Address changes section and the speed-test image in its Device speed test section.
- Update docs/README.md: list all seven generated images and describe how the script retakes them.
- Include generated image assets and the listed documentation changes. Exclude unrelated README rewrites and all changes from the dirty main checkout.

## Decisions and risks

Each capture uses synthetic browser fixture data. The address fixture supplies a saved sample phone and one pending new-address suggestion. The speed-test fixture supplies the fake phone and helper. The screenshot stops at the initial dialog so it cannot imply a completed test or real throughput. Neither fixture calls a real Pi, router, device, LAN or speed-test service.

The origin/main ref was fetched and is at 18c3f3d4 (Refresh the docs, changelog and screenshots from the merged main (#156)). The handoff says PR 156 is merged and requests refreshed captures for the Address changes card and speed-test controls. PR 161 remains unrelated to these captures. Recheck file overlap before publication.

## Verification

- Run python scripts/docs-screenshots.py --out <temporary-directory>. Confirm it writes the two named images alongside the five existing captures.
- Inspect both images. Confirm the address card shows the sample pending suggestion and Ask setting, the speed-test dialog shows its initial controls, text is readable and neither image is clipped.
- Compare output hashes with the tracked five images and preserve all five byte-for-byte. Copy back only the two new images unless a separate, evidence-backed correction is needed.
- Run python -m pytest tests/unit/test_docs_links.py and git diff --check. Verify the README, user-guide and docs-index image links resolve.
- If the prescribed local CI runner is available, run its lint and changed-browser steps on this exact candidate. Record any setup or hosted-CI failure separately from passing focused checks.

## Delivery

Keep the work in this isolated worktree and on its codex/readme-screenshots-2026-10-19 branch. Do not merge.
