# Notification wrapping — 24 October 2026

Base: ec403d0f8f347b430398ee5cc281add1aa309318. Coordinator confirmed toast-only CSS and unique notification regression ownership, including one sidebar_status.py import/call. Other dashboard selectors and shared docs are excluded.

Preserve full recovery instructions, persistent error messages, existing dismissal/focus return and the two permanent live regions. No HTML, JavaScript, copy, networking or stored-state change is required.

Allow unbroken message text to wrap anywhere. Below30rem, put the message on its own full-width grid row. Reserve the existing control row, gap, padding and borders in the message height cap, retaining at least one line when space is exceptionally short. Short messages keep their natural height and long messages scroll within the remaining40vh space; retain the phone safety-bar offset and original controls.

Measured regressions drove the sizing: wrapping alone left the message at zero width at320px/200%; an unconstrained second row let End scrolling move Dismiss out of view; an explicit fractional grid fixed long messages but enlarged short results at200%. The final rule uses the existing documented dimensions directly. Keep those failed runs in ignored verification evidence rather than discarding them.

Registered browser coverage checks320x640 and1440x1000, normal and200% root font: unbroken names, full exact message/alert text, no horizontal message scrolling, viewport and safety-bar clearance, Tab/End/Tab/Enter, dismissal focus return, compact short results and no extra short-message tab stop. Preserve existing phone touch-scrolling coverage.

Use /private/tmp/claude-501/jittersim-heavy.sh for exact-head sidebar, GUI, lint and affected native checks. Independent review is required before a coherent tested draft PR; no merge. No browser/Docker load during the reserved study, pinned files, live device contact or dashboard redesign.
