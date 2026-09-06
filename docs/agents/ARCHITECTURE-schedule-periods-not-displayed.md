# Architecture: Schedule periods counted but not shown

## Shape
- Lives: `heatingassistant/app/static/js/schedules/schedules-detail.js` (`ensureWhenState` next to other period-editor locals); `heatingassistant/app/static/css/pages/schedules.css` (`[hidden]` on `.sched-detail__section-header`).
- Depends on: existing `normalizePeriodForEditor` in `schedule-utils.js`.
- Seams: Node harness scanning the detail module and CSS (same pattern as SWD-287 expanded-editor lock).
- Will not add: new modules, DOM libraries, or a second period-state type.

## Neighbourhood
- Opened modules: schedule detail page + schedules CSS. Markup stays in `schedules-detail-markup.js`.
- Major refinement: none — restore the helper the extract dropped.

## Tracker
- Task: SWD-491
- Branch: cursor/swd-491-schedule-periods-not-displayed-2822

## Next
`/implement SWD-491` — Build to this shape
