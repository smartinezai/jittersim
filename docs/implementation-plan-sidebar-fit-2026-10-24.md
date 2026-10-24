# Sidebar update badge fit

Keep the existing dark sidebar and its behavior. The current seeded dashboard
already shows Reset Wi-Fi name fully at 1366 by 768 pixels. The confirmed small
fix is the update badge which wraps its two words in desktop navigation. Preserve
its text and accessible name while keeping it on one line.

The main risk is allowing the badge to force horizontal overflow. Verify its
bounds inside the navigation link at desktop and phone sizes. A 390 pixel tall
landscape window still needs sidebar scrolling and does not keep Panic in the
initial viewport. That separate safety layout problem needs its own scoped design
and review. This change must not worsen Panic visibility or touch targets.

## Evidence before implementation

Base: ec403d0f8f347b430398ee5cc281add1aa309318.
The seeded Chromium fixture at 1366 by 768 and 1024 by 768 places Reset Wi-Fi
name at 694.56 to 732.95 pixels and Panic at 474.98 to 538.30 pixels. The update
badge is 31.28 pixels tall and wraps. At 844 by 390 Panic is below the initial
viewport. At 375 by 812 the existing fixed phone Panic target is 44 pixels tall.
Evidence is retained in ignored test-results/sidebar-fit.

## Work and verification

1. Prevent wrapping in the existing update badge style only.
2. Add a focused browser geometry test for the demonstrated regression. Keep the
   existing label and measure containment rather than repeating CSS source.
3. Check desktop and phone together in one confirmation batch. Include keyboard
   focus, overflow and Panic geometry. Use the shared second browser slot.
4. Run the relevant existing sidebar and update tests and the required design
   detector once. Do not run full browser or native gates in this lane.
5. Review the diff and commit the plan, CSS and focused test together. Record the
   exact commit and limitations in the ignored report. Do not publish.

## Operational scope

This is static CSS. It adds no data reads, stored data, network calls or new
endpoints. It ships through the existing packaged dashboard and rolls back with
its source revision. It does not change build configuration, the Pi helper or
router operations. Risk is low and bounded to navigation label geometry.
