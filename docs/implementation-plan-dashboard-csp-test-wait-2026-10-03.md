# Wait for private diagnostics without string evaluation

The final Linux browser run rejects the new delayed-private-read test at
its wait_for_function string expression. Chromium153 applies the dashboard
Content Security Policy to that evaluation. The Mac check passed on its
earlier browser. The policy must stay intact.

Replace only this fixture wait with a bounded five-second event loop that
reads the same condition through page.evaluate with a function. Require
the owner to be idle before starting the held real diagnostics request.
Keep real enrollment removal, original locked-markup checks, release of the
old reply and the assertion that private content cannot return.

Independently review the narrow test change. Run the full dashboard-access
module in actual bounded Linux and Mac browsers. Then run every browser
module on the frozen combined source before restarting full release checks.
Retain the complete bdbd631 Python pass and original browser failure with
their exact source identity. No application or security-policy change.
