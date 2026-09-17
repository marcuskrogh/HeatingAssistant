# Architecture: Physical wall EKF process noise

## Shape
- One change in `HouseThermalSDE._build_sigma_matrix`: wall-node diffusion is `σ_w · √q_scale · min(C_a/C_w, 1)` per room.
- Air, emitter-filter, and offset blocks keep the previous intensities.
- Live CD-EKF, PE reconstruction, and NMPC all share this SDE, so one matrix fixes room-view Wall.

## Not this Task
- Integrator `n_int_steps`, NMPC sample grid, PE origin stride, Tw clamps.

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/690
