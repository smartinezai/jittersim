# SSH key rejection test

The release image exposed a test assumption. A directory supplied as an SSH
key in automatic transport mode may deliberately select HTTPS. The test must
select SSH to verify the SSH key rejection before any remote action. No
application or deployment behavior changes.

## Change and checks

- Select SSH explicitly in the directory-key case.
- Require the missing-file diagnostic and no SSH or copy calls.
- Reproduce the old failure in the clean release image without a Git checkout.
- Run the repaired case there and the full deployment test module on the host.
- Run the configured release checks against the final committed source.

## Container skip audit

The release image deliberately omits Git metadata. Its full suite skips
48 deployment cases with `the deploy script builds from a git checkout` and
13 release signing cases with `the scripts build from a git checkout`.
Allow only those exact class and reason pairs plus the 39 kernel cases.
Require all 61 checkout cases to run in the separate Git checkout matrices.
Keep the failing report as evidence. Do not hide unexpected skips or failures.

## Risk and recovery

Risk is low because only a test changes. Packaging, authentication, signing,
network configuration, stored data and runtime code remain unchanged.
The normal source commit rollback restores the previous test.
