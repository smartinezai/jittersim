# Implementation plan: a neutral sign-in page

## Reviewer summary

Today anyone on the household network can open the dashboard address and see
the whole page: its name, layout and every feature. The data and the actions
already need the admin token. This puts a neutral sign-in page in front of
everything. Without a valid sign-in, the address shows only a plain page that
asks for the token, and nothing names JitterSim or hints at what is behind it.
Signing in with the admin token keeps that browser signed in for 7 days or
until it signs out. The work also removes the other places where the name
leaks to someone without access.

## Who sees what

- **Not signed in:** `/` shows the sign-in page: the title "Sign in", one
  password field labelled Token, and a Sign in button. It has no script,
  product name, logo or links. Every other address answers "not found" or
  "sign in required" with a generic message.
- **Signed in:** `/` shows the dashboard, and the API works as today.
- **Scripts:** API requests with `Authorization: Bearer <admin token>` keep
  working without a browser session.

## Decisions and risks

- **Signed cookie, no server state.** The session cookie holds an expiry time,
  a random value and an HMAC-SHA256 signature keyed by the admin token.
  - Dashboard restarts and Pi reboots keep browsers signed in.
  - Changing the admin token signs every browser out.
  - Sign out clears the cookie in that browser. A copy of the cookie taken
    before signing out would stay valid until it expires, which is acceptable
    for a home lab; changing the token ends it.
- **Cookie attributes:** named `session`, `HttpOnly`, `SameSite=Strict`,
  `Path=/` and 7 days. It is `Secure` when the dashboard serves HTTPS, as the
  Pi does; configuration-only runs over plain HTTP on a laptop leave that out.
- **Cross-site requests.** `SameSite=Strict` keeps the cookie off requests from
  other sites. On top of that, a write authorised by the cookie must send
  `Content-Type: application/json` and, when the browser sends an `Origin`,
  one that matches the dashboard's host.
- **Guessing.** After 5 failed sign-ins from one address within 15 minutes, that
  address can't sign in for 15 minutes; the page says to try again later. A
  successful sign-in clears the count. The admin token is 256 random bits, so
  this mainly keeps the log quiet.
- **Other places the name leaks:**
  - the `Server` response header says `jittersim` and becomes a neutral value;
  - `/api/health` is public and describes the helper and the mode, so it now
    needs a sign-in like the rest of the API;
  - the installer's HTTPS certificate is issued to `jittersim.local` and becomes
    the Pi's own host name plus its address. A Pi named `jittersim` still shows
    that name, so the guide suggests renaming it;
  - the installer's default test Wi-Fi name is `JitterSim-Test` and becomes
    `TestNet` for new installs. Existing installs keep their name, which the
    dashboard can change.
- **Client isolation on the test Wi-Fi.** The installer turns on the access
  point's client isolation, so a visitor's device can't reach other test Wi-Fi
  devices, such as a laptop's shared folders. Casting between test Wi-Fi
  devices stops working too; the guide shows how to turn isolation off. The
  test Wi-Fi restarts once on the update that turns it on.
- **No token configured.** The sign-in page says sign-in isn't set up.
- **Unchanged:** the firewall still blocks the dashboard from the test Wi-Fi,
  and the CLI and helper don't use the dashboard.

## Dashboard

- The page loads its data right away, because only signed-in browsers get it.
- The Control token card becomes a short "Signed in" line with a Sign out
  button.
- API calls rely on the cookie and stop sending the token header. Any
  "sign in required" answer returns the browser to the sign-in page.

## Work slices

1. Session cookies: signing, checking, expiry and the guessing limit.
2. The sign-in and sign-out routes, the neutral page, and cookie or bearer
   checks on every protected route, including `/api/health`.
3. The neutral `Server` header and the cross-site checks on cookie writes.
4. The dashboard: loading on arrival, sign out, and returning to sign in on
   401.
5. The installer's certificate name, default Wi-Fi name and client isolation,
   plus the guides.

Each slice is committed separately with its tests.

## Verification and recovery

- Unit tests:
  - the sign-in page and every unauthenticated response, headers included,
    contain no product name;
  - cookies expire, and a tampered cookie is refused;
  - the guessing limit works;
  - bearer requests still work;
  - cross-site writes are refused;
  - the installer builds the certificate from the host name.
- Browser check:
  - the page before sign-in;
  - a wrong token, then the right one;
  - staying signed in across a reload;
  - signing out;
  - no console or content security policy errors.
- On the Pi: sign in over HTTPS from the laptop and from the Tailscale address.
- Reverting the branch restores the token box. Signing out, or changing the
  token, ends every session.
