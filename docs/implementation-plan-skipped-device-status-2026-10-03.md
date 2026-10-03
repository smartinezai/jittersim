# Skipped saved-device status repair

## Review summary

Correct a confirmed misleading Start message. When three saved devices cannot
be identified and none is applied, the actual served JavaScript reports that
no device profiles are saved. Preserve safe exclusion of uncertain devices.
Change only dashboard status wording and its existing input mapping. Show the
count of skipped saved devices and direct the owner to reconnect them and check
their saved profiles. Keep actual applied counts separate from saved counts.

Risk is low. No network mutation, storage change, endpoint, credential rule or
dependency changes. Existing backend warnings remain available. Unknown optional
response fields retain compatibility with older servers. Independently review
the saved plan before changing source under the current takeover authority.

## Evidence and decisions

Source is root combined 936fb454d3a93be53ac12954e24cca5d930dc505. The controlled
consumer reproduction is /private/tmp/jittersim-skipped-start-reproduction.json.
It executes actual served appliedMessage with zero applied devices, one
protected device and three unverified saved IDs. Its response wrongly says:
"Started. 1 protected device stays untouched. No device profiles or service
lists are saved yet."

The frontend currently drops unverified_device_ids before startedText. The saved
summary filters unverified device rows before counting them. Forward their count
to the status helpers. When a Start skipped saved devices, describe those skips
instead of saying no profiles were saved. Retain the true zero-saved case.
The saved summary must distinguish eligible and skipped devices without claiming
the skipped profiles were applied. Preserve general-profile precedence, protected
devices, service lists and access-rule wording.

## Implementation and verification

1. Add failing consumer and saved-summary regressions using actual served
   JavaScript. Cover all-skipped, mixed eligible/skipped, protected clients,
   general mode, absent fields and the truly empty configuration.
2. Make bounded count forwarding and plain status wording changes. Preserve
   the existing warning details and avoid another network read.
3. Run existing apply-summary and active-profile checks plus configured lint.
   Existing browser and installed workflow checks cover the final combined page.
4. Independently review the complete patch and exact source before committing.
   Root owns these status-function blocks. Other chats own their agreed blocks.
5. Record the original failure and passing results with the exact source.
   Final combined release gates remain unchanged.

Rollback is a code revert. Runtime work stays linear in the already bounded
saved device list. The status counts reveal no information beyond authenticated
data already supplied to the page. This repair does not prove live phone or Pi
acceptance and does not authorize deployment before management recovery.
