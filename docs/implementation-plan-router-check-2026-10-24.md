# Reachable router Wi-Fi check

Reproduce with the existing synthetic router fixture and counted status requests:
missing password blocks checking; accepted password leaves the footer's Check text
noninteractive while the real button is hidden in closed Controls. No live router.

One writer owns the footer markup, existing render/binding and narrowly scoped
button CSS plus router regressions. Expose a native Check action beside the plain
read-only state, sharing the original disabled expression and pollRouterWifi
handler. Preserve the Controls action and all backend/password/busy/rate gates.
Use existing status text for prerequisites, not a new announcer. Verify desktop,
375/320px, pointer/keyboard,44px, no overflow/Panic obstruction and exact read-only
request counts, then affected unit/budget/lint, independent review and draft only.
