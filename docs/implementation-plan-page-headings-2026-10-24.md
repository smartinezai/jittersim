# Main page heading

Give the main content one level-one heading that follows the current page label.
Keep the existing dark appearance and show only “Sign in” before authentication.
Use the existing visually hidden utility and page-title lifecycle. Preserve all
existing IDs, skip-link focus, navigation history and private branding rules.

## Scope and decisions

- Convert the sidebar brand heading to a paragraph with the same ID and appearance.
- Add a visually hidden main heading with neutral initial text.
- Update that heading from the current section's existing accessible label in the
  existing title updater. Its callers already cover navigation and authentication.
- Keep browser title output unchanged. Use textContent for heading text.
- Change only the brand selectors needed to preserve the paragraph's appearance.
  Coordinate the phone selector with its current owner before editing that file.

## Risks and checks

Authentication changes must clear a previous page label. Unknown sections need a
neutral fallback. A late brand reply must retain the existing request ownership
rules. Exercise actual served functions in Node and run existing branding and
navigation tests through the shared wrapper using an isolated offline runtime.
Check that the initial HTML has exactly one main h1 and all old IDs remain.
Browser, Docker and full gates remain deferred while the owner release runs.

This is low risk presentation work with no endpoint, storage, deployment or
data-volume change. Reverting the small markup, style and updater changes restores
the current behavior. No open product question remains within the approved scope.
