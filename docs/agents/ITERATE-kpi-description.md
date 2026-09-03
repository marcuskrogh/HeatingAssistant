# Iterate: Short KPI description on Overview and room cards

## Prior work
- Task: SWD-469
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/652 (`cdd42be`)
- Spec context: docs/agents/PLAN-kpi-expand-detail.md

## Problem
- Expanded KPI cards list absolute values, but the short “what this KPI is”
  sentence is only in the dark panel after expand. It needs to sit on the
  card itself, collapsed and expanded.
- App version is still 2026.08.x in September; the next cut is 2026.09.0.

## Clarifications
- Catalog sentences from SWD-469 stay; they become the always-visible line.

## Pass criteria
- Each Overview and room expandable KPI shows a one-sentence description.
- That sentence is visible collapsed and expanded.
- Gauge/countdown chrome is otherwise unchanged.
- App version is 2026.09.0 with a matching changelog heading.

## Out of scope
- New KPIs, System Status page, changing the 2 s MPC LOAD percent.

## Work packages
1. Always-visible lead line on the expand host; detail panel keeps rows only.
2. CalVer 2026.09.0, changelog, App package sync, tests.

## Tracker
- Task: [SWD-474](https://marcusknielsen.atlassian.net/browse/SWD-474)
- Relates: SWD-469
- Branch: `cursor/swd-474-kpi-description-e3f0`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/653

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/653 (`412eb31`)
