# Model: CD-Kalman with latent wall ODE

## Problem statement

Reconstruct the unmeasured wall/mass temperature \(T_w\) of the live 2R2C
plant so that the estimate stays consistent with the measured air
\(T_a\), outdoor \(T_{\mathrm{out}}\), known inputs \(u\), and solar
\(Q_{\mathrm{sol}}\). The current CD-EKF can send \(\hat{T}_w\) to
\(25\)–\(30\,^{\circ}\mathrm{C}\) while \(T_{\mathrm{out}} < 20\,^{\circ}\mathrm{C}\)
and cooling is on. That is not EKF linearisation error on a strongly
nonlinear plant. It is an unmeasured-node Kalman update plus a sol-air
wall drift.

## Notation

| Symbol | Meaning |
|--------|---------|
| \(T_a\) | Indoor air node (measured) |
| \(T_w\) | Wall / mass node (unmeasured) |
| \(T_{\mathrm{out}}\) | Outdoor air (exogenous) |
| \(x = (T_a, T_w, \varphi, b)^{T}\) | Augmented CD-SDE state (filter \(\varphi\), offset \(b\) optional) |
| \(u\) | Heater / cooler fraction |
| \(d\) | Disturbance vector (outdoor, solar, \(q_{\mathrm{int}}\)) |
| \(Q_{\mathrm{sol}}\) | Window solar gain [W], scaled by identified \(s\) |
| \(w_s\) | `SOLAR_WALL_FRACTION` (fixed \(1/2\)) |
| \(Q_w\) | Net heat into the wall node [W] (solar share + facade − sky) |
| \(g_{aw}, g_{wout}\) | Air–wall and wall–outdoor conductances [W/K] |
| \(C_a, C_w\) | Node capacitances [J/K] |
| \(y^m = T_a + b\) | Measurement |
| \(\hat{x}_{k\mid k}, P_{k\mid k}\) | Filtered mean and covariance |
| \(K = (K_a, K_w, K_\varphi, K_b)\) | Kalman gain, partitioned |
| \(\nu_k\) | Air innovation \(y^m_k - \hat{y}_{k\mid k-1}\) |
| \(T_{w,\mathrm{ss}}\) | Instantaneous wall quasi-steady (diagnostic, not a clip) |

## Formulation

**Plant (physical 2R2C).** Affine in \((T_a, T_w)\):

\[
\begin{aligned}
C_a \dot{T}_a &= Q_a(u, T_{\mathrm{out}}) + (1-w_s)s Q_{\mathrm{sol}} + g_{aw}(T_w-T_a) + g_{\mathrm{inf}}(T_{\mathrm{out}}-T_a), \\
C_w \dot{T}_w &= Q_w + g_{aw}(T_a-T_w) + g_{wout}(T_{\mathrm{out}}-T_w).
\end{aligned}
\]

\(Q_a\) is heating/cooling on the air node only. \(Q_w\) is the solar
share \((w_s + \alpha\cdot\mathrm{share}) s Q_{\mathrm{sol}}\) plus sky
offset. Inter-room wall coupling is the existing sparse \(g_{ij}\)
(omitted here). Wind infiltration and open-window UA are overlays on the
air row; they do not make \(F = \partial f/\partial(T_a,T_w)\)
state-dependent except through a weak Sherman–Grimsrud \(\sqrt{|T_a-T_{\mathrm{out}}|}\)
term.

**Consequence for the filter class.** On the \((T_a, T_w)\) block the
drift Jacobian is the constant RC matrix \(F\). A CD-EKF, CD-UKF, and
linear CD-Kalman filter coincide for that block. Switching to UKF,
cubature, or a particle filter does not remove high \(\hat{T}_w\).
Heat-pump COP\((T_{\mathrm{out}})\) and `smooth_thermal_power`(\(\varphi\))
are nonlinear in input/disturbance, not in the wall state.

**Observation.**

\[
y^m_k = C x_k + v_k, \qquad C = [I \quad 0], \qquad v_k \sim \mathcal{N}(0, R).
\]

Only air is observed. \(\hat{T}_w\) is inferred through \(g_{aw}\) and
through process noise. Any air residual that the model does not explain
(cooling map error, occupancy, solar-scale error) has a Kalman path into
\(T_w\). That path is what produced the earlier \(\hat{T}_w \to 5\,^{\circ}\mathrm{C}\)
on heat pulses (SWD-564: same \(\sigma_w\) on wall and air \(\Rightarrow K_w \approx 1\)).
Capacitance-scaled \(\sigma_{T_w}\) lowered \(K_w\); it did not set
\(K_w = 0\), so innovations can still raise \(\hat{T}_w\) when cooling is
over-predicted and measured \(T_a\) stays warm.

**Sol-air envelope.** With \(Q_w = 0\), the wall steady state is a convex
combination of \(T_a\) and \(T_{\mathrm{out}}\). With solar on the mass
node it is not:

\[
T_{w,\mathrm{ss}} = \frac{g_{aw} T_a + g_{wout} T_{\mathrm{out}} + Q_w}{g_{aw}+g_{wout}}.
\]

