# Architecture: 1R1C live plant for control

## Shape
- Lives: `heatingassistant/engine/thermal_model.py` (`Room`, `HouseModel`)
  and `heatingassistant/engine/controller/sde.py` (`HouseThermalSDE`).
  Same types; state dimension `n` instead of `2n`.
- Depends on: existing `HeatSource` maps, integrator, PE `KalmanMLEstimator`
  theta packing, NMPC `MeanOcp` roll, App chart datasets.
- Seams: `HouseModel.step` / `_build_matrices`; SDE `f`, `dfdx`, `hm`,
  `sigma`; PE theta layout; room chart dataset labels `Wall` /
  `Wall Forecast`.
- Will not add: a second plant, a wall observer module, a new filter
  package, or a compatibility 2R2C simulator in production.

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
`/implement SWD-570` — Build to this shape
