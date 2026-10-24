# Tab navigation scroll regression

Owner reports that selecting a page scrolls the dashboard down. Reproduce on the
1.0.0 source with the existing synthetic dashboard-navigation journey before editing.

One writer owns `dashboard-navigation.js` and its existing unit/browser regressions.
Keep explicit page focus with preventScroll and reset navigation to document top,
so the header and page links remain visible. Cover pointer and keyboard switching,
Back/Forward, phone layouts and the focus-only skip link. Preserve intentional
revealElement scrolling to form controls. Do not change extension settings, runtime
network controls, package/version files or the README branch. Run only affected
navigation/phone/auth safety tests, lint/budgets and one bounded browser module;
obtain independent delta review, then publish a separate draft without deployment.
