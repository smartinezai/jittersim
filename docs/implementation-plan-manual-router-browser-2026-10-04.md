# Follow manual router checks in Wi-Fi recovery acceptance

The complete804 Python3.13 suite passed5461 tests with40 expected kernel skips.
The Wi-Fi recovery browser then expected the router switch to become usable
automatically. Current containment requires the user to choose Check router
Wi-Fi. Update this journey to assert the initial unknown switch, request the
manual check and verify its first refresh flag. After reloading credentials,
request another manual check before expecting the switch to be usable.

Keep cancellation, held writes, health access, two switch writes, Wi-Fi name
recovery and all desktop/laptop/phone layout checks. No production behavior
changes. Run this journey locally and in the complete renewed Linux sequence.
Review all other browser expectations of this switch and preserve the failed
run as incomplete. There is no live router or Pi contact.

Wait for the held write request after clicking. The busy notice precedes the
request identifier read, so it alone does not prove the write has been sent.
