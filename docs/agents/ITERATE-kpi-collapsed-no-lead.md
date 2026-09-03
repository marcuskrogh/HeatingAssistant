# Iterate: Hide KPI description on collapsed cards

## Prior work
- Task: SWD-475
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/654 (`00f1cc1`)
- Spec context: docs/agents/SANDBOX-kpi-expand-motion.md

## Problem
- Collapsed KPI cards show a one-sentence description under the gauge.
- The collapsed card should look as it did before that sentence existed:
  label, value, and bar only.
- Keep the current expand mechanic unchanged.

## Clarifications
- Description stays in the open inset under the Description heading.

## Pass criteria
- Collapsed Overview and room KPI cards have no description sentence.
- Open cards still show Description in the inset plus value rows.
- Expand motion, viewport follow, and NMPC / Regulator load cards stay as
  shipped in SWD-475.

## Out of scope
- NMPC / Regulator load formulas.
- System Status page.

## Work packages
1. Remove the collapsed lead from the expand host; keep inset Description.
2. CalVer, changelog, App package sync, tests.

## Tracker
- Task: [SWD-476](https://marcusknielsen.atlassian.net/browse/SWD-476)
- Relates: [SWD-475](https://marcusknielsen.atlassian.net/browse/SWD-475)
- Branch: `cursor/swd-476-kpi-collapsed-no-lead-e3f0`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/655

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/655
