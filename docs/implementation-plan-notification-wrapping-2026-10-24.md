# Notification wrapping — 24 October 2026

Base: ec403d0f8f347b430398ee5cc281add1aa309318. Coordinator confirmed ownership of toast-message wrapping, focus/scroll accessibility and unique notification tests. Other dashboard selectors and shared docs are excluded.

Current error messages retain full text, stay visible until dismissed and announce once through the permanent alert region. Keep these semantics. Current message scrolling handles long ordinary paragraphs, but lacks unbroken-text wrapping; the existing regression exercises touch scrolling only.

Start with overflow-wrap:anywhere on toast-message. Verify the served dashboard at 320x640 and 1440x1000, normal and 200% root font, with long unbroken identifiers and full recovery instructions. Assert no horizontal scrolling in the message, full alert/message text, visible Dismiss, bounded viewport and preserved safety bar. Check keyboard scrolling of long messages before deciding whether a focusability change is necessary. Avoid extra tab stops on trivial messages when existing browser scroll focusability suffices. Request broader toast layout ownership only if the measured result requires it.

Add a unique browser regression helper and invoke it from an existing registered browser module only after coordinating ownership. Browser/Docker load remains held for the reserved study. Run applicable exact-head checks through /private/tmp/claude-501/jittersim-heavy.sh, independent review and coherent tested draft PR; no merge. No pinned files, live device access, truncation, error-copy rewriting or dashboard redesign.

Measured follow-up: exact832283be browser subset failed at320px/200% with message clientWidth0/scrollWidth23. Wrapping alone cannot overcome fixed icon/button tracks. Give #toast-message the full grid width on a second row below30rem, retaining the existing outer toast, controls, phone safety offset and live regions. No broader selector ownership is needed.

Second measured follow-up:061e672f restores full text width but End scrolling at320px/normal font also moves the outer toast, clipping Dismiss (viewport ratio0.413). Allocate the narrow toast's second row with minmax(0,1fr) and let the message shrink/scroll within it; remove the message's separate height cap only in that layout. Preserve outer40vh cap and avoid guessed pixel/rem deductions for the additional row. All failing runs remain in local evidence.

Grid sizing verification: b33d8a61 showed an auto-height grid does not constrain its fractional message row from max-height alone (message height1944). Set the narrow toast's definite height to40vh and cap it at max-content so short messages retain their natural compact height; long messages then have a definite remaining row size. No fixed deduction or new control is needed.

The inherited align-items:center also keeps the message at intrinsic content height even in its constrained row. Override only the message to align-self:stretch so its overflow area fills the available row rather than protruding from it. Regression diagnostics now include actual grid rows and alignment.
