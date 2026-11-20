# Two-room NMPC solve profile

Profile of why planning got slower after the Office was added. No control law was changed in this document. The one code change that leaves a finished plan untouched is the forward-roll cache: https://github.com/marcuskrogh/HeatingAssistant/pull/720

Ticket: SWD-624, related to SWD-595. Measured on 2026.11.10 (`d7643ff5`) with the real `HeatingMPCController.solve_nmpc` and `solve_mean_ocp`. SciPy SLSQP. No stand-in solver.

## Method

Two plants, built through `ControlEngine` so the November Living Room fit is applied the same way the app applies it.

| | Living Room | Office (second plant only) |
| --- | --- | --- |
| Source | Heat pump, `hvac_mode` heat, 9000 W | Electric heater, 1300 W |
| Power scale | 0.15 (November fit) | 1.0 |
| Thermal mass | 1.329e7 J/K (fit; the config value 9e6 is overwritten) | 2.5e6 J/K |
| `r_external` | 0.00908 K/W (fit) | 0.05 K/W |
| Other fit terms | internal gain 1550 W, solar scale 0.179 | solar scale 1.0 |
| Window | none in the packaged fit | one, 2 m², west (270°), tilt 90° |
| Setpoint / band | 21 °C, ±1.5 °C | 21 °C, ±1.5 °C |
| Start temperature | 19.5 °C | 18.0 °C |

Shared solver settings are the live defaults: horizon 36 h, period 900 s, one fast substep, 10 implicit-Euler steps, `energy_price_weight` 1. Outdoor −2 °C. Latitude 55.676, longitude 12.568, at 2026-11-18 05:30 UTC (06:30 in Copenhagen). The flat-price cases use 0.35. A third case uses a diurnal price (0.12 at night, 0.42 during the day, 1.35 at 07:00–09:00 and 17:00–20:00).

Each reported solve is one call. Cold means `_nmpc_warm` is empty, so the start is zeros. The next two slots call `apply_nmpc_result` and solve again, which is how the worker carries a plan forward. The timeout used for the timing runs was 180 s, plus one two-room cold start at the production default of 60 s.

Setup is the time to construct `ControlEngine`. Pre-solve is everything inside `solve_nmpc` before `solve_mean_ocp` (forecast alignment, solar geometry, comfort bounds, `MeanOcp` construction). The NLP time is `solve_mean_ocp`. `plan['elapsed_s']` also includes `J(0)` and the accept roll that builds `t_ref`. The worker stamps `last_nmpc_duration_s` around `solve_nmpc_blocking` and before `apply_nmpc_result`, so it matches `elapsed_s` within the 1–2 ms of pre-solve. The EKF is not on that path. `solve_nmpc` does not run it.

Repeats: three cold starts of each plant. One cProfile and one pyinstrument of a two-room cold start. Cost and Jacobian micro-benchmarks, three each, at `u = 0` and at `u = 0.4`.

## Where the time goes

Controller construction is 2–5 ms. Pre-solve, including the west-window solar geometry, is 1–2 ms. There is no symbolic model and no CasADi NLP. `MeanOcp` is a NumPy object rebuilt every slot, and that rebuild is inside the 2 ms.

A two-room cold start on this host:

| Phase | Median of 3 | Min | Max |
| --- | ---: | ---: | ---: |
| Pre-solve | 1.6 ms | 1.6 ms | 1.6 ms |
| `solve_mean_ocp` | 9.29 s | 8.77 s | 9.57 s |
| Wall, including `J(0)` and the accept roll | 9.44 s | 8.92 s | 9.72 s |

The same split for the Living Room alone is a wall time of 0.686 s (0.676–0.693 s). The spread across repeats is a few percent. The Office does not add a second of setup.

pyinstrument on one two-room cold start (the profiler stretched a ~9 s solve to 15.1 s; the shares are the point, not the absolute seconds):

- 14.8 s of 15.1 s is `solve_mean_ocp`.
- 6.6 s is the SLSQP cost callback and 7.1 s is the gradient callback.
- Both callbacks spend almost all of that in `implicit_euler_step` → `HouseThermalSDE.f` and `dfdx`.
- The heat-pump sigmoid is a visible part of `f`, not the bulk.
- The sensitivity update (`_refresh_M` and the `M @ sx` products) is about 0.16 s of the 7 s gradient.

