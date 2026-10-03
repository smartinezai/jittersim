# Make raw diagnostics reachable from the keyboard

The existing raw diagnostics area can scroll at phone width but cannot receive
keyboard focus. The current forty-state accessibility capture reports this in
both phone appearances. Give that one area a clear name, normal keyboard entry
and the existing visible focus style. Keep the dark interface, content and phone
controls. This is a small interface change with no API or saved-data change.

The main risk is adding a focus stop that is unnamed or hidden behind the phone
safety bar. Keep sign-out clearing and pending-request protection intact. A test
must check actual keyboard entry and scrolling on served content rather than
only checking that an attribute exists.

1. Preserve the current source and accessibility observation with its exact
   capture identity. Save this plan in the implementation repository first.
2. Review the diagnostics heading, scrolling area and existing focus style.
3. Add named keyboard access and apply the existing visible focus treatment.
4. Test Tab entry, focus visibility and arrow scrolling with controlled long
   diagnostic output at phone and desktop sizes. Check the phone safety bar,
   enlarged fonts and sign-out behavior still work.
5. Run the current phone and private-data browser journeys plus both visual
   and accessibility captures on the final combined source.
6. Include the change in the next complete configured release gate. Keep all
   remaining accessibility observations explicit rather than claiming a clean
   interface from this one repair.

Chromium captures do not establish Safari or physical phone acceptance. Record
those limitations. Reverting the focused markup and style change restores the
previous focus behavior. No external requests, new data collection, database
change or infrastructure change is needed. Existing diagnostic result limits
continue to bound the amount of content as use grows.
