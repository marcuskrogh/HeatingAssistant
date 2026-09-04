# Model: Receding N-step PEM with EKF Jacobians

## Problem statement

Identify structural 2R2C parameters \(\theta\) so the **N-step open-loop air
trajectory** from a **CD-EKF state** matches measurements on the same discrete
grid the NMPC uses. \(N\) and \(dt\) are the configured look-ahead and fast
step, not the dataset length.

## Notation

| Symbol | Meaning |
|--------|---------|
| \(dt\) | NMPC fast step \(T_s = T_{\mathrm{period}}/M\) |
| \(M\) | `nmpc_fast_substeps` (origin stride in fast steps) |
| \(N\) | `n_fast` look-ahead steps (`nmpc_horizon_h` / \(dt\)) |
| \(o \in \mathcal{O}\) | Slow-grid origins (every \(M\) fast steps, and dataset starts) |
| \(x_k,\ P_k\) | EKF mean and covariance at fast index \(k\) |
| \(\hat y_{o+j\mid o}\) | Open-loop air prediction \(j\) steps from \(x_o\) (no Kalman updates) |
| \(y^m\) | Measured indoor air |
| \(R\) | Measurement variance (same \(R_{\mathrm{var}}\) as the EKF) |
| \(T_w(t_0^{(i)})\) | Wall initial on stored-dataset start \(i\) (PE decision) |

## Formulation

**Grid.** Live NMPC triple \((T_{\mathrm{period}}, M, H)\) defines \(dt\) and
\(N\). If a history is shorter than \(N\), score the remaining length.

**Recursion.** At dataset start \(i\): air from \(y^m\), wall from
\(\theta\)'s \(T_w(t_0^{(i)})\), \(P_0 \propto Q\). Then for each fast step:

1. If \(k \in \mathcal{O}\) and enough future samples exist, **open-loop**
   implicit-Euler simulate \(N\) steps from \((x_k, \partial x_k/\partial\theta)\)
   with recorded \(u,d\). Add Gaussian path misfit
   \[
   \ell_o = \frac{1}{2R}\sum_{j=1}^{N}
   \bigl\| y^m_{o+j} - \hat y_{o+j\mid o} \bigr\|^2.
   \]
2. **CD-EKF** predict (same implicit Euler as the live filter / NMPC) and
   measurement update. Propagate \(\partial x/\partial\theta\) and
   \(\partial P/\partial\theta\) through predict and Kalman correction.

**NLP.** Minimise \(\sum_o \ell_o +\) existing MAP regularisation (L-BFGS-B).
Overlapping origins \(\Rightarrow\) quasi-MLE (correlated path errors).

**Timeout.** Wall-clock cap is not part of \(J\); it aborts the solve.

## Assumptions

- 2R2C, air-only measurements, contact-UA and open-window pinning as in current OE.
- Path noise is isotropic \(R\) (shared with the EKF). No extra path-scale parameter.
- Joint multi-dataset: new \(T_w(t_0)\) and EKF re-init at each dataset start;
  timestamp gaps still split segments.

## Algorithmic choices

- Do **not** use one-step innovation PED as \(J\).
- Do **not** tile independent 12 h OE windows for the joint fit (kept only as a
  comparative baseline flag and for wall-only diagnostic fits).
- Origins on the slow period; EKF on every fast step.

## Numerical considerations

- Implicit Euler sub-steps as in `_simulation_mse_and_grad` (\(\le 300\,\mathrm{s}\)).
- Sentinel \(10^{10}\) on non-finite trajectories.
- Deadline checked each fast step / origin.

## Open items

- None remaining for implement (path \(R\) shared with EKF).

## Role in pipeline

Finding docs for implement. Not product UX.

## Tracker

- Task: SWD-481
- Artifact: `docs/agents/MODEL-pe-nstep-mle.md`
- Branch: `cursor/swd-481-pe-nstep-mle-dfe4`
- PR: — (model never opens a PR)

## Next

`/architect SWD-481` — Record module shape for N-step PEM + Advanced config
