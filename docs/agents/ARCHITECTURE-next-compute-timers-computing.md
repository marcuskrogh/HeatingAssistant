# Architecture: Next Compute timers show computing

## Shape
- Lives: Ingress panel countdown widget
  (`heatingassistant/app/static/js/components/countdown.js`) plus
  `industrial.css`; Overview / room pages keep calling `setCountdownComputing`.
- Depends on: existing `sensor.heating_assistant_mpc_performance` attributes
  (`nmpc_computing`, `control_computing`, `last_nmpc_ts`, `dt_s` /
  `nmpc_period_s`, `nmpc_result_ts`, `last_control_ran_ts`).
- Seams: exported `countdownIsComputing(state, spec, nowMs)` for a node
  harness; `setCountdownComputing` also toggles the `.kpi-expand` host.
- Will not add: new HTTP/WS endpoint, new runtime attributes, new layer,
  overlay on gauges.

## Neighbourhood
- Opened modules/boundaries: countdown component, kpi-expand wrap CSS,
  Overview / room tick loops (already 1 Hz).
- Major refinement: none. Decision function stays next to remaining-time
  math rather than a new store.

## Tracker
- Task: SWD-494
- Branch: cursor/next-compute-timers-computing-febc

## Next
`/implement SWD-494` — Build overlay per PLAN.md (same branch/PR)
