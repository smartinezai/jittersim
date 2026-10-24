# Short-window safety layout plan

The existing sidebar places Panic below the initial viewport at 844 by 390 pixels. Reuse the current phone header, fixed safety bar and scrolling Controls sheet when a window is short. Keep the current theme and controls. The main risk is CSS and JavaScript disagreeing about the layout, or desktop-only navigation rules leaking into the wider short-window view. Review the exact query and overlay limits before source edits.

## Baseline and reason

Base is `f6e360b7257fd58d22f88a83e9362e48c36ec2d4`, which contains frozen `ec403d0f` plus the reviewed update-badge nowrap change. The sidebar-fit report and confirmation image show Panic at y=474.98 through 538.30 in an 844 by 390 viewport. The sidebar scrolls but Panic can scroll out of view. Existing 375 by 812 evidence places the phone Panic target at y=760 through 804 with a 44 pixel height.

`phone.css` applies the safety bar only at width <=48rem. `dashboard-navigation.js` uses the same width-only query. Desktop rules in `dashboard.css` still apply at wider short sizes. Those give page navigation a 7rem minimum height, vertical overflow and a vertical fade. Existing browser tests cover tall portrait phones and tall desktop windows without the failing landscape dimensions.

## Proposed change for review

1. Set the main layout query in phone.css and the phoneLayout query in dashboard-navigation.js to exactly `(max-width: 48rem), (max-height: 36rem)`. The comma means either condition qualifies.
2. Use every pointer type. Safety depends on available height, so touch-only treatment would leave mouse-driven short windows affected. The added range includes 844x390, 1024x576 and 1440x500. Width >768 and height >576 at default media font size keeps the sidebar, including 1024x768 and 1366x768. At browser zoom the effective CSS viewport determines layout.
3. Neutralize the inherited desktop navigation minimum height, vertical overflow and vertical mask inside that compact layout. Reuse the existing horizontal row and its horizontal overflow hints. Do not add new navigation, shrink controls or remove actions.
4. Keep the final randomness-card media query width-based. Its column choice is driven by width rather than the safety bar. Keep width-based help placement unchanged unless a measured overlap proves a necessary scoped correction.
5. Preserve current Controls behavior, including scrollable sheet above the bar, Escape, outside click, focus return, page selection and lock handling. Check resize transitions in both directions and whether an open sheet requires state cleanup on entering the compact layout.

The 36rem height boundary is 576 CSS pixels with the default media font size. It sits above the reproduced Panic bottom of 538.30 by about 38 pixels and covers common short landscape windows. It does not promise full desktop footer visibility above that boundary. The goal is to pin the safety controls in the clearly short-window range with the existing mechanism.

## Focused checks

Use one agreed browser slot after the coordinator releases it. Batch the baseline and implementation checks and use at most one confirmation pass after correcting any discovered problem.

- Reproduce 844x390 before changing source using the exact focused browser assertion that will verify the fix. Reuse retained sidebar evidence as the visual baseline instead of another screenshot sweep.
- Check 844x390 with touch and fine pointer, 1024x576 at the added boundary and 1024x577 immediately outside it. Check 1440x500 for short wide desktop use, 390x844 for the original phone layout and 1366x768 for unchanged desktop layout.
- Include an effective 200 percent zoom case by reducing a 1688x780 desktop window to 844x390 CSS pixels and clearly label it as viewport reflow evidence. Include 112.5 percent root-font stress. Do not claim real browser zoom or physical-device acceptance from viewport emulation alone.
- On compact layouts verify Panic, read-only router status and Controls are fully visible with targets of at least 44 pixels, both initially and after page/Controls-sheet scrolling. Use a safe intercepted Panic request or recorded fixture action to verify the target remains clickable without operating real networks.
- Open Controls, scroll to each footer action and verify the sheet stays inside the viewport above the bar. Verify keyboard focus is visible, Escape returns focus, outside click closes and page selection focuses the selected page.
- Open Credentials in a short window, scroll to the token/check and Close controls and verify Escape plus focus return. Credentials and native confirmation dialogs retain their established overlay behavior. The always-visible Panic guarantee covers normal pages and the Controls sheet, not every higher overlay.
- Resize from compact to desktop and back with Controls/status open. Check synchronized CSS/JS layout, body state, usable focus and no page horizontal overflow.
- Run existing relevant unit modules plus the unique focused browser test. Adjust only the three existing test parser expressions that hardcode the old media header if approved. Otherwise the media-query change would make those tests parse the unrelated final width-only randomness block.
- Run Impeccable detector once at completion and distinguish unchanged warnings. Check diff whitespace and tracked scope.

