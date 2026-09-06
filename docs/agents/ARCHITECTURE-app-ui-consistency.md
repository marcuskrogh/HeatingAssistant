# Architecture: App UI consistency

## Shape
- Lives: Ingress design layer — `heatingassistant/app/static/css/industrial.css`
  (`:host` tokens), page CSS under `css/pages/`, Chart.js wrapper
  `js/components/time-series-chart.js`, PE overlay `js/identification/pe-progress.js`,
  chart height callers in room-detail / tuning / sysid-detail.
- Depends on: existing colour tokens on `:host`; Chart.js already loaded by
  `TimeSeriesChart`.
- Seams: CSS custom properties consumed by CSS and (via `getComputedStyle`) by
  canvas drawing; `chart-theme.js` exports room-guide numeric constants
  (`CHART_TICK_SIZE` 10, `CHART_LINE_WIDTH` 2) and `sizePlotCanvas` so PE
  copies room CSS-pixel strokes without changing Chart.js defaults.
- Will not add: a CSS-in-JS runtime, a second chart library, new typefaces,
  or a parallel token file that pages could skip.

## Neighbourhood
- Opened modules: industrial panel shadow CSS, Identification PE popup,
  room/tuning/sysid plots.
- Major refinement: one chart theme module so canvas plots and Chart.js
  cannot drift; page CSS must reference tokens, not raw px for type.

## Tracker
- Task: SWD-498
- Branch: cursor/swd-498-app-ui-consistency-dcd6
- Next: `/implement SWD-498`
