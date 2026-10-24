# Token sign-in form errors

The token sign-in currently keeps entered values but a failed check only updates status text and a toast. This change adds an accessible error summary for that form while preserving the dark design, authentication flow and existing announcements. A blank or rejected token will be marked invalid. A connection or service failure will offer recovery without calling the token invalid. No open product questions remain within this slice.

## Scope and decisions

- Keep the existing token field, password-manager markup and token status ID.
- Add a hidden focusable summary with a link back to the token. Reuse the existing error class and status text as the field description. Keep the single announcement path.
- Update the token check handler only. Show the summary after current failures and clear it before retry, after edits and after success. Focus it only when the person submitted from the token form so background recovery cannot take focus.
- Preserve token and remember choices. Preserve revision guards so an old response cannot repaint a newer sign-in.
- Do not extend shared form helpers, other credential forms or other dashboard handlers.

## Verification

Add a uniquely named offline Node harness driven by pytest. Exercise the real token handler for failed then successful sign-in, empty input, connection/service failure and an older response after a token edit. Assert token/remember values, invalid state, described error, summary link, focus and cleanup. Run only bounded offline checks through the shared wrapper when its environment can do so without installing dependencies or touching the release environment. Browser and full suite validation remain deferred to the owner.

## Risk and rollback

This is a bounded front-end state change with no endpoint, stored data, authentication or deployment changes. Summary content uses textContent and does not add live regions. The main risk is stale or background responses moving focus. Capture whether the check began within this form and retain existing revision guards. Rollback consists of reverting the two source edits and the unique regression test.