cProfile agrees: 158 400 implicit-Euler steps for 110 rolls (53 gradients and 56 costs, 144 slow steps, 10 substeps). `numpy.linalg.solve` on the 3×3 Newton step is real time, and it is still inside the plant, not a separate KKT linear solver.

One evaluation costs the same with or without the Office:

| | Cost, median of 3 | Gradient at 0 | Gradient at 0.4 |
| --- | ---: | ---: | ---: |
| Living Room | 73 ms | 83 ms | 83 ms |
| Two rooms | 71 ms | 81 ms | 80 ms |

So the slowdown is not a more expensive model evaluation.

## Problem size and solver options

| | Living Room | Two rooms |
| --- | ---: | ---: |
| Air states | 1 | 2 |
| Emitter filters | 1 (heat pump, τ = 60 s) | 1 (the heater τ is 0, so it adds none) |
| `nx` | 2 | 3 |
| `nu` | 1 | 2 |
| Slow steps `N` | 144 | 144 |
| Decisions | 144 | 288 |
| Box bounds | 144 | 288 |
| Euler substeps per slot | 10 | 10 |

There are no integer variables and no complementarity constraints. The heat pump is a smooth sigmoid. The NLP passes box bounds only.

SLSQP options, from `solve_mean_ocp`: `ftol` 1e-6, `maxiter` 200, `disp` false. There is no linear-solver option. `DEFAULT_MPC_SOLVER = "qp"` is the linear planner, not this NLP. The cost seen by SLSQP is divided by `|J|` when `|J| > 1`, so the large comfort penalty does not pin the search at zero. Accept still uses the unscaled cost.

Every solve, including a warm one, takes one projected Cauchy step from the start when that step lowers `J`. A zero start that is still near zero after SLSQP gets one extra mid-bound probe. Neither of those is a reused plan.

## Why two rooms take longer

Per evaluation does not grow. Iterations do.

| Case | Status | Iterations | Evaluations | Gradients | `elapsed_s` | `J` | `J(0)` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Living Room, cold (median of 3) | success | 2 | 7 | 3 | 0.69 | 28.01 | 1.48e6 |
| Two rooms, cold (median of 3) | success | 52 | 108 | 53 | 9.3 | 2516 | 1.38e7 |
| Two rooms, cold, cap 60 s | success | 52 | 108 | 53 | 9.1 | 2516 | 1.38e7 |

About 15× more evaluations, at the same ~80 ms each, is the whole gap between 0.7 s and 9 s. Time per iteration stays near 0.18–0.23 s.

The Office time constant `C·R` is 1.25e5 s. The fitted Living Room is 1.21e5 s. The same per-evaluation time means the 2.5e6 J/K mass against a 1300 W heater is not making the Newton loop diverge or take extra substeps. The extra iterations are SLSQP on a second input whose comfort cost the opening Cauchy step does not finish. The west window is inside the 2 ms pre-solve; it does not show up in the plant time.

## Warm start, and startup versus a later slot

After an accepted plan, `apply_nmpc_result` stores `shift_slow_plan(U)` (drop the first slot, repeat the tail). The next `solve_nmpc` uses that vector when its length matches. Startup is the zero vector. The previous plan is reused. It is not reused across rooms as a separate mechanism: each source is a column of the same `U`.

On a flat price the reuse helps the two-room plant and hurts the one-room plant:

| Case | Warm `|u|` max on entry | Iterations | Wall (s) |
| --- | ---: | ---: | ---: |
| Living Room, slot 2 | 1.0 | 115 | 23.9 |
| Living Room, slot 3 | 0.83 | 44 | 11.2 |
| Two rooms, slot 2 | 1.0 | 5 | 1.30 |
| Two rooms, slot 3 | 1.0 | 6 | 1.36 |

The one-room cold start is fast because the Cauchy step lands at `J = 28.01`. The shifted plan then spends 115 iterations to reach `J = 25.94`. That is a finished success, not a timeout.

A diurnal price reverses the two-room warm start. Cold stays 52 iterations and 8.7 s. The next two slots take 125 iterations (23.6 s) and 87 iterations (14.5 s). The shifted plan is a poor start once the price shape has to be tracked, and SLSQP is allowed 200 iterations to keep polishing.

