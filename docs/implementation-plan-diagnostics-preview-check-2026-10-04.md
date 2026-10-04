# Diagnostics keyboard request check

The navigation check mistakes the authenticated range preview for a configuration write. The endpoint only calculates values and returns them. Allow that exact POST while continuing to reject all other non-GET requests during keyboard diagnostics checks. Application behavior stays unchanged.

## Work and verification

1. Confirm the server returns before scheduler, storage and helper write paths. Check the existing read-only preview regression.
2. Exclude only POST to the exact `/api/profiles/ranges` path in the keyboard request detector. Preserve focus, arrow key, accessible name, desktop and phone layout checks.
3. Run the complete navigation browser journey. Obtain an independent source and test review.
4. Freeze a clean commit and repeat the configured local CI equivalents including clean builds, supported Python versions, browser journeys and kernel checks.

## Risk and recovery

This is a test-only correction. A broad method or path exemption would hide real writes so the allowed endpoint must match exactly. Revert the test change if independent review finds a write in that endpoint. Hosted billing failures and physical deployment acceptance remain separate evidence.
