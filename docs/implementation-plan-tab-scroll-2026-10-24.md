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

Outcome: unchanged1.0.0 source reproduced287px downward scroll on Profiles activation
(receipt local-ci-20261024-232630). Fix468aa721 resets document top while preserving
preventScroll page focus;148 focused offline/budget checks, lint and syntax pass.
Existing dashboard_navigation browser module passes at468aa721 on1440/1280 desktop
and320/360/390 phone widths, with keyboard, pointer, Back/Forward and native focus-only
skip-link checks. Independent coordinator reviewer cleared this exact source delta.
No skip-link CSS, extension settings, network controls or release package changed.
[Failed and passing receipts and synthetic captures](tab-scroll-regression/provenance.json)
are retained alongside this plan. No physical Pi/Safari/assistive-technology claim.
