# Architecture: Low-pass solar gain

## Shape
- Lives: `heatingassistant/engine/solar_model.py` (pure EMA + schedule walk);
  `heatingassistant/engine/controller/facade.py` (`_forecast_solar`, persist
  k = 0 on `compute` / `solve_nmpc`); `heatingassistant/engine/const.py`
  (`SOLAR_GAIN_SMOOTHING_TAU_S`)
- Depends on: controller `dt`, per-step `_room_gain` intensity (inward)
- Seams: `smooth_solar_gain_step` / `smooth_solar_gain_schedule` (no HA);
  `_forecast_solar(..., persist=False)` for tests
- Will not add: new RC solar state, config UI, weather-layer cloud EMA
  wiring, extra package

## Neighbourhood
- Opened: solar disturbance schedule in the controller facade (same
  neighbourhood as SWD-462 k = 0 cloud and SWD-432 GHI holes)
- Major refinement: none — reuse the existing first-order discrete filter
  already documented for emitters and `smooth_cloud_cover_step`

## Tracker
- Task: SWD-487
- Branch: cursor/swd-487-solar-gain-lpf-51da

## Next
`/implement SWD-487` — Build to this shape
