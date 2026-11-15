# Current control loop

**This is the live control architecture.** Read this before
`docs/ROADMAP.md`, `docs/agents/MODEL-nmpc-p-ff.md`, or any SWD-392–395
artifact. Those describe a **removed** two-layer NMPC + P tracker
(shipped, then taken out). Do not use them as the current spec.

`docs/` root files other than `agents/` are gitignored except when
already tracked; keep this pointer here so pipeline skills can find it.

## What runs each sample

Default planner is Nonlinear MPC (`mpc_mode = nmpc`). Linear MPC is the
other exclusive mode. Both use **one sample grid** (period = `dt`,
`nmpc_fast_substeps = 1`). There is no inner P/PID loop.

```text
each sample (default 900 s), once:
  1. CD-EKF predict over the real elapsed time since the last update, then update
  2. one planner solve (NMPC NLP or Linear QP)
  3. one actuator write of u = U*[k] for that plan (the new plan's first step)
```

Room-temperature tags between samples update the estimator with the real
elapsed time. They do not solve and they do not write. Price tags and other
sensor tags only store the new value. Saving configuration or a room schedule
runs one immediate cycle: the estimator steps by the real elapsed time since
its last update, then there is one planner solve and one actuator write.
A sample does not write the previous plan's next step while the solve is
still running: the last accepted command stays in place until the one write
for that sample. Consecutive rejects can trip the watchdog to `u = 0`, and
that zeroing is one write.

Leftover names in code (`refresh_p_command`, `_p_command_vector`,
`u_ref_gate`) are API leftovers. They **hold `U*`**, they do not run
`u_ref + Kp*(T_ref - Ta)`.

## What this is not

```text
# removed — do not plan against this
slow NMPC -> (T_ref, u_ref)
fast P:    u = clip(u_ref + Kp * (T_ref - Ta_hat))
```

That two-rate tracker was the SWD-392 route. Inner P was removed;
Nonlinear was then put on the same sample grid as Linear (SWD-532).

## Plant

Live thermal plant is **1R1C** (one air node per room) plus CD-EKF on
`x = [Ta, phi]` with `augment_offsets=False`. There is no integrating
disturbance state and no bias state. There is no hidden wall state and no P tracker.

## Identified parameters

The estimator and the planner copy capacitance, drift, and solar gain from
the house model when they are built, and again whenever stored parameters
are restored. The estimator's temperature and covariance stay on the same
filter. A config save still rebuilds the controller after that restore, so
the copied matrices match the restored model.

The Living Room fit from 8–12 Nov 2026 is in
`heatingassistant/engine/data/living_room_estimated_params.json`. If the
active snapshot is still the 26 Sep set (about 277 MJ/K, 284 W/K, solar
scale 0.25), startup replaces it with that fit and keeps the 26 Sep entry
at the front of parameter history. `revert_parameters(..., history_index=0)`
promotes the 26 Sep entry back to active.

The fit's heat loss is 110.12 W/K. Capacity is 1.329e7 J/K, solar scale is
0.179, and the heat pump power scale is 0.15. The pump's minimum output is
`internal_gain = 1550` W. `min_power` on the heat pump does not change
thermal output. The split between that constant and the heat per degree of
offset is the uncertain part. The heat-loss coefficient is the reliable one.

## Pointers

| Live | Historical (do not treat as current) |
|------|----------------------------------------|
| `docs/TUNING.md` planner table | `docs/ROADMAP.md` two-rate destination |
| `docs/agents/ITERATE-nmpc-equiv-lmpc.md` (SWD-532) | `docs/agents/MODEL-nmpc-p-ff.md` |
| `heatingassistant/engine/controller/facade.py` (`_p_command_vector` holds `U*`) | `docs/agents/PLAN-nmpc-p-ff.md` |
