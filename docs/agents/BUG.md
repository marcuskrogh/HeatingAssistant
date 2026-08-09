# Bug: Large whitespace between Save Current Window inputs on mobile

## Summary
- On the System Identification page, **Save Current Window** shows a large empty vertical gap between **New dataset name** and **Notes** on narrow/mobile screens.
- Wide layouts are fine (fields sit side-by-side).
- Root cause: `.ds-save-row__name` / `__notes` used `flex: 1 1 220px` for desktop row layout; the mobile rule sets `flex-direction: column`, so the **220px flex-basis became height**.

## Repro
1. Open System Identification for a room on a phone / narrow viewport (≤600px).
2. Scroll to **Save Current Window**.

## Expected
- Name, notes, and Save button stack with normal form spacing (~8–12px).

## Actual
- Each stacked field box is ~220px tall; a large empty band appears under the first input before the notes label.

## Impact
- Save form looks broken / hard to scan on mobile; wastes vertical space.

## Suspected area
- `heatingassistant/app/static/css/pages/climate-card.css` — `.ds-save-row` / `@media (max-width: 600px)`.

## Acceptance criteria
- [x] Narrow viewport (≤600px): name, notes, and Save button stack tightly with no large gap.
- [x] Wide viewport: name and notes remain horizontally aligned.
- [x] App package synced via `scripts/sync-ha-app-package.sh`.
- [x] Version bump to **2.0.22** (CSS loaded as `?v=${version}`).

## Out of scope
- Broader identification page layout redesign.
- Container-query stacking for HA sidebar narrow cards when viewport stays wide (row wrap already avoids the height bug).

## Tracker
- Task: SWD-283
- Branch: `cursor/fix-sysid-save-row-mobile-gap-6a4c`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/576

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/576
- Version: **2.0.22**

## Next
Done — rebuild App on HAOS to v2.0.22; confirm Save Current Window stacks tightly on mobile.
