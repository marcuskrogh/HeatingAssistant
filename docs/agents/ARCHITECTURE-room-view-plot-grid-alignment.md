# Architecture: Align room view plot grids

## Shape
- Lives: Ingress Chart.js wrapper `heatingassistant/app/static/js/components/` —
  new `chart-align.js` (pure plot-box + time-domain math and the Chart.js
  plugin) used by `time-series-chart.js`. Room view opts in from
  `pages/room-detail.js` via `alignGroup`.
- Depends on: existing Chart.js instance on `TimeSeriesChart` (layout padding
  and `scales.x`). No new HTTP, MQTT, or chart library.
- Seams: `unionPlotBox`, `extraPaddingToMatch`, `naturalPlotArea`, and
  `sharedTimeDomain` are pure. Tests feed fake `{left,right,width}` / point
  lists without booting HA. The plugin only reads `chartArea` / datasets.
- Will not add: a second chart library, a combined mega-chart (unless the
  helper cannot meet the bar), or a new layout framework.

## Neighbourhood
- Opened modules/boundaries: room-view stacked `TimeSeriesChart` cards;
  Chart.js `nowLine` already maps x through `scales.x`.
- Major refinement: none — keep three cards; share plot box and time domain.

## Tracker
- Task: SWD-551
- Branch: cursor/swd-551-room-plot-grid-4b29

## Next
`/implement SWD-551` — Build to this shape