A few kilowatts of wall solar for a few hours on a typical \(C_w\) is
several kelvin above air. Room-view “Wall” at \(25\)–\(27\,^{\circ}\mathrm{C}\)
with \(T_a \approx 24\,^{\circ}\mathrm{C}\) and \(T_{\mathrm{out}} \approx 16\,^{\circ}\mathrm{C}\)
during a solar spike is compatible with this ODE. It is **not** compatible
with the conduction-only box \(\mathrm{conv}\{T_a, T_{\mathrm{out}}\}\).

**Estimator (chosen).** Keep the production continuous-discrete Kalman
filter on the affine 2R2C. Stop using air innovations to write the wall.

1. **Predict** as today (same implicit Euler, same \(F\), same solar split,
   same capacitance-scaled \(\sigma\)).
2. **Update air, filter, and offset as today.** Set the wall block of the
   Kalman gain to zero:

\[
K_w \equiv 0.
\]

   Equivalently: after the standard update, replace \(\hat{T}_w^+\) by
   \(\hat{T}_{w,k\mid k-1}\) and drop \(T_w\)–measurement cross-covariance
   from the next gain. The wall is a **disturbance-driven latent ODE**,
   not a dump for \(\nu_k\).
3. **Do not clip to \(T_{w,\mathrm{ss}}\).** That quasi-steady point with
   \(Q_w \sim 2\,\mathrm{kW}\) and \(g_{wout}\sim 25\,\mathrm{W/K}\) is
   \(\sim 60\,^{\circ}\mathrm{C}\) — it does not bound a live spike.
   Optional numerical floor/ceiling around the open-loop predict is
   redundant once \(K_w=0\). A product display clamp is not the estimator.

If \(\hat{T}_w\) still walks to \(30\,^{\circ}\mathrm{C}\) with \(K_w=0\),
the ODE itself is hot: solar-on-wall, \(s\), \(C_w\), or \(g_{wout}\).
That is plant/ID, not filter class.

**Not chosen.** UKF / particle filter (same linear wall block). Moving-horizon
estimation (heavier than this plant needs). Hard clamps to
\(\mathrm{conv}\{T_a,T_{\mathrm{out}}\}\) (fights solar-on-wall). Clipping
to \(T_{w,\mathrm{ss}}\). Reverting SWD-564 equal-Watt \(\sigma\).

## Assumptions

- Live plant stays 2R2C; air-only measurements.
- Solar continues to split onto the wall (`SOLAR_WALL_FRACTION`). “Wall”
  on the room plot is a mass/sol-air node, not a conduction-only envelope.
- Cooling thermal map is the existing piecewise `smooth_thermal_power`
  (input error is a plant/ID issue, not a reason to change filter class).
- Offset \(b\) remains the slow air bias; it must not be replaced by
  writing the same bias into \(T_w\).

## Algorithmic choices

- Production filter: CD-Kalman (existing `ContinuousDiscreteEKF` on an
  affine drift), **wall gain blocked**.
- No sol-air-\(T_{w,\mathrm{ss}}\) projection (unbounded for large \(Q_w\)).
- PE / NMPC keep the same SDE predict. Live reconstruction uses
  \(K_w = 0\); open-loop PE walls already have that.
- Do not add a new UI noise knob.

## Numerical considerations

- Energy scale: \(Q_w \Delta t / C_w\) is the open-loop kelvin rise; a
  \(2\,\mathrm{kW}\) solar share for \(3\,\mathrm{h}\) on
  \(C_w = 4\times 10^6\,\mathrm{J/K}\) is \(\approx 5.4\,\mathrm{K}\).
  Quasi-steady \(T_{w,\mathrm{ss}}\) with the same \(Q_w\) and
  \(g_{aw}=g_{wout}=25\,\mathrm{W/K}\) is \(60\,^{\circ}\mathrm{C}\) —
  diagnostic only, not a clip target.
- With \(K_w=0\), do not zero \(P_{ww}\) (PE/NMPC still need a wall
  covariance prior).

## Open items

- Whether forecast Wall (already an open-loop roll) needs any extra
  clamp for the room plot.
- Whether to move all solar onto air if the product meaning of “Wall”
  must stay inside \(\mathrm{conv}\{T_a,T_{\mathrm{out}}\}\). That is a
  plant change, not an estimator class change.

## Role in pipeline

Finding docs for `/define` and `/implement`. Math alignment input — not
product scope/UX.

## Tracker

- Task: [SWD-570](https://marcusknielsen.atlassian.net/browse/SWD-570)
- Relates: [SWD-564](https://marcusknielsen.atlassian.net/browse/SWD-564), [SWD-554](https://marcusknielsen.atlassian.net/browse/SWD-554)
- Research: — (plant affine; UKF literature not required)
- Branch: `cursor/constrained-cdkf-wall-5de1`
- PR: — (model never opens a PR)
- Artifact: `docs/agents/MODEL-state-estimation.md`

## Next

`/define SWD-570` — superseded: live plant is 1R1C (`MODEL-1r1c.md`)
