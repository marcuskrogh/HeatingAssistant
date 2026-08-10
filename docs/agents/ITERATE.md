# Iterate: SWD-300 review-fix — module health, XSS, API-error indicator

## Prior work
- Task: SWD-300
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/595 (v2.0.32)
- Spec context: docs/agents/PLAN-system-status.md
- Review: post-merge findings (should-fix) from PR #595 review

## Problem
Shipped System Status slice is incomplete vs acceptance and has a few wiring/security gaps:
1. Backend `system_health.modules` are not exposed on `system_summary` / rendered on the page (AC2 module health).
2. Dynamic strings (`issue_summary`, etc.) are interpolated into `innerHTML` unescaped.
3. Ingress API poll failure re-shows the floating pill and does not force ERROR on the health indicator.
4. No harness covering System Status sections or HEALTHY/WARNING/ERROR indicator wiring.
5. Minor: duplicate `system-status.js` stubs in lifecycle/watchdog harnesses; health-dot CSS lives only in page stylesheet.

## Acceptance criteria
1. System Status page shows a MODULES (or equivalent) section driven by backend module health rows.
2. Dynamic System Status text is HTML-escaped (or set via `textContent`).
3. On API refresh failure: floating pill stays hidden; top indicator shows ERROR (client override until recovery).
4. Panel harness (or equivalent test) covers System Status paint + health indicator classes/labels.
5. Duplicate harness stubs removed; `.live-dot--*` / `.live-label--*` health styles live in `industrial.css`.
6. Version bump to **2.0.33** + App package sync.

## Out of scope
- Catalog empty → WARNING grace period redesign.
- Renaming “Parameter Estimation Parameters” section title.
- Dropping legacy `#identification` hash migration.

## Work packages
1. Publish `modules` on `system_summary` + render MODULES on System Status; escape dynamic HTML.
2. API failure → ERROR indicator; keep pill hidden.
3. Harness + CSS move + stub dedupe; version **2.0.33**.

## Tracker
- Task: SWD-306
- Relates: SWD-300
- Branch: `cursor/swd-306-system-status-review-fix-c2e7`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/596
- Next: `/review-fix SWD-306`
