# Iterate: Keep App calendar version in September

## Prior work
- Task: SWD-576
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/695 (`b3e1a5a4`)
- Spec context: docs/agents/PLAN-pe-fit-results.md

## Problem
- The App calendar version jumped to 2026.10.1 while the calendar month is still
  September.
- YYYY.MM must be the current calendar month. PATCH may continue in that month
  (2026.09.31 is valid; 2026.10.1 is not until October).

## Clarifications
- Dual tree: edit sources, then `scripts/sync-ha-app-package.sh`.
- Changelog heading must match the live version. Identification Results copy stays.

## Pass criteria
- Live lock is `2026.09.31` across package, App config, Dockerfile, changelog heading, and tests.
- YYYY.MM of the live version matches the current UTC calendar year and month.
- PATCH stays unpadded (`31` is valid; `01` is not).
- Identification Results behaviour is unchanged.

## Out of scope
- Identification Results persist, Load, Delete, or auto-apply.
- Jumping the month to October before October.

## Work packages
1. Relock CalVer to September and sync the App tree.

## Tracker
- Task: SWD-580
- Relates: SWD-576
- Branch: `cursor/swd-580-calver-september-6f70`

## Next
`/test SWD-580` — Dedicated testing phase, then harden and code review
