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
