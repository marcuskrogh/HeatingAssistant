# Model: 2R2C wall-node physics in estimation

## Problem statement
The wall/mass temperature \(T_{w,i}\) is not measured.  The CD-EKF reconstructs it from air measurements.  Equal process noise on air and wall, plus a wide \(T_w(t_0)\) box in PE, lets air innovations and \(q_{\mathrm{int}}/R\) degeneracy dump into \(T_w\).  Observed failure: overnight \(\hat T_w\) falls from \(\sim 20^\circ\mathrm{C}\) to \(\sim 6^\circ\mathrm{C}\) while \(T_{\mathrm{out}} > 10^\circ\mathrm{C}\) and \(T_a \approx 22^\circ\mathrm{C}\).  A clip onto \(\min(T_a,T_{\mathrm{out}})-\delta\) would hide the error without teaching \(\theta\) or the filter the wall row of the 2R2C model.

## Notation
| Symbol | Meaning |
|--------|---------|
| \(T_{a,i}, T_{w,i}\) | Air and wall nodes of room \(i\) |
| \(T_{\mathrm{out}}\) | Outdoor air |
| \(g_{\mathrm{aw}}, g_{\mathrm{wout}}\) | Air–wall and wall–outdoor conductances |
| \(\rho = g_{\mathrm{aw}}/(g_{\mathrm{aw}}+g_{\mathrm{wout}})\) | Steady-state mix (PE: \(\rho \approx 1-r_{\mathrm{aw}}\) without sky) |
| \(\kappa=0.1\) | Wall/air process-noise fraction |
| \(\sigma_{\mathrm{lag}}=2.5\,\mathrm{K}\) | Prior std of lag around algebraic SS |

## Formulation
**Algebraic wall SS** (\(dT_w/dt=0\), no inter-room flow):

\[
T_{w,i}^{\mathrm{ss}} = \rho_i T_{a,i} + (1-\rho_i) T_{\mathrm{out}} + Q_{\mathrm{wall},i}/(g_{\mathrm{aw},i}+g_{\mathrm{wout},i})
\]

**Live CD-EKF.** Diffusion \(\sigma_{\mathrm{wall},i} = \kappa\,\sigma_w\sqrt{q_i}\).  After the air Kalman update, fuse \(y_w = T_w^{\mathrm{ss}}\) as a measurement of the wall block with \(R=\sigma_{\mathrm{lag}}^2+(Q_{\mathrm{wall}}/g_{\mathrm{sum}})^2\) (Joseph form).  Air nodes change only through covariance coupling.

**PE Tw0.** Safety box remains \([-30,60]^\circ\mathrm{C}\).  Gaussian MAP mean for each dataset-start segment is \(T_w^{\mathrm{ss}}(\theta)\) at that segment’s first \((T_a,T_{\mathrm{out}},Q_{\mathrm{solar}})\) sample, so \(\mu\) moves when splits / UA / solar scale move.  Gradient includes \(\partial\mu/\partial r_{\mathrm{aw}}\) and solar/UA terms.

**PE N-step path.** At each open-loop origin step add \(\sum_i w(T_{w,i}-T_{w,i}^{\mathrm{ss}})^2\).  Gradient: \(2w(T_w-\mu)(\partial T_w/\partial\theta-\partial\mu/\partial\theta)\).  Do not fuse SS inside the N-step EKF (would distort `sx`).

## Assumptions
- The lumped wall node is thermally between indoor air and outdoor, plus solar on surfaces.
- A wall many kelvin below outdoor while indoor air is warm is not a 2R2C equilibrium of this model; the estimator should learn that via \(\rho(\theta)\), not a floor.
- Fusion is a Bayesian measurement of the wall row, not a hard state constraint.

## Algorithmic choices
- Joseph update on the live filter (keeps \(P\succeq 0\)) rather than clipping the mean.
- Soft SS residual on PE trajectories (differentiable via `sx` and \(\partial\mu/\partial\theta\)) rather than clipping inside the N-step EKF.

## Numerical considerations
- \(\kappa=0.1\): over \(\Delta t=900\,\mathrm{s}\), wall Brownian scale is \(\sim 0.3\,\mathrm{K}\) vs \(\sim 3\,\mathrm{K}\) for air.
- \(\sigma_{\mathrm{lag}}=2.5\,\mathrm{K}\) is lag around SS, not a box.  Initial \(P_{ww}=\sigma_{\mathrm{lag}}^2\) so the first SS measurement is not ignored.

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
