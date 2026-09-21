# Architecture: 1R1C live plant for control

## Shape
- Lives: `heatingassistant/engine/thermal_model.py` (`Room`, `HouseModel`)
  and `heatingassistant/engine/controller/sde.py` (`HouseThermalSDE`).
  Same types; state dimension `n` instead of `2n`.
- Depends on: existing `HeatSource` maps, integrator, PE `KalmanMLEstimator`
  theta packing, NMPC/Linear receding-horizon apply of `U*[k]`, App charts.
  Not a P tracker (`refresh_p_command` only holds `U*`).
- Seams: `HouseModel.step` / `_build_matrices`; SDE `f`, `dfdx`, `hm`,
  `sigma`; PE theta layout; room chart dataset labels `Wall` /
  `Wall Forecast`.
- Will not add: a second plant, a wall observer, a new filter package, a
  2R2C simulator in production, or a restored P-layer.

## Neighbourhood
- Opened: thermal model, SDE, EKF facade, NMPC roll, PE, room plots,
  dual tree `heating_assistant/heatingassistant/`.
- Major refinement: collapse the wall block rather than add a
  reconstruction layer. Inter-room `R_ij` moves from wall–wall to
  air–air on the same `RoomConnection` objects.

## Tracker
- Task: SWD-570
- Branch: `cursor/constrained-cdkf-wall-5de1`

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/691
