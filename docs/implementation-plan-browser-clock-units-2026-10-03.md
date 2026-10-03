# Use seconds for the polling test clock

## Review summary

The polling test passes JavaScript milliseconds to a browser clock API that
expects Unix seconds. That pauses the test thousands of years ahead. Convert
Date.now() to seconds and add one second before pausing so ordinary elapsed
execution cannot make the requested pause time lie in the past.

This changes one test expression. Application behavior, system clocks, stored
data and production deadlines remain untouched. Independent review confirms
the mismatch against the installed Playwright client and approves the correction.
The existing implementation authority clears the human plan pause.

## Verification

Run the actual polling browser module against the exact current installed
application wheel. Preserve every one-write, request count, relative read budget
and diagnostics assertion. Save the original 64.43-second receipt with its
original test bytes. Run the fresh complete release pipeline on the final merged
source after this focused test fix. Reverting the expression restores the old test.
