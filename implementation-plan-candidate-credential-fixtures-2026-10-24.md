# Address journey credential-panel compatibility

Two existing browser journeys assume Credentials stays open after a manual sign-in. The new legitimate phone handoff closes it when the token is accepted for a device still needing consent. Frozen candidate4c843cac therefore stops before privacy or address-move assertions at hidden controls. Repair only these test workflows in a separate branch, preserving the candidate and all active validation copies.

## Scope and decisions

- In address_move.py close Credentials only when it is still open after each unregistered sign-in. Preserve the two sign-ins, refusal, explicit move consent, undo and privacy assertions.
- In address_move_privacy.py add a small local UI helper that opens Credentials when hidden. Use it before sign-in and any token clear or edit requiring the panel. Close only if open before explicit Allow or address-move consent. Preserve every stale-reply, private-text and registration assertion and the existing deliberate held requests.
- Use visible UI buttons and ordinary Playwright interactions. Add no force clicks, state injection, application source changes or baseline updates.

## Verification and delivery

Send the complete two-file diff for independent coordinator review before committing. After review run only address_move and address_move_privacy through the mandatory wrapper in this repair worktree with its own copied offline runtime/cache and synthetic loopback fixture. The main release repair keeps priority. Preserve original immutable-run failures as evidence. A passing repair remains a separate local commit until the coordinator authorises candidate composition and final review.

## Risk and rollback

This is a fixture workflow correction with no product, auth policy, registration, API or deployment change. The risk is accidentally skipping a privacy assertion or granting consent early. Review every existing assertion and keep the consent clicks in their original sequence. Revert the two test files and this unique plan to discard the repair. No open product question remains.
