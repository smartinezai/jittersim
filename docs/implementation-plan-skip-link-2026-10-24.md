# Focus-only skip-link release fix

Investigate the existing focus-only link locally without changing the live Pi or
extensions. Baseline: normal desktop/phone and2× visual page scale hide the link
when unfocused;320px with32px root text wraps it to138.375px high, exceeding the
fixed128px hiding offset and leaking10.375px after pointer navigation.

One writer owns only the skip-link CSS and existing navigation regressions. Hide
by the element's own height, retaining native href/main focus and focus-only reveal.
Preserve baseline/confirmation metrics and synthetic screenshots. Run the bounded
four-case matrix and existing affected navigation module, unit/budget/lint checks,
then independent delta review and a separate draft. Do not change extension
settings, installed signed1.0.0, release assets, other layout or features. The owner
screenshot was unavailable; local reproduction is not claimed as pixel identification.

Outcome: baseline234923 demonstrates a10.375px unfocused border leak. Fixed
sourcea0575b5a passes the four-case matrix and full existing navigation module
in235143;131 focused unit/budget tests and lint pass. Independent exact-source
review CLEAR. Native Skip still appears on focus and moves focus to main content
without changing the selected page; ordinary navigation hides it again. A focused
link can properly be large with enlarged text. Owner screenshot pixels remain
unavailable, so no exact screenshot-cause match or Helperbird repair is claimed.
Evidence and capture hashes are retained in skip-link-regression/. No deployment.
