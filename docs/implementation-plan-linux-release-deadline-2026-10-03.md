# Allow the complete Linux release checks to finish

## Review summary

Raise the Pi Linux release job's bounded deadline from 10 to 30 minutes.
The completed frozen Python 3.13 suite alone takes 806.74 seconds locally.
The Linux job also builds a clean image and runs eleven other permission or
packet commands. A 600-second local allowance cannot cover this workload.
This is ARM64 evidence on one CPU, not a measurement of hosted runner speed.

Only the job deadline changes. All commands, privileges, checks, branch rules
and publication gates remain. The extra allowance matches the existing Python
matrix budget and allows reasonable growth without removing the deadline.
The risk is longer CI resource use if a job stalls. Thirty minutes keeps that
cost bounded. No application, network or stored-data behavior changes.

## Implementation and verification

1. Independently review this plan and the single deadline change before editing.
2. Change only the Linux job timeout in wifi-recovery.yml from 10 to 30 minutes.
3. Verify the workflow commands and all other fields are byte-identical.
4. Freeze the reviewed browser repair and deadline change before final checks.
5. Record actual final clean-image build, full-suite and all other Linux command
   durations. Require the whole local job to fit the new 30-minute budget.
6. Execute every configured release check on the final source and keep the
   original failed and incomplete attempts with their original identities.

Reverting the single field restores the previous limit. Hosted checks remain
unavailable when account limits prevent them from starting. No hosted success
or physical Pi acceptance follows from this deadline repair.
