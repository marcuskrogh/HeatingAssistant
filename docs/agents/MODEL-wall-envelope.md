# Model: Physical envelope for the 2R2C wall node

## Problem statement
The wall/mass temperature \(T_{w,i}\) is not measured.  The CD-EKF reconstructs it from air measurements.  Equal process noise on air and wall, plus a wide \(T_w(t_0) \in [-30,60]\) box in PE, lets air innovations and \(q_{\mathrm{int}}/R\) degeneracy dump into \(T_w\).  Observed failure: overnight \(\hat T_w\) falls from \(\sim 20^\circ\mathrm{C}\) to \(\sim 6^\circ\mathrm{C}\) while \(T_{\mathrm{out}} > 10^\circ\mathrm{C}\) and \(T_a \approx 22^\circ\mathrm{C}\).

## Notation
| Symbol | Meaning |
|--------|---------|
| \(T_{a,i}, T_{w,i}\) | Air and wall nodes of room \(i\) |
| \(T_{\mathrm{out}}\) | Outdoor air |
| \(\delta_{\mathrm{lo}}=1.5\,\mathrm{K}\) | Allowed lag below \(\min(T_a,T_{\mathrm{out}})\) |
| \(\delta_{\mathrm{hi}}=8\,\mathrm{K}\) | Allowed solar/storage overshoot above \(\max(T_a,T_{\mathrm{out}})\) |
| \(\kappa=0.1\) | Wall/air process-noise fraction |

## Formulation
**Envelope** (missing \(T_{\mathrm{out}}\) \(\Rightarrow\) \(T_{\mathrm{out}} := T_a\)):

\[
\min(T_{a,i}, T_{\mathrm{out}}) - \delta_{\mathrm{lo}} \;\le\; T_{w,i} \;\le\; \max(T_{a,i}, T_{\mathrm{out}}) + \delta_{\mathrm{hi}}
\]

intersected with the safety box \([-30,60]^\circ\mathrm{C}\).

**Live CD-EKF.** Diffusion \(\sigma_{\mathrm{wall},i} = \kappa\,\sigma_w\sqrt{q_i}\).  After the Kalman update, project \(\hat T_{w,i}\) onto the envelope using measured \(T_{a,i}\) and \(T_{\mathrm{out}}\).  \(P\) is left unchanged (clipped mean, uncorrected covariance).

**PE Tw0.** For each dataset-start segment, \((T_w(t_0))_i\) is boxed to the envelope of that segment’s first \((T_a, T_{\mathrm{out}})\) sample.

**PE N-step path.** At each open-loop origin step add \(\sum_i w\,\mathrm{viol}_i^2\) to the air SSE, with \(\mathrm{viol}_i\) the signed distance of simulated \(T_{w,i}\) outside the envelope of the matching measured air and outdoor.  Gradient: \(2w\,\mathrm{viol}_i\,\partial T_{w,i}/\partial\theta\) from the existing sensitivity `sx`.  \(w=1\) (same \(\mathrm{K}^2\) units as air SSE before the \(1/(n\sigma_R^2)\) scale).

## Assumptions
- The lumped wall node is thermally between indoor air and outdoor, plus short-lived solar heating of surfaces.
- A wall several kelvin *below* outdoor while indoor air is warm is not a physical 2R2C state for this model.
- Projection is a hard state constraint, not a third RC node.

## Algorithmic choices
- Hard clip on the filter mean (fast, stable for control) rather than a constrained Kalman filter.
- Soft penalty on PE trajectories (differentiable via `sx`) rather than clipping inside the N-step EKF (would zero sensitivities).

## Numerical considerations
- \(\kappa=0.1\): over \(\Delta t=900\,\mathrm{s}\), wall Brownian scale is \(\sim 0.3\,\mathrm{K}\) vs \(\sim 3\,\mathrm{K}\) for air.
- \(\delta_{\mathrm{hi}}=8\,\mathrm{K}\) covers sun-warmed mass without allowing the \(\sim 32^\circ\mathrm{C}\) spikes far above air unless air/outdoor are also high.

## Open items
- None.

## Role in pipeline
Finding docs for `/define` and `/implement`. Math alignment input — not product scope/UX.

## Tracker
- Task: SWD-564
- Research: none
- Branch: `cursor/swd-564-wall-envelope-a891`
- PR: — (model never opens a PR)

## Next
`/architect SWD-564` — Shape stamp on the same branch