That is the startup-versus-06:30 pattern. A cold start is not systematically the slow one, and a warm start is not systematically the fast one. Live 43 s at startup and 68 s at 06:30 fits a warm plan that is off the new morning price, not a bigger NLP.

## What the live times correspond to

On this host a converged two-room solve is about 9 s (52 iterations) or 1.3–24 s once a previous plan is reused. Live numbers of 28 s (one room) and 62 / 162 / 127 s, then 43 s and 68 s, are the same structure with more iterations, a slower host, or both.

`maxiter` 200 at ~0.18 s/iteration is about 36 s on this host and on the order of three minutes on a host five times slower. The 162 s outlier sits in that range. The code default `nmpc_max_compute_s` is 60 s. A single roll is checked at entry, so a solve can run past the cap by one evaluation (~0.1 s), not by a minute. Recorded solves of 127 s and 162 s are therefore not the default 60 s cap truncating one evaluation. The installed cap is higher than the default. The 60 s run in this profile still converged, in 9.1 s, because this particular case finishes at iteration 52.

## Ranked changes

Gains below are for this host. A slower host scales them. "Plan unchanged" means a finished solve returns the same `U`. A solve stopped by the wall-clock cap is called out where the timing change can give SLSQP extra iterations.

1. **Cache the forward roll. Plan unchanged on a finished solve. Expected about 1.5–2×.** SLSQP evaluates `J(u)` and then `∇J(u)` at the same `u`, and each one integrates the plant. Remembering the roll removes the second integration. The sensitivity math still runs. Measured on the real solver, nine solves (cold plus two later slots, one room and two rooms, flat price and diurnal price): every accepted `U` was bitwise identical, and the held command fractions were identical. Iteration counts did not change.

   | Case | Before (s) | After (s) | Iterations |
   | --- | ---: | ---: | ---: |
   | Living Room, cold | 0.65 | 0.33 | 2 |
   | Living Room, slot 2 | 23.86 | 14.39 | 115 |
   | Living Room, slot 3 | 11.20 | 7.55 | 44 |
   | Two rooms, cold | 8.65 | 4.51 | 52 |
   | Two rooms, slot 2 | 1.30 | 0.67 | 5 |
   | Two rooms, slot 3 | 1.36 | 0.68 | 6 |
   | Two rooms, diurnal price, cold | 8.70 | 5.16 | 52 |
   | Two rooms, diurnal price, slot 2 | 23.57 | 13.69 | 125 |
   | Two rooms, diurnal price, slot 3 | 14.51 | 9.06 | 87 |

   That cache is https://github.com/marcuskrogh/HeatingAssistant/pull/720. Residual risk: the deadline is checked at the start of an evaluation. A gradient that used to be aborted in the middle now finishes, so a solve that is currently cut by `nmpc_max_compute_s` can take iterations it did not take before. Finished solves do not move.

2. **Fewer Euler substeps. Can change the plan. Marcus decides.** Ten substeps per 900 s slot are most of the 158 400 Newton steps. Cutting them scales the plant time down with the step count. The trajectory changes, so `U` can change. Not shipped.

3. **Stop the long polish. Can change the plan. Marcus decides.** `ftol` 1e-6 and `maxiter` 200 let a one-room warm start spend 115 iterations to move `J` from 28.01 to 25.94, and let a priced two-room warm start run to 125 iterations. A looser tolerance or a lower cap would cut those tails. The accepted `U` would move. The live 162 s case is this tail on a slower host, if it is not the cap.

4. **Shorter horizon. Can change the plan. Marcus decides.** 36 h is 144 decisions. Half the horizon halves every roll and shrinks the decision vector. The plan covers less of the price day. Not shipped.

5. **A different warm start. Can change the plan. Marcus decides.** The shifted plan is already reused. On a flat price it cuts the two-room solve from 52 iterations to 5. On a diurnal price it raises the next slot from 52 to 125. Changing the shift, skipping it, or skipping the Cauchy step would change some plans and is not a uniform speedup.

6. **Do not chase a faster linear solver or a cached symbolic NLP.** SLSQP is not spending time in a KKT factorization, and there is no symbolic NLP to cache. Swapping the solver would be a new planner and needs the same `U` check as any other plan-changing idea. Rescaling the Office mass would change the model. The profile does not show a conditioning problem in the integrator.
