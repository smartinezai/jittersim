# Notification wrapping — 24 October 2026

Base: ec403d0f8f347b430398ee5cc281add1aa309318. Coordinator confirmed ownership of toast-message wrapping, focus/scroll accessibility and unique notification tests. Other dashboard selectors and shared docs are excluded.

Current error messages retain full text, stay visible until dismissed and announce once through the permanent alert region. Keep these semantics. Current message scrolling handles long ordinary paragraphs, but lacks unbroken-text wrapping; the existing regression exercises touch scrolling only.

Start with overflow-wrap:anywhere on toast-message. Verify the served dashboard at 320x640 and 1440x1000, normal and 200% root font, with long unbroken identifiers and full recovery instructions. Assert no horizontal scrolling in the message, full alert/message text, visible Dismiss, bounded viewport and preserved safety bar. Check keyboard scrolling of long messages before deciding whether a focusability change is necessary. Avoid extra tab stops on trivial messages when existing browser scroll focusability suffices. Request broader toast layout ownership only if the measured result requires it.

Add a unique browser regression helper and invoke it from an existing registered browser module only after coordinating ownership. Browser/Docker load remains held for the reserved study. Run applicable exact-head checks through /private/tmp/claude-501/jittersim-heavy.sh, independent review and coherent tested draft PR; no merge. No pinned files, live device access, truncation, error-copy rewriting or dashboard redesign.
