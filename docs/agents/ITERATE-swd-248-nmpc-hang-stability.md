# Iterate: Stop NMPC hang — executor, timeout, SciPy horizon gate

## Prior work
- Task: SWD-247
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/541
- Spec context: `docs/agents/ITERATE-swd-247-ipopt-scipy-fallback.md`

## Problem
- After enabling non-linear MPC (especially SciPy fallback), Home Assistant appears to hang when computation starts.
- Root cause: coordinator MPC `compute()` runs synchronously on the asyncio event loop; default horizon N=100 makes SciPy SLSQP simultaneous-transcription NMPC pathological (minutes / never finishes).
- NLP `success=False` is ignored by mbc OCP, so SWD-244 failure notification never fires for soft solver failures.

## Clarifications
- Realistic goal: keep HA responsive and bound worst-case NMPC cost — not make SciPy as fast as Ipopt at N=100.
- Prefer executor + wall-clock wait, SciPy horizon cap, tighter solver caps, and surface failures via existing NMPC notification.

## Acceptance criteria
- Main MPC compute runs off the HA event loop (executor).
- Nonlinear path has a wall-clock timeout; on timeout/failure: keep last controls when present, clear forecasts, raise NMPC failure notification.
- SciPy backend: lower maxiter + horizon cap (auto-cap on build with log) so production sizes stay bounded.
- NLP `success=False` raises into the mid-cycle failure path.
- Tests cover executor/timeout wiring, SciPy horizon cap, and guarded NLP failure.

## Out of scope
- CasADi / condensed NMPC rewrite
- User-facing solver picker
- Matching Ipopt speed at default N=100 on SciPy

## Work packages
1. Executor + timeout around coordinator MPC compute.
2. SciPy horizon cap + tighter NLP options + success guard.
3. Tests.

## Tracker
- Task: SWD-248
- Relates: SWD-247

## Next
`/review-fix SWD-248` — Review and auto-fix until clean
