# Reset network tooltip positioning

## Reviewer summary

Keep the Reset network explanation beside its help icon instead of at the
bottom of the window. The current sidebar rule deliberately fixes the tooltip
to the bottom to avoid clipping inside the scrolling sidebar. Replacing it
with ordinary absolute positioning would restore that clipping problem.

Use a single shared tooltip outside the scrolling sidebar for its two action
help icons. Position it from the active icon and keep it inside the visible
window. Preserve the existing explanation text and visual style. This is a
small frontend change with no network behavior change. The main risks are
clipping on short windows and losing keyboard access. No product decision is
open. The user approved urgent direct implementation with testing, push and deployment on 4 October 2026.

## Current evidence

- `src/jittersim/api/server.py` contains the dashboard HTML and styles.
- `.sidebar` scrolls vertically and becomes a normal block on narrow screens.
- `.help-icon::after` supplies the existing explanation text.
- The sidebar override at the desktop breakpoint uses `position: fixed` with
  `bottom: 1rem` and `left: 16rem`. This directly explains the attached image.
- The narrow-screen rule also places help text at the bottom of the window.
- Start with saved settings shares the same sidebar tooltip rule.
- `tests/browser/dashboard_navigation.py` already checks desktop and mobile
  layouts against a local dashboard fixture.

## Scope and decisions

1. Keep this change limited to the two sidebar action help icons. Leave form
   field help positioning unchanged.
2. Render one explanation outside the sidebar so its scrolling boundary cannot
   cut off the text. Read its content from the existing help attributes.
3. Place the explanation beside the active icon on desktop. On narrow screens
   place it directly above or below the icon. Choose the available space and
   constrain its width and height to the visible window.
4. Support pointer hover and keyboard focus. Keep the text hoverable and allow
   Escape to dismiss it. Associate the visible explanation with its trigger
   for assistive technology.
5. Close the explanation when the active control leaves view. During scrolling
   or resizing either update its position or dismiss it consistently. Bound
   the work to one tooltip and one active trigger. Avoid polling.
6. Preserve colors and copy. Do not invoke Reset network during verification.

## Work sequence

1. Record the user’s urgent implementation and delivery authorization.
2. Add the shared sidebar explanation and its bounded positioning behavior in
   the existing dashboard. Remove the bottom-pinned sidebar fallback and
   suppress the duplicate pseudo-element for those triggers.
3. Extend the existing browser navigation acceptance checks for the demonstrated
   positioning regression. Do not add tests that merely compare CSS text.
4. Review the complete diff for focus behavior and event cleanup. Run the
   existing help-copy unit checks and dashboard browser checks.

## Verification

- Reproduce the current behavior using the local fixture before changing it.
- Check Reset network and Start with saved settings at 1440 by 900 and 1280 by
  720. The explanation must remain near its trigger and fully within the window.
- Check 390 by 844 and a short window with the sidebar scrolled. Include browser
  zoom or enlarged text to expose width and height limits.
- Verify hover, moving onto the explanation, keyboard focus, Escape and blur.
  Moving away must hide the explanation without moving dashboard controls.
- Confirm no horizontal page overflow or duplicate explanation appears. Capture
  desktop and mobile screenshots and inspect both.
- Run `python -m pytest -q tests/unit/test_dashboard_help_text.py` and
  `python -m tests.browser.dashboard_navigation` using the repository environment.

## Delivery and recovery

The existing CI browser job installs the package in a fresh runner and runs
dashboard navigation acceptance. No dependency or shared build setting change
is planned. The dashboard is embedded in the Python server source so the normal
package deployment must carry the changed source. Browser refresh is required
after deployment. No migration or backfill is needed.

No data is read beyond existing explanation text and element positions. No
credentials are copied or logged. Authentication and device isolation are
unchanged. The repository has no pull request template to classify this change
against. Treat it as low risk UI work and require the browser checks above.

Revert the focused frontend change to roll back. The user explicitly authorized remote publication and Pi
deployment after verification. Release coordination now holds those actions
during the critical Vodafone outage investigation. Preserve the installed
release and integrate this change into the official combined release. Preserve active network settings during this UI deployment.

## Learning focus

The central concept is choosing a positioning boundary that stays close to the
trigger without being clipped by a scrolling parent. Test fixture setup and
copy preservation are routine work. Urgency defers the comprehension check about scrolling boundaries until
the next natural review checkpoint.
