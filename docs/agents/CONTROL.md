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
each sample (default 900 s):
  1. read y = room air temperatures
  2. CD-EKF predict + update  ->  x_hat
  3. solve planner on that grid (NMPC NLP or Linear QP)
  4. apply u = U*[k]          # receding horizon, clip to source bounds
```

If a solve is still running, the last accepted `U*` is held. Consecutive
rejects can trip the watchdog to `u = 0`.

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
`x = [Ta, phi, b]`. There is no hidden wall state and no P tracker.

## Pointers

| Live | Historical (do not treat as current) |
|------|----------------------------------------|
| `docs/TUNING.md` planner table | `docs/ROADMAP.md` two-rate destination |
| `docs/agents/ITERATE-nmpc-equiv-lmpc.md` (SWD-532) | `docs/agents/MODEL-nmpc-p-ff.md` |
| `heatingassistant/engine/controller/facade.py` (`_p_command_vector` holds `U*`) | `docs/agents/PLAN-nmpc-p-ff.md` |