## Boundaries and risks

Only phone.css, dashboard-navigation.js, the unique plan and focused tests are in scope. Two existing unit files need the minimum parser updates noted above. Do not edit dashboard.js, profile/discovery code, shared active documentation, pinned Panic files or release tooling. No hardware, remote writes or publication.

Risk class B. This changes local dashboard layout and focus decisions without changing network actions or storage. Rollback is a source revert. Desktop-only CSS overlap, very small heights, viewport resize and overlay layering need the focused checks above. Physical Safari/Android browser chrome and virtual-keyboard acceptance remain outside fixture evidence.

## Status

Approved by the coordinator on 24 October 2026 for the exact query, all pointer types, the existing overlay limits and minimum parser updates. Implemented and functionally verified. Two measured desktop navigation neutralizers are included. Independent review remains before publication.

## Approved static budget adjustment

The first focused run had 133 cases: 131 passed and two budget assertions failed because distinct media queries increased from 13 to 14. Preserving width-only random-card layout while adding the combined short-window query requires one extra distinct query. The coordinator approved only `distinct_media_queries` 13 to 14 on 24 October 2026. All other counters and limits remain unchanged. The baseline was edited directly for that one number without a general allow-growth command.

## First browser batch evidence

The first nine-case batch ran under the real browser-2 lock and released it at completion. Three cases passed. Four compact wider cases measured the inherited navigation at 112 pixels high with a vertical fade and overflow-y auto, while its horizontal row was 48.78 pixels. This proves the need to reset that minimum height/overflow and vertical fade. Panic at 844x390 already moved to 338 through 382 with a 44 pixel target. The font-stress case was stopped by the test's style insertion under CSP. The resize case met an immediate assertion before the media event settled. Correct those two test mechanics before classifying a source focus defect. Retain the initial logs, geometry and failed screenshots.

## Final behavior and evidence

The second and final visual batch passed eight of nine cases. All geometry checks passed, including the exact 576/577 pixel boundary. Navigation measured 48.78 pixels with no vertical fade. The remaining resize test needed a CSP-safe locator wait. Its corrected functional run proved that resizing with Start focused dropped focus to the body. A focused run then passed after the small focusout fallback. The final fallback also requires a visible enabled Controls toggle and no deliberate new focus destination. Seven Node cases cover those guards, including locked and disabled toggle states.

The final nine-case functional run passed under the browser-4 lock with screenshots disabled. Python process 54390 was recorded in its log and exited normally. The five focused unit modules passed 140 cases in 2.91 seconds. Impeccable detector ran once against the changed CSS and JavaScript, returned an empty finding list and exited zero. New tests pass focused Ruff and the diff whitespace check.

The landscape helper is called once by the existing phone_layout journey after its viewport loops, so the registered browser gate runs the regression without adding a workflow or module entry. The original sidebar-fit screenshots were the pre-change visual baseline. There were exactly two visual batches here. Further resize and final runs were functional checks without new aesthetic inspection. A queued browser-2 lock process, PID 49198, was cancelled only after checking it had no child. The coordinator rerouted that same functional check to the newly assigned browser-4 slot.

The effective 844x390 CSS viewport also describes reflow at 200 percent for a 1688x780 viewport. No real browser zoom, virtual keyboard, Safari or physical-device acceptance is claimed. The full registered phone_layout journey still needs its own gate result. Source and exact failures are retained in test-results/landscape-safety.
