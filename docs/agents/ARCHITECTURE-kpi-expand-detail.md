# Architecture: Expandable KPI detail cards

## Shape
- Lives: Ingress panel UI in `heatingassistant/app/static/` — new host
  `js/components/kpi-expand.js`, copy/rows in `js/kpi-detail-catalog.js`,
  chrome in `css/industrial.css`. Overview and room pages register cards.
  App runtime publishes `last_nmpc_duration_s` on
  `sensor.heating_assistant_mpc_performance` (`runtime_nmpc.py` +
  `runtime_states.py`).
- Depends on: existing `createGauge` / `createCountdown` elements, `kpi-engine.js`
  values, `utils.js` formatters, `hass_states()` attributes. No new HTTP or MQTT
  topics.
- Seams: `expandStateAfterClick({ keys, openKey, clickedKey })` is a pure
  order function tests lock. Duration is a float on the existing performance
  entity. Detail rows are functions of state, not DOM.
- Will not add: a widget framework, modal overlay, persisted expand state,
  new page, or a second KPI visual language.

## Neighbourhood
- Opened modules/boundaries: `.grid-kpi` on Overview (two sections) and room
  view; `HeatingRuntime` NMPC worker wall-clock around `solve_nmpc_blocking`;
  dual-tree App package sync.
- Major refinement: none. Wrap gauges rather than rewrite `gauge.js`. Keep
  collapsed `.card.gauge` / `.card.countdown` markup.

## Tracker
- Task: SWD-469
- Branch: `cursor/swd-469-kpi-expand-e3f0`

## Next
`/ship SWD-469` — Merge and close out
