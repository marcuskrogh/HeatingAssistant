# Bug: Solar gain plot stuck at zero despite High exposure

## Summary
- Room DISTURBANCES Solar Gain / Solar Gain Forecast stay flat at 0 kW even when solar exposure is **High** (Option A) and facing is set.
- Outdoor and price forecasts already show dynamics (post SWD-278/279); only solar stays constant zero.
- Root cause: App `_build_house_model` built `Room` without mapping `solar_exposure` → `solar_exposure_aperture` or passing `solar_facing`, so aperture stayed 0 and geometric solar was always 0.

## Repro
1. Configure a room with Solar gain → Option A: Solar exposure **High**, Facing direction **270**, no individual windows.
2. Ensure weather/price wired so outdoor & price forecasts move.
3. Open the room view DISTURBANCES plot (daytime / multi-day horizon).

## Expected
- Solar Gain (measured) and Solar Gain Forecast show diurnal dynamics (non-zero daytime, near-zero overnight) consistent with High exposure.

## Actual
- Both solar series sit at constant 0.0 kW across the horizon.

## Impact
- Solar disturbance invisible on plots; MPC treats rooms as having no solar gain despite configured exposure.

## Suspected area
- `heatingassistant/engine/control_loop.py` → `_build_house_model`: omitted `solar_exposure` / `SOLAR_EXPOSURE_TO_APERTURE` and `solar_facing`.
- Secondary: `estimation/model_build.py` `_build_rooms_from_theta` dropped aperture/facing when rebuilding rooms after ID.

## Acceptance criteria
- [x] With Option A High (no windows), `/api/forecasts` room steps include non-zero varying `solar_gain` over a daytime horizon.
- [x] DISTURBANCES plot path receives non-zero solar from MPC snapshot (regression tests).
- [x] `_build_house_model` maps `solar_exposure`/`solar_facing` onto `Room.solar_exposure_aperture` / `Room.solar_facing`.
- [x] ID rebuild preserves aperture/facing.
- [x] Version bump to **2.0.21**.

## Out of scope
- Requiring per-window geometry when Option A is set.
- Re-adding Environment solar-irradiance entity picker (SWD-271).
- History persistence (SWD-281).

## Tracker
- Task: SWD-282
- Relates: SWD-278, SWD-279
- Branch: `cursor/swd-282-solar-exposure-aperture-3296`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/574
- Merge: `38846fb`

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/574
- Merge: `38846fb`
- Version: **2.0.21**

## Next
Done — rebuild App on HAOS to v2.0.21; confirm DISTURBANCES Solar Gain shows daytime dynamics with High exposure.
