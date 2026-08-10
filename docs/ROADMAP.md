# Roadmap

> The planned technical evolution of Heating Assistant's control software — how
> the thermal model, state estimator, optimal-control problem, parameter
> identification and disturbance forecasts will deepen over upcoming releases.

This is a forward-looking engineering plan, not a user guide. For what the
integration does *today*, see the [main README](../README.md) and [Physics,
Models & Control Theory](THEORY.md).

---

## 17. Roadmap

This roadmap describes the **technical evolution of the control software**:
how the thermal model, state estimator, optimal-control problem, parameter
identification, and disturbance forecasts will deepen over the next several
release cycles.  Distribution, UI polish, and HACS publication are tracked
separately in §17.13 and treated as one-line items.

The plan is sequenced as **phases** rather than calendar quarters.  Each
phase lists its prerequisite phases so the order is explicit, and every step
identifies the failure mode it fixes, the technique it introduces, and the
measurable acceptance criterion that closes it.

**Status of phases.**  Phases 1–3 are **locked in detail**: capability
scope, locked design decisions, definition of done (implementation +
README + tests + config UI), sequenced steps with acceptance criteria,
migration plan, and deferred items.  Implementation work begins with
Phase 1 Step 1.  Phases 4–11 are **locked at the capability level**
documented below; each will receive the same sequenced,
definition-of-done treatment as its implementation work begins.  At
that point, items that turn out to be premature or unnecessary (as
happened with most of the original Phase 2 menu) will be moved to a
deferred footer with rationale.  This staged approach keeps the
near-term plan concrete and the long-term direction visible without
front-loading detail that is likely to change.

---

### 17.1 Current technical baseline

This is the surface we build on.  Every later phase is described as a
*delta* from this baseline.

| Layer | Today |
|-------|-------|
| **Plant model** | Two thermal nodes per room ($2R2C$: fast air + slow wall) with wall-to-wall inter-room conductances and split infiltration/conduction outdoor coupling; Erbs-decomposed solar gain (clear-sky/cloud/forecast) with identified per-room scale; Carnot-corrected heat-pump COP |
| **Disturbances** | Outdoor temperature (HA weather forecast, interpolated to horizon); solar irradiance from latitude/longitude clear-sky pipeline |
| **State estimator** | Continuous-discrete EKF; implicit-Euler sub-stepped covariance propagation; per-room scalar measurement update |
| **Optimal-control problem** | Receding-horizon linearized MPC over continuous $u\in[0,1]$ minimising tracking + energy + Δu smoothing + soft-constraint slack; zero-order hold; horizon $N$ steps × $\Delta t$ (default 6 × 15 min) |
| **Solver** | OSQP/HiGHS (batch convex QP after linearization); analytic Jacobians for `f` and `h` |
| **System ID** | Offline maximum-likelihood (multi-step open-loop objective with analytic forward sensitivities) over the rolling history buffer; identifies $C_i$, $R_{i,\text{ext}}$, internal gain, heater scale, inter-room R, per-room solar scale, and the 2R2C envelope splits (gated) |
| **Constraints** | Box on $u$; soft slack on temperature corridors; Schmitt-trigger hysteresis on cooling mode |
| **Cycle time** | ~5 s for a 5-room / 6-step horizon on a small NUC |

The remainder of §17 lifts each of these rows in turn.

---

### 17.2 Phase 1 — Modelling fidelity (the new default plant model)

**Why:** The 1R1C lumped model is the dominant residual-error source.
Open-loop RMSE > 0.5 °C usually tracks back to unmodelled envelope
dynamics, slab thermal lag, wind-driven infiltration, or unaccounted-for
nocturnal long-wave loss.  Fixing the plant is the single highest-leverage
upgrade — every later phase consumes residuals that Phase 1 reduces.

**Depends on:** baseline.  **Unlocks:** Phases 2, 4, 5, 7.

#### Locked design decisions

These are the calls we've made for the v1 of Phase 1 and they bound the
scope of every item below:

- **2R2C-with-slab becomes the new default per-room model**, not an opt-in
  variant.  Existing installs are migrated by a one-shot re-identification
  on first start after the upgrade; failure (insufficient excitation) falls
  back to the 1R1C parameters until enough history accumulates.
- **All inter-room connections are wall-to-wall (mass-to-mass).**
  Cross-couplings route through the envelope node $T_w$, not the air node.
  The existing `connections:` schema is unchanged.
- **Open-plan spaces are declared as a single room** in YAML / UI — a
  single well-mixed air node is the correct model for an open archway
  anyway, and this keeps the configuration surface narrow.  An explicit
  "open" connection type stays on the deferred list (see below) and will
  be added only if real-world data motivates it.
- **The implicit-Euler integrator from `mbc` is adopted as part of
  Phase 1**, not deferred to Phase 7 numerics.  2R2C + slab has a
  stiffness ratio of ~$10^3$–$10^4$; an L-stable solver is a hard
  prerequisite, not a polish item.

#### Definition of done (applies to every item below)

Each Phase 1 item is "done" only when **all four** of the following are
landed in the same release:

1. **Implementation** in `thermal_model.py` / `heat_sources.py` /
   `controller.py` / `parameter_estimator.py` as appropriate.
2. **README updated.**  §3.1 (lumped model), §3.2 (state-space form),
   §3.3 (integration), §3.4 (solar) and §3.5 (heat sources) are rewritten
   to match the new physics.  Configuration reference (§10), examples
   (§11), and the parameter-estimation guide (§14) are updated wherever
   user-facing parameters appear.
3. **Regression tests.**  At minimum: a synthetic ground-truth test for
   the new dynamics, a closed-loop replay test against the previous
   model's behaviour on the bundled scenarios, and a parameter-
   identification test for any new parameter.  Existing tests must
   continue to pass; the 14 known `mbc`-dependent failures stay the
   only exceptions.
4. **Configuration UI extended.**  Where a Phase 1 item introduces a
   new user-facing parameter, `config_flow.py` / `_options_flow.py` /
   `strings.json` / `translations/en.json` are updated so the parameter
   is reachable through the wizard *and* the options flow.  Defaults
   come from typology presets (building age, floor type, facade colour);
   advanced users can override.

#### Sequenced work-plan

Each step assumes the previous steps are landed.

- [x] **Step 1 — N1: Implicit-Euler integrator (numerics first).**  Adopt
  `mbc`'s implicit-Euler stepper for the EKF state propagation, the
  Riccati covariance update, and the OCP dynamics constraint.  Validate
  on the *current* 1R1C model before any structural change so the
  numerics swap is decoupled from the modelling swap.  Acceptance:
  bit-equivalent closed-loop behaviour on the synthetic baseline
  scenarios, plus a new stress test with a deliberately stiff
  configuration (stiffness ratio ≥ $10^3$) that explicit Euler fails
  and implicit Euler passes.  README: §3.3 rewrite.  Tests: new
  explicit-vs-implicit equivalence suite under `tests/test_integrator.py`.
  Config UI: no change.
- [x] **Step 2 — C1: Infiltration vs conduction split.**  Replace the
  bundled $1/R_{i,\text{ext}}$ with a fixed conductive $UA_\text{cond}$
  and a wind-driven Sherman–Grimsrud infiltration term
  $\rho c_p \dot V_\text{inf}(v_w, \Delta T)$.  Identification jointly
  fits $UA_\text{cond}$ and a small set of building-wide leakage
  coefficients.  Acceptance: residual correlation with forecast wind
  speed drops to within $\pm 0.05$ on the bundled traces.  README:
  §3.1 update, §10/§11 new envelope-tightness field.  Tests:
  windy-day synthetic regression + leakage-coefficient identification
  test.  Config UI: new "envelope tightness" preset selector
  (`leaky` / `typical` / `tight` / `passive_house`) with
  auto-mapping to Sherman–Grimsrud coefficients; advanced users may
  override the raw values.
- [x] **Step 3 — A1: 1R1C → 2R2C envelope.**  Per-room split into
  air node $T_a$ (small $C_a$, fast) and envelope node $T_w$ (large
  $C_w$, slow) coupled by $R_{aw}$, with the envelope conducting to
  outdoor via $R_{we}$.  All inter-room couplings route through $T_w$.
  Default-on for every install; auto-fallback to 1R1C per room if the
  $C_w$ posterior fails to tighten beyond a configurable threshold
  after $N$ days of observation.  Acceptance: open-loop 30-min RMSE
  drops ≥ 30 % on the bundled golden traces; the (C, R) log-likelihood
  slice flattens from banana to ellipse on richly-excited synthetic
  data.  README: §3.1, §3.2 rewrite; §14 thermal-mass guide updated to
  document the new air/envelope split.  Tests: step-response
  regression; observability check on the 2×2 system; loglik-shape test;
  EKF $T_w$ back-estimation test.  Config UI: the existing
  thermal-mass preset gains an internal split-ratio defaulted by
  building age; advanced users can override.  The dashboard hides
  $T_w$ from the default sensor list and exposes it only on the
  diagnostics view.
- [x] **Step 4 — A2 + B1: Slab node and UFH routing.**  Add a third
  state $T_s$ per room with `floor_type ∈ {slab_on_grade, concrete,
  ufh}`, coupled to a built-in ground-temperature driver $T_g(t)$
  (sinusoidal annual + diffusion lag, no external data required).
  UFH heat sources route their power into $T_s$ rather than $T_a$,
  capturing the characteristic 4–8 h lag.  Acceptance: a synthetic
  UFH step test reproduces the expected slab→air delay within 15 %.
  README: §3.1 (slab equations), §3.5 (UFH routing), §10/§11
  (new `floor_type` field).  Tests: slab-dynamics regression + UFH
  lag regression + ground-temperature driver test.  Config UI: new
  per-room `floor_type` selector defaulted by building age, with
  free-form override.
- [x] **Step 5 — B2 (pragmatic): Per-source first-order emitter
  filter.**  Each heat source's commanded fraction is passed through
  a first-order filter with an identified time constant
  $\tau_\text{em}$ before reaching the air (or slab, for UFH) node.
  Captures the dominant TRV / valve / metal-mass lag without
  requiring supply-temperature telemetry.  Acceptance: closed-loop
  overshoot on a radiator-equipped room drops by ≥ 50 % on the
  bundled traces.  README: §3.5 (emitter filter), §10/§11 (per-source
  time constant).  Tests: filter-state-identification test +
  closed-loop overshoot regression.  Config UI: per-source
  `emitter_time_constant` field with type-based default (electric =
  0 s; radiator = 600 s; fan-coil = 60 s).  The full water-loop
  emitter with $T_\text{supply}$ telemetry is deferred to Phase 6.
- [x] **Step 6 — Finishing pass: C3, C4, C5.**  Three small,
  independent residual terms shipped together:
    - **C3 long-wave radiation to sky.**  Add a radiative
      conductance in parallel with conduction on outer surfaces,
      using effective sky temperature
      $T_\text{sky} = T_e - \Delta T_\text{sky}$ with a constant
      fallback $\Delta T_\text{sky} = 6$ K.  Phase 5 promotes this
      to a cloud-cover-driven term later.
    - **C4 sol-air temperature on opaque surfaces.**  External
      walls and roof see
      $T_\text{sol-air} = T_e + \alpha \cdot G_\text{inc} / h_e$
      using the existing per-surface tilt/azimuth pipeline.  Per-
      surface absorptance $\alpha$ defaulted by colour preset.
    - **C5 thermal-bridge correction.**  Per-room $\Psi L$ [W/K]
      added to external $UA$, identified from data with default 0
      and a strong prior centred on 0.
  Acceptance: clear-night nocturnal-cooling bias drops below
  $0.15$ °C; south-facing-facade midday bias drops below $0.15$ °C;
  no regression on rooms where C5's posterior posterior stays at 0.
  README: §3.1 (sky and sol-air terms), §3.4 (solar pipeline update),
  §10 (facade-colour preset).  Tests: residual-pattern regressions on
  three targeted synthetic traces.  Config UI: per-room
  `facade_colour` preset (`light` / `medium` / `dark` / `custom_alpha`);
  thermal-bridge value entirely auto-identified, no user input.

#### Migration

The very first start after upgrading triggers a one-shot
re-identification on the new (2R2C + slab + emitter) model using the
persisted history buffer.  If the re-identification succeeds (all
parameter posteriors tighten), the live controller switches to the new
model atomically.  If it fails (insufficient excitation, ill-
conditioned likelihood), each affected room keeps its 1R1C parameters
and re-identifies opportunistically as more excitation accumulates.
The `format_version` field in persisted storage is bumped so a
downgrade cleanly rejects the new layout.

#### Deferred to later phases (kept on the roadmap)

- **A3 — Stratified-air node for tall rooms.**  Two-air-node model
  with buoyancy-driven mixing.  Gated by a `room_type: tall` flag.
  → **Phase 1.5** once the v1 Phase 1 results are in production and
  the demand is visible.
- **B2 (full) — Two-state water-loop / metal emitter.**  Honest
  $C_w^{\text{rad}}$ + $C_m$ dynamics with $T_\text{supply}(t)$ from
  the heat-source side.  → **Phase 6**, alongside heat-pump telemetry
  adapters that surface the supply-temperature feed.
- **C2 — Latent-heat / enthalpy state.**  Humidity ratio $w_i$ per
  room, sources from occupancy/cooking, sinks from ventilation/AC.
  → **Phase 3** (required by the PMV/PPD comfort objective) and
  **Phase 6** (cooling-mode realism with sensible/latent split).
- **"Open" connection type.**  Doorway/archway-as-air-coupling
  between rooms, as an alternative to merging open-plan rooms.
  → **future phase**, only if real-world feedback shows that the
  "declare open-plan as one room" workaround is unworkable.
- **Per-window air-exchange-rate modelling.**  Phase 3's W1
  open-window override is a coarse "force $u = 0$ during open
  periods" rule that does not predict the cooling rate of the
  room.  A model-level upgrade would identify each configured
  window's effective open-state air-exchange rate so the OCP can
  predict the room's cooling trajectory and the post-close
  recovery dynamics.
  → **Phase 1.5** if the coarse W1 override proves insufficient
  in practice.

---

### 17.3 Phase 2 — Outlier rejection (predictive-likelihood gating)

**Why:** The implicit CD-EKF adopted in Phase 1 (N1) is sufficient for
state estimation on this plant.  The measurement function is linear
($y = H x$ with $H$ picking $T_a$ out of the per-room state vector); the
residual nonlinearity in $f$ (Carnot COP, sol-air, long-wave to sky) is
mild and gets re-linearised at every implicit-Euler substep; and
parameters are positive-by-construction in the storage representation,
so MHE's constraint-handling advantage is muted.  The estimator's only
remaining weakness is robustness to bad measurements — today only
`None` values are filtered, so a stuck-at thermistor, a ghost reading,
or a brief sensor fault feeds straight into the measurement update
and throws the filter for hours.  Phase 2 fixes exactly that and
nothing more.

**Depends on:** none (uses only the $\hat y^-, S$ pair the existing
CD-EKF already produces).  Ships in parallel with Phase 1.
**Unlocks:** Phase 10 (rejection events feed the longer-term fault
diagnosis).

#### The mechanism

At each measurement-application cycle the CD-EKF already produces the
predictive distribution

$$\hat y^- = H \hat x^-, \qquad S = H P^- H^T + R$$

so under a Gaussian assumption $p(y \mid \hat x^-) = \mathcal{N}(\hat y^-, S)$.
For each incoming measurement $y$, compute the squared
normalised-innovation distance

$$d^2 = (y - \hat y^-)^T S^{-1} (y - \hat y^-)$$

and **hard-reject** the measurement if $d^2 > k^2$ for a configurable
threshold $k$ (default $k = 5\sigma$, false-reject rate ~5.7 × 10⁻⁷
under normality).  For our current scalar measurement this reduces to
the one-line test $|y - \hat y^-| / \sqrt{S} > k$; written in the
general multivariate form so the same code-path applies when humidity
or other measurements are added later.

This sits *on top of* the existing `None`-value filter and the existing
sensor-availability checks; it does not replace them.

#### Behaviour and design calls

- **Hard-reject, not down-weight.**  When the test fails, skip the
  measurement update entirely for that sensor on that cycle.  Predict
  still runs; $P^-$ stays uncorrected and grows on the next cycle.
  Simpler and more honest than inflating $R$.
- **Auto-thaw via covariance growth.**  Persistent rejection grows
  $P$, which grows $S$, until a previously-extreme reading falls
  within $k\sigma$ and the filter reaccepts.  This is the right
  behaviour for genuine fast transients (a window opens; the filter
  catches up after a brief lag) and for sensors that recover from
  being stuck.  A permanently broken sensor is *not* permanently
  muted by this layer — that diagnosis belongs in Phase 10.
- **Independent per sensor in multi-sensor rooms.**  Each sensor's
  measurement gets its own test against the same predictive $S$; a
  drafty sensor that consistently disagrees with the others is
  silenced on its own merit while the others keep updating.
- **Normality assumed.**  We rely on the Gaussian predictive
  distribution.  No empirical likelihood, no heavy-tailed
  alternatives.  Sufficient for this plant; revisited only if a
  specific failure mode demands it.
- **Out of scope.**  This gate sits at the measurement-update
  boundary.  It does not validate disturbance inputs (outdoor
  temperature, irradiance, weather-forecast trajectories) — those
  have their own validation path in `weather.py` (the U3
  `WeatherForecastStatusSensor`) — and it does not validate
  setpoints or comfort-schedule inputs.

#### Definition of done

1. **Implementation** in `controller.py` / `coordinator.py` at the
   measurement-application boundary; written for the multivariate
   case from the start.
2. **README update.**  §4.2 (state estimation) gains a subsection
   describing the gate, the auto-thaw mechanism, and the explicit
   scope boundary.
3. **Regression tests.**  False-reject rate ≤ $10^{-5}$ under
   synthetic Gaussian noise at $k = 5\sigma$; true-reject rate
   ≥ 99 % against injected $> 10\sigma$ spikes; thawing within
   $M$ cycles after sustained rejection; multivariate
   generalisation (2-D synthetic case); cold-start non-interference
   (initial inflated $P$ must not trigger spurious rejections);
   per-sensor independence in a multi-sensor room.
4. **Configuration UI.**  New options-flow field
   `outlier_sensitivity` with presets `conservative` (5σ) /
   `moderate` (4σ) / `aggressive` (3σ), defaulted to
   `conservative`.  Per-measurement-source rejection counter and
   last-rejection timestamp exposed as diagnostic-category sensors.
   Rate-limited INFO log on each rejection following the U3
   weather-failure logging pattern.

#### Deferred from Phase 2

The items below were considered for Phase 2 and explicitly rejected
because the implicit CD-EKF plus the outlier gate above is sufficient
for this plant.  Each remains a known option for future work, to be
revisited only if a specific failure mode is observed in production:

- **Iterated EKF (IEKF).**  Buys nothing on a linear measurement
  function $y = H x$; would only matter if a future $h$ becomes
  nonlinear (e.g. a fused-PMV observation).
- **Continuous-Discrete UKF (CD-UKF).**  Second-order sigma points
  add little when the state-transition nonlinearity is mild and
  gets re-linearised at every implicit-Euler substep.  Kept on the
  shelf for a future model with sharp curvature in $f$.
- **Moving-Horizon Estimator (MHE).**  Constraint-handling
  advantage is muted because parameters are positive-by-
  construction in the storage representation; revisit only if
  observed sensor biases or parameter excursions cross hard bounds.
- **Square-root EKF/UKF form.**  Numerical safeguard against loss
  of positive-definiteness; the current plain form is stable in
  practice on the implicit-Euler-integrated dynamics.  Revisit
  only if PD violations are observed.
- **Rauch–Tung–Striebel smoother.**  Useful for offline residual
  analysis; not needed because Phase 4 system ID and Phase 8
  golden-trace work operate on filtered (not smoothed) states.
- **Adaptive process / measurement noise.**  Risky without a
  whiteness monitor that distinguishes "noise drift" from "model
  drift"; static $Q$, $R$ from configuration is more predictable.
- **Augmented joint state-and-parameter estimation.**  Online
  parameter tracking is deferred to Phase 4's offline Bayesian
  identification, which is more controllable and auditable.
- **Per-sensor bias and drift state.**  Persistent multi-sensor
  disagreement is silenced by the per-sensor outlier gate above;
  explicit bias modelling becomes interesting only if real-world
  data shows disagreement consistently below the gate threshold.
- **Particle filter fallback.**  Gaussian assumption with the
  outlier gate handling heavy-tail rejections is sufficient.

#### Further robustness considerations (deferred)

A separate question — *what if a sustained model bias causes the gate to
reject everything?* — surfaces four orthogonal safeguards.  Given the
typical 10–15 minute update interval, the basic auto-thaw via process-
noise accumulation alone delivers a worst-case reacceptance time well
under 1.5 hours, which is acceptable for slow thermal dynamics.  None
of these are in the Phase 2 v1 scope; each is implementation-detail-
sized and orthogonal, and can be added incrementally without touching
the basic mechanism if specific failure modes appear in production:

- **Multiplicative $P^-$ inflation on rejection** (factor $\beta > 1$
  per rejected cycle).  Turns the linear thaw into exponential thaw;
  reduces worst-case reacceptance from ~$30Q^{-1}$ cycles to
  ~$\log(\cdot)/\log\beta$ cycles.
- **Consecutive-rejection cap with force-accept.**  After
  $N_\text{max}$ consecutive rejections, the next measurement is
  force-accepted with inflated $R_\text{forced} = \gamma R$.  Hard
  backstop against any pathological persistent rejection.
- **Floor on innovation variance.**
  $S_\text{eff} = \max(S, S_\text{min})$ guarantees a minimum gate
  width regardless of how confident the filter or how tightly $R$ is
  configured.  Protects against pathological $R$ misconfiguration.
- **Physical-bounds sanity check.**  If $\hat T_a$ drifts outside
  $[-20, 50]\,°C$, log ERROR, raise a Repairs issue, and re-anchor
  from the most recent reading.  Belt-and-braces against the
  worst-case where every other layer has failed.

---

### 17.4 Phase 3 — Cost-aware corridor MPC (continuous-time, continuous-variable)

**Why:** The current OCP is a setpoint-tracking quadratic with a
dimensionless energy term: it pulls the room toward a single
temperature target with no notion of price, and it actively works even
when the room is comfortably within the user's tolerance band.  Real
users want a comfort *band*, and a controller that exploits
time-of-use tariffs by pre-heating cheaply, not just physically.
Phase 3 reformulates the cost around a soft comfort corridor with an
economic energy term, while keeping the continuous-variable OCP and
the existing solver stack intact.

**Depends on:** Phase 1 (the multi-state plant model is the prediction
substrate).  **Unlocks:** Phase 6 (heat-source-side cost models),
Phase 11 (whole-home co-optimisation).

#### Locked design decisions

- **Continuous-time, continuous-variable OCP.**  No mixed-integer
  formulation in Phase 3.  On/off heaters are dispatched at the
  actuator boundary by the existing `switch.*` / `number.*` / `climate.*`
  dispatch logic.  A duty-cycle dispatcher that translates
  continuous $u$ into a temporal on/off pattern within the sample
  interval (e.g. "$u = 0.33$ ⇒ on for 5 of 15 minutes") is on the
  deferred list as a follow-up to add only if real-world dispatch
  quality demands it.
- **Soft corridor with a weak setpoint pull as the new cost
  structure**, implemented as a smooth slack-variable formulation
  (mbc's native soft-constraint mechanism).  Per horizon step, two
  non-negative slack variables $s^\text{lo}_k, s^\text{hi}_k$ are
  added to the decision vector and the cost becomes

  $$J_T = \sum_k \big[\varepsilon (T_k - T^\text{ref}_k)^2 + \rho_\text{soft} (s^\text{lo}_k)^2 + \rho_\text{soft} (s^\text{hi}_k)^2 \big]$$

  subject to the soft inequalities

  $$T_k + s^\text{lo}_k \;\ge\; T^\text{lo}_k, \qquad T_k - s^\text{hi}_k \;\le\; T^\text{hi}_k, \qquad s^\text{lo}_k, s^\text{hi}_k \;\ge\; 0.$$

  No `max`, no piecewise term — the objective is a smooth quadratic
  in $(u, s)$ and the constraints are linear, so IPOPT and SLSQP use
  their existing gradient and Hessian pipelines without any
  reformulation per cycle.  With $\varepsilon \ll \rho_\text{soft}$,
  the controller is essentially economic-driven inside the corridor
  with a barely-perceptible preference for the setpoint; the slack
  penalty dominates at and outside the edges and pulls back.  The
  user-facing configuration grows a `[T_lo, T_hi]` corridor; the
  existing `setpoint` becomes the soft attractor inside the band.
- **Economic energy term that defaults to flat unit price.**  The
  $\|u\|^2_R$ energy term is replaced by
  $\sum_k \pi_k \cdot P^\text{elec}_k(u_k, d_k) \cdot \Delta t$ where
  $\pi_k$ is the per-step electricity price.  When no tariff entity
  is configured, $\pi_k \equiv 1$ (dimensionless), and the term
  reduces to a unit-priced energy minimisation that gives the same
  effective behaviour as today's energy weight.  When a tariff entity
  *is* configured (Nord Pool, Tibber, Octopus, EPEX, flat tariff,
  any HA sensor in €/kWh), $\pi_k$ becomes the time-varying real
  price and the controller pre-heats during cheap hours.
- **Robust corridor tightening with a constant default σ.**  The
  corridor edges $T^\text{lo}_k, T^\text{hi}_k$ at each horizon step
  are tightened inward by $k_\alpha \cdot \sigma_\text{const}$ where
  $\sigma_\text{const}$ is a configured constant standard deviation
  (default 0.3 K) and $k_\alpha$ is a fixed quantile (default 1.96 for
  95 % confidence).  Phase 5 later replaces $\sigma_\text{const}$ with
  the actual forecast-ensemble standard deviation per step, at which
  point the tightening becomes time-varying without changing the OCP
  structure.
- **Efficient-by-default implementation as a non-functional
  requirement.**  Every Phase 3 item carries an obligation to use
  warm-starts across cycles, exploit known sparsity in the NLP build,
  avoid per-cycle Python-level Jacobian recomputation where the
  structure is constant, and add benchmark coverage to
  `BENCHMARKS.md`.  Real-Time Iteration (RTI) and acados migration
  remain in Phase 7 — they are structural changes to the solver
  stack, best done once across the whole codebase.

#### Definition of done (per item, same as Phase 1 / 2)

1. **Implementation** in `controller.py` / `coordinator.py` /
   `parameter_estimator.py` as appropriate.
2. **README updated.**  §4.3 (OCP), §4.5 (control cycle) rewritten;
   §10 (configuration reference) and §11 (examples) updated for any
   new user-facing parameter; §14.5 (MPC tuning) updated for the new
   weights.
3. **Regression tests.**  Synthetic closed-loop scenario suite plus
   a price-aware regression against a flat-tariff baseline plus a
   corridor-violation rate test under Monte Carlo forecast
   realisations.  Benchmark entries added to `BENCHMARKS.md`.
4. **Config UI extended.**  New options-flow fields for corridor
   edges, tariff entity, tightening parameters, and (for W1)
   per-room window sensors plus global open/close timing; defaults
   derived from existing configuration so no manual migration is
   required.

#### Sequenced work-plan

1. [ ] **Step 1 — O2: Soft corridor with weak setpoint pull.**
   Reformulate the temperature cost from pure quadratic tracking to
   the smooth slack-variable corridor form above, using mbc's
   existing soft-constraint mechanism.  Add per-step
   $s^\text{lo}_k, s^\text{hi}_k$ to the decision vector with
   non-negativity bounds, the linear inequalities relating them to
   $T_k$, and the $\rho_\text{soft} \sum_k (s^\text{lo}_k)^2 + (s^\text{hi}_k)^2$
   penalty plus the $\varepsilon$-weighted setpoint attractor.
   Migrate existing `setpoint` + `turn_off_deadband` into a corridor
   $[T^\text{ref} - \text{deadband},\, T^\text{ref} + \text{deadband}]$
   on first start (no user action required).  Default $\varepsilon$
   chosen so the setpoint pull is dominant at the corridor's centre
   but negligible at the edges; default $\rho_\text{soft}$ large
   enough that 0.1 K of corridor violation contributes the same cost
   as the most expensive single-hour tariff peak.  Acceptance: with
   a flat tariff, mid-corridor and no disturbances, $u^* = 0$ (no
   heat applied) instead of the current chasing behaviour; corridor
   edges are respected on a year-long synthetic trace; closed-loop
   solver iteration count stays within ~10 % of the pre-refactor
   baseline (the slack variables grow the NLP slightly).  Config UI:
   per-room `comfort_corridor_low` / `comfort_corridor_high` fields
   (defaulted from setpoint ± deadband); existing `setpoint`
   retained as the soft attractor.
2. [ ] **Step 2 — O1: Tariff-aware economic energy term.**  Replace
   $\|u\|^2_R$ with $\sum_k \pi_k P^\text{elec}_k \Delta t$.  Add a
   per-room or per-source optional `tariff_entity` configuration that
   resolves to a €/kWh number (a fixed-value HA `input_number`, a
   `sensor.*` exposed by Nord Pool / Tibber / Octopus, or unspecified
   = flat 1).  Sample the tariff at each horizon step via the same
   interpolation pipeline as the weather forecast.  Acceptance: on a
   synthetic day with a 4:1 cheap-vs-peak price ratio, the controller
   shifts ≥ 50 % of heating energy from peak to cheap hours while
   keeping corridor-violation rate under 5 %.  Config UI: per-config-
   entry `tariff_entity` field (optional, defaults to none = flat 1).
   New diagnostic sensors `cost_forecast` (€/h) and
   `cost_savings_today` (€ vs flat-tariff baseline).
3. [ ] **Step 3 — O3: Per-source Δu smoothing weights.**  Promote the
   global $\|\Delta u\|^2_S$ scalar weight to a per-source vector so a
   heat pump gets a heavier penalty than an electric resistive heater.
   Default values keyed by heat-source type (`electric`: small,
   `heat_pump`: medium, `modulating_boiler`: medium).  This is the
   continuous-regime analogue of "equipment wear cost".  Acceptance:
   on a heat-pump room with a noisy outdoor-temperature forecast,
   commanded Δu magnitude drops measurably vs the global-weight
   baseline.  Config UI: per-source override field; otherwise inherits
   the type default.
4. [ ] **Step 4 — U2: Robust corridor tightening (constant default
   σ).**  Tighten the corridor edges inward by $k_\alpha \cdot
   \sigma_\text{const}$ at every horizon step.  Defaults: $k_\alpha
   = 1.96$ (95 %), $\sigma_\text{const} = 0.3$ K.  Implementation
   leaves a hook for the per-step σ to come from Phase 5 forecast
   ensembles without restructuring the OCP.  Acceptance: under
   Monte-Carlo forecast realisations with $\sigma \le 0.3$ K,
   comfort-corridor violation rate ≤ 5 %.  Config UI:
   `corridor_confidence` preset (`relaxed` 90 % / `standard` 95 % /
   `strict` 99 %), defaulted to `standard`; advanced users may set
   `corridor_sigma` directly.
5. [ ] **Step 5 — N2: Warm-start refinement.**  Each cycle's NLP is
   seeded from the previous cycle's solution shifted by one step,
   with a one-shot terminal extrapolation.  Acceptance: median solver
   iteration count drops measurably on the bundled benchmark
   scenarios; per-cycle work re-runs as the `BENCHMARKS.md` regression
   suite.  No config UI change.
6. [x] **Step 6 — W1: Open-window / open-door heater override.**
   High-level handling for rooms whose configured window or door
   binary sensors report `on` for an extended period.  Independent
   of Steps 1–5; can be developed in parallel. **Done — SWD-298 / PR #592 (App port after SWD-262).**

   *Mechanism.*  A small per-room state machine tracks
   `closed → pending_open → open → pending_closed → closed`.
   The room enters the `open` state when *any* configured
   `binary_sensor` has been continuously `on` for at least
   `window_open_debounce` (default 60 s); it leaves `open` and
   enters `pending_closed` when all configured sensors report `off`,
   and returns to `closed` after `window_open_close_settle`
   (default 30 s) without any sensor flipping back to `on`.  This
   hysteresis prevents both brief-opening false triggers (taking
   out the trash) and rapid re-toggling on bouncy contact sensors.

   *Three orthogonal effects while the room is in the `open` state:*
   - **Dispatch-layer override.**  The coordinator clamps
     commanded $u = 0$ for every heat source assigned to the room
     before issuing actuator commands.  The OCP solution from
     step 1 onwards is unaffected — the receding horizon
     re-evaluates next cycle.
   - **Process-noise inflation.**  The affected room's EKF
     process-noise covariance is inflated by
     `window_open_q_inflation` (default 10×) so the filter
     tracks the rapid cooling rather than tripping the Phase 2
     outlier gate.
   - **No plant-model change.**  The model stays blind to the
     open window; per-window air-exchange-rate identification is
     a deferred Phase 1.5 follow-up (see Phase 1's deferred
     items).

   *Multi-sensor rooms* combine their sensors with logical OR.
   Rooms with no configured `window_sensors` keep the existing
   behaviour exactly — the feature is fully opt-in per room.

   *Acceptance:* on a synthetic trace with a 10-minute window
   opening, the heater stops within one cycle of the debounce
   expiring and the EKF state tracks the rapid cooling without
   triggering outlier rejections; a 30-second window opening
   (below debounce) does not trigger the override; on close,
   the override releases within one cycle of the settle expiring
   and normal control resumes; multi-sensor rooms behave as
   logical OR.  README: §4.5 (control cycle) gains a window-
   override subsection; §10 / §11 documents `window_sensors`,
   `window_open_debounce`, `window_open_close_settle`,
   `window_open_q_inflation`.  Tests: state-machine regression
   (debounce, settle, multi-sensor OR, bouncy contact),
   dispatch-override end-to-end test, process-noise-inflation
   effect on EKF tracking test, no-regression test for rooms
   without window sensors.  Config UI: per-room `window_sensors`
   field (list of HA `binary_sensor.*` entity IDs) added to the
   room edit flow; global options-flow gains the three timing
   parameters with safe defaults; new diagnostic sensor
   `window_state` per room exposing the state-machine state
   (`closed` / `pending_open` / `open` / `pending_closed`).

#### Migration

Existing installs are migrated atomically on first start after
upgrade:
- `setpoint` becomes the soft-attractor inside the corridor.
- The corridor is set to
  $[\,\text{setpoint} - \text{turn\_off\_deadband},\,\text{setpoint} + \text{turn\_off\_deadband}\,]$.
- `tariff_entity` is None (flat 1 €/kWh), keeping cost-objective
  behaviour identical to today's dimensionless energy term.
- `corridor_confidence` defaults to `standard` (95 %); the corridor
  tightens by $\approx 0.59$ K.
- Per-source Δu weights inherit type defaults.
- `window_sensors` defaults to an empty list per room (W1 is fully
  opt-in); when no window sensors are configured, dispatch behaviour
  is unchanged from today.  `window_open_debounce` (60 s),
  `window_open_close_settle` (30 s), and `window_open_q_inflation`
  (10×) default at the global level.

The `format_version` field in persisted storage is bumped so a
downgrade cleanly rejects the new layout.  No YAML re-authoring is
required; users see a wider, "looser" controller behaviour out of
the box that, on a flat tariff, matches today's energy-priced
controller within numerical noise.

#### Deferred from Phase 3 (kept on the roadmap)

The items below were considered for Phase 3 and explicitly rejected
because they are heavier structural changes, are gated on capability
from other phases, or are simply premature optimisation given that
the cycle is 10–15 min:

- **Mixed-integer MPC (MILP / MINLP) for on/off equipment.**  Binary
  variables + min on/off-time constraints + branch-and-bound (Bonmin).
  Skipped because the user-impact gap between properly-rounded
  continuous + good dispatch and full MILP is small for residential
  systems, and the MILP solver dependency is non-trivial.  **Revisit
  if** real-world dispatch shows pathological cycling that the
  duty-cycle dispatcher below cannot fix.
- **Duty-cycle dispatcher for on/off heaters.**  An actuator-layer
  overhead that translates the OCP's continuous $u \in [0, 1]$ into
  a temporal on/off pattern within the sample interval (e.g.
  $u = 0.33$ on a 15-min step ⇒ on for 5 min, off for 10).  Currently
  on/off heaters are dispatched by simple threshold; the dispatcher
  is a clean follow-up if that proves coarse.
- **Predictive open-window horizon clamping (W1 follow-up).**
  Instead of just clamping $u_0 = 0$ at dispatch, propagate the
  override over the first $N$ horizon steps based on a learned
  open-duration distribution.  Skipped because the receding-
  horizon controller re-evaluates every cycle and the simpler
  dispatch-override gives acceptable closed-loop behaviour;
  revisit if pre-emptive overshoot at window-close becomes
  visible.
- **Residual-based open-window detector (W1 follow-up).**  Detect
  open windows from the EKF innovation signature alone, without
  requiring a configured binary sensor.  Phase 10 fault-detection
  territory; useful for rooms whose users haven't installed
  contact sensors.
- **Minimum-modulation handling for modulating sources.**
  $u \in \{0\} \cup [u_\text{min}, 1]$ as an explicit OCP constraint.
  Non-convex; would require either MILP or a smoothing relaxation.
  Skipped for now; live with the relaxed continuous solution.
- **Stochastic scenario-tree MPC.**  $K$ forecast realisations, non-
  anticipative control tree.  Skipped because U2's robust corridor
  tightening gives a similar comfort guarantee at a tiny fraction of
  the cost.  Revisit if Phase 5 produces well-calibrated forecast
  ensembles and dynamic-tariff variance becomes large enough to
  warrant scenario decomposition.
- **Lexicographic constraint hierarchy (lex-MPC).**  Sequence of OCPs
  with priorities safety > comfort > cost.  The current weighted-sum
  with hard safety + soft corridor + cost handles infeasibility
  gracefully in practice; revisit only if observed behaviour shows
  premature comfort sacrifice in cost-minimisation regimes.
- **PMV/PPD comfort objective.**  Requires Phase 1's deferred
  latent-heat state (C2) and per-room humidity sensors.  Lands when
  those land.
- **Hierarchical (slow + fast) MPC.**  Two-rate controller.
  Justified once DHW, battery, and EV co-optimisation enter the
  scope (Phase 11); the single-rate Phase 3 OCP is sufficient until
  then.
- **Distributed MPC across zones (ADMM).**  Per-zone subproblems
  with consensus on inter-zone heat-flux.  Justified above ~15 rooms
  or when sub-second cycles are required; revisit when the user base
  shows real-world houses at that scale.
- **Real-Time Iteration scheme and acados migration.**  Structural
  changes to the solver stack.  Deferred to Phase 7 (numerics) so
  the migration happens once across the codebase rather than being
  bolted onto a Phase 3 item.
- **Convexified relaxation for warm cold-start.**  Linear-MPC warm-
  start handoff to the full NMPC on first cycle after restart.
  Skipped because cold starts converge in practice; revisit only if
  observed cold-start failures appear.

---

### 17.5 Phase 4 — System identification & online adaptation

**Why:** Today's identification is offline (`estimate_parameters_ml` button
press), uses a single point estimate, and lacks a strategy for sustained
non-identifiability or seasonal regime shifts.  The upgrades make ID
continuous, prior-aware, and adversarial-resistant.

**Depends on:** Phases 1, 2.  **Unlocks:** Phase 8.

- [ ] **Bayesian MAP identification with informative priors.**  Replace
  the unconstrained NLL minimisation with a MAP objective $\mathcal{L}
  + \log p(\theta)$ using log-normal priors centred on
  building-typology defaults (year built, wall type, square metres).
  Eliminates the degenerate "$C$ huge, $R$ huge" optimum that the
  current loglik landscape sometimes drifts into.
- [ ] **Recursive Bayesian update (RB-Kalman / particle-filter
  parameter sampler).**  Maintain a full posterior over $\theta$
  rather than a point estimate; update once per cycle.  Surfaces
  posterior credible intervals on the dashboard.
- [ ] **Multi-step prediction-error minimisation.**  Optimise the loss
  $\sum_{k=1}^{K} \|y_{t+k} - \hat y_{t+k|t}\|^2$ over open-loop
  rollouts of length $K$ (e.g. $K=12$) instead of one-step Kalman
  innovations.  Aligns the ID objective with the MPC's actual
  prediction horizon.
- [ ] **Active experiment design.**  When parameters are flagged
  non-identifiable, the controller proposes a small comfort-bounded
  perturbation $\delta u$ to maximise Fisher information on the
  problem parameter, with explicit user opt-in.  Acceptance: median
  parameter-confidence ≥ 0.7 within 5 days of install.
- [ ] **Sparse topology identification (SR3 / LASSO on $A$).**  Learn
  which inter-room connections matter from data, automatically
  pruning negligible $1/R_{ij}$ edges and proposing new ones.
  Reduces over-fitting and surfaces unintentional topology errors.
- [ ] **Seasonal regime detection.**  Detect changepoints in the
  innovation statistics (Bayesian online changepoint detection,
  Adams & MacKay 2007) and either re-identify or branch into a
  regime-specific parameter set (heating season / cooling season /
  shoulder season).
- [ ] **Sensitivity-weighted parameter freezing.**  Auto-fix the
  identifiability-flagged parameters at their prior mean during low-
  excitation periods to prevent the optimiser from chasing noise.
- [ ] **Cross-validation framework.**  Train/test split on rolling
  windows; report out-of-sample log-likelihood; refuse to apply a
  re-estimation that degrades out-of-sample fit beyond a threshold.

---

### 17.6 Phase 5 — Disturbance forecasting

**Why:** The MPC is forecast-bounded — better forecasts directly translate
to better control.  Today, outdoor temperature is interpolated from a
weather entity and solar gain is computed from a clear-sky model with no
data assimilation.  These are the next two biggest residual sources after
the plant model.

**Depends on:** Phase 1 (for solar absorptance), Phase 2 (for assimilation).

- [ ] **Clear-sky → cloud-corrected irradiance.**  Combine HA cloud-cover
  forecasts with the Erbs / Reindl decomposition to produce DNI/DHI
  forecasts on cloudy days.  Acceptance: solar-gain RMSE ≤ 30 % of
  midday peak on a year-long trace.
- [ ] **Plane-of-array irradiance assimilation.**  If a measured
  irradiance sensor (or even a PV-generation entity) is available,
  blend it with the clear-sky model via a Kalman correction step on a
  per-window basis.
- [ ] **Multi-source weather ensemble.**  Pull forecasts from $M$
  weather entities (Met.no + ECMWF + DMI + …) and use a recursive
  least-squares forecast-combination layer to produce a single
  posterior temperature trajectory with calibrated variance.  The
  variance feeds the stochastic / chance-constrained MPC.
- [ ] **Occupancy / internal-gain forecasting.**  Hidden Markov model
  over per-room presence sensors + calendar + device_tracker; output
  an expected internal-gain $\hat Q_\text{int}(t)$ trajectory with
  uncertainty.  Replaces the constant $Q_\text{int}$ assumption.
- [ ] **Wind-dependent infiltration forecast.**  Use forecast wind
  speed / direction to drive the Phase 1 infiltration model so the
  controller pre-heats ahead of forecast storms.
- [ ] **Ground-temperature model.**  Sinusoidal annual + diffusion-
  lagged ground temperature for the Phase 1 slab/GSHP coupling.  No
  external data needed.
- [ ] **Price forecasting & residual modelling.**  When a day-ahead
  price (Nord Pool / EPEX) is published, store it; outside that
  window, run a small autoregressive model conditioned on
  day-of-week + season for the look-ahead beyond the public horizon.

---

### 17.7 Phase 6 — Heat-source model fidelity

**Why:** The Carnot-corrected COP and step-modulated heater scale are good
enough for switch-domain electric heaters but miss the dominant cost
non-linearities of real heat pumps (defrost, modulation efficiency curve,
flow-temperature dependence) and modulating boilers (cycling losses,
condensing efficiency cliff).

**Depends on:** Phase 1, telemetry availability.  **Unlocks:** Phase 11.

- [ ] **Variable-speed inverter heat-pump map.**  Replace the Carnot
  scaling with a fitted $(f_\text{comp}, T_\text{out},
  T_\text{flow}) \to (P_\text{elec}, Q_\text{th})$ map.  Identified
  from telemetry (MELCloud, Onecta, NIBE Uplink) or from
  manufacturer NEN-EN 14825 curves where telemetry is absent.
- [ ] **Full water-loop / metal emitter model (Phase 1 B2 follow-up).**
  Replace the pragmatic first-order emitter filter shipped in Phase 1
  with an honest two-state model: water-loop capacitance $C_w^{\text{rad}}$,
  metal capacitance $C_m$, and a flow-temperature input $T_\text{supply}(t)$
  fed from the heat-source side (now available via the inverter
  telemetry bullet above).  Identification jointly fits the emitter UA
  and $C_w^{\text{rad}}, C_m$.  Falls back to the Phase 1 filter when
  supply-temperature telemetry is absent.
- [ ] **Defrost cycle as an explicit input-domain event.**  Detect
  defrost from telemetry or from the residual signature; model it as
  a known transient $-Q_\text{def}$ that the controller can either
  pre-empt or schedule into a low-comfort-impact window.
- [ ] **Modulating gas / oil boiler with cycling-efficiency curve.**
  Steady-state efficiency vs modulation ratio + per-cycle ignition
  loss; condensing/non-condensing transition at the dew-point flow
  temperature.
- [ ] **Buffer-tank / hydraulic-separator dynamics.**  Single-node
  or stratified-node tank between the heat pump and the emitters;
  decouples source modulation from emitter demand.
- [ ] **Ground-source / water-source heat pump.**  Borehole loop
  temperature dynamics (line-source g-function, Eskilson) so the
  controller respects the ground-loop's thermal recovery rate.
- [ ] **Solar-thermal collector model.**  Flat-plate / evacuated-tube
  efficiency $\eta = \eta_0 - a_1 \Delta T / G - a_2 (\Delta T)^2 /
  G$ feeding the buffer tank; jointly scheduled with the PV +
  battery + heat-pump plan.
- [ ] **Hybrid sources with marginal-cost crossover.**  Heat-pump +
  boiler, heat-pump + electric immersion, district + boiler:
  marginal-cost (€/kWh-thermal) is computed each cycle and the MPC
  binary-selects the cheaper source within the equipment
  constraints from Phase 3.

---

### 17.8 Phase 7 — Solver, numerics, performance

**Why:** Closing the gap between "5 s solve on 5 rooms" and "sub-second
solve on 15 rooms with mixed-integer dynamics and stochastic scenarios"
requires moving off the current Python-loop NLP build and onto a sparse,
JIT-compiled symbolic stack.

**Depends on:** Phases 3 and 4 to know the final problem structure.

- [ ] **CasADi symbolic OCP build.**  Replace the hand-rolled NLP
  assembly with a CasADi graph; gives free analytic Jacobians +
  Hessians + sparsity patterns.  Persist the compiled function
  cache between HA restarts.
- [ ] **acados backend (multiple-shooting + RTI).**  Migrate the
  short-horizon lower-layer MPC to acados for sub-100 ms solves with
  warm-start.  IPOPT remains the long-horizon upper-layer solver.
- [ ] **Sparse Hessian exploitation.**  The OCP is block-tridiagonal
  in time; expose the structure to the QP backend (HPIPM, qpOASES)
  for $O(N)$ rather than $O(N^3)$ solves.
- [ ] **JIT-compiled EKF/UKF inner loop (numba / cython).**  The
  Euler-sub-stepped covariance propagation is the per-cycle hot path
  for the estimator; pre-compile per house topology.
- [ ] **Parallel per-house EKFs for the parameter-likelihood grid.**
  `compute_loglik_slice` and the multi-start ID search are
  embarrassingly parallel; use `concurrent.futures` with a worker
  pool sized to the host's CPU count.
- [ ] **Deterministic regression harness.**  Seeded synthetic
  traces, recorded numeric outputs of every solve, golden snapshots
  in CI to catch any silent change in optimiser behaviour across
  releases.

---

### 17.9 Phase 8 — Verification, validation, benchmarking

**Why:** Each upgrade above is a hypothesis ("this estimator is better",
"this objective saves cost") and needs to be checked against the
alternatives on a common benchmark.  Without this, the codebase drifts.

**Depends on:** Phases 1–7 incrementally; itself blocks confident release
of Phases 9–11.

- [ ] **Hardware-in-the-loop simulator harness.**  Bind a high-fidelity
  reference simulator (e.g. EnergyPlus FMU or a Modelica IDEAS
  package) as the "true plant" and run the integration against it
  through HA's regular event loop.  Captures dynamics that the RC
  abstraction must approximate.
- [ ] **Year-long golden trace library.**  Bundle a handful of
  recorded year-long real-house traces (weather + setpoints + sensor
  + controller decisions) and assert closed-loop KPIs stay within
  bands across releases.
- [ ] **Monte-Carlo robustness assessment.**  Sample $N=1000$
  parameter / forecast realisations from their identified
  posteriors; report a comfort-violation probability and a 95-%
  cost envelope for each release.
- [ ] **KPI suite.**  Standardise on: (1) RMSE of one-step prediction,
  (2) open-loop $K$-step RMSE, (3) ITAE on setpoint tracking,
  (4) total kWh per HDD, (5) total € per HDD, (6) compressor
  cycles per day, (7) % time in comfort band.  Surface every KPI in
  the dashboard and in CI.
- [ ] **A/B controller comparison framework.**  Run two controller
  variants over the same trace with synchronised seeds and produce
  a paired-test report.  Used to gate Phase-3/4/10 changes.
- [ ] **Formal comfort guarantee under tube MPC.**  Prove (or
  numerically certify via reachable-set propagation) that the
  Phase-3 tube MPC keeps the temperature in the comfort band under
  the identified disturbance bounds.

---

### 17.10 Phase 9 — Learning-augmented control (grey-box / hybrid)

**Why:** After Phase 1 there will still be a residual the physics doesn't
capture (occupant heat patterns, infiltration micro-pathways,
unidentified emitters).  A small learned component can absorb that
residual while the physics core keeps the system safe, sample-efficient,
and explainable.

**Depends on:** Phases 1, 2, 4, 8.  **Unlocks:** later RL work.

- [ ] **Residual neural-network drift term.**  Write the dynamics as
  $\dot x = f_\text{phys}(x,u,d;\theta) + g_\phi(x,u,d)$ with
  $g_\phi$ a small MLP (∼10–100 parameters).  Train $\phi$ jointly
  with $\theta$ under a weight prior $\|\phi\|^2$ that pushes the
  residual toward zero when the physics is enough.  Falls back to
  pure physics when $\|g_\phi\| > \tau$ on out-of-distribution
  inputs.
- [ ] **Neural-ODE option for the room drift.**  When the residual
  is non-negligible, swap $f_\text{phys}+g_\phi$ for a parameterised
  Neural-ODE; preserves the continuous-discrete framework and
  remains compatible with the existing EKF / NMPC.
- [ ] **Differentiable simulator.**  Expose $f_\text{phys}+g_\phi$
  as a JAX / PyTorch module so that gradients through year-long
  rollouts are available for design questions ("what insulation
  upgrade saves the most?") and for end-to-end ID.
- [ ] **MPC-policy distillation.**  Train a feed-forward policy
  $\pi_\psi(x,d) \to u$ on offline MPC rollouts (behaviour cloning
  + DAgger).  Run at every cycle for sub-millisecond decisions and
  fall back to the full MPC whenever $\|u_\text{policy} -
  u_\text{MPC,1-step}\| > \tau$.
- [ ] **Safety-filtered RL fine-tuning.**  Optional online RL
  improvement layer with the MPC as a safety filter (Hewing 2020):
  the RL proposes $u_\text{RL}$, the MPC projects it onto the
  constraint-admissible set, and only the projected action is
  applied.  Provably keeps the safety guarantee while enabling
  exploration.
- [ ] **Bayesian-optimisation auto-tuner for MPC weights.**  Treat
  $(Q_\text{track}, R_\text{energy}, S_\Delta u, \rho_z, N)$ as a
  black-box hyperparameter vector and run BO over the closed-loop
  KPI on the HiL harness.

---

### 17.11 Phase 10 — Fault & anomaly detection

**Why:** A well-instrumented controller knows when *itself* is wrong.
Today the only signal is `prediction_error` per room; we can do much
more with the residuals the estimator already produces.

**Depends on:** Phase 2 (rejection events as a real-time fault signal)
and Phase 8 (residual statistics from the validation harness).

- [ ] **CUSUM / Page–Hinkley change detector on innovations.**  Per-
  room and per-source detectors that trigger a Repairs issue when
  the residual mean drifts.
- [ ] **Generalised likelihood-ratio (GLR) test for COP drop.**
  Compare a "nominal Carnot" hypothesis against a "scaled-Carnot
  with factor $\eta < 1$" hypothesis on the heat-pump residuals;
  flag refrigerant-leak / fouled-coil candidates with a $p$-value.
- [ ] **Sensor-bias / stuck-at detector.**  The Phase-2 per-sensor
  bias state, combined with a flatline detector and a Δ-out-of-band
  detector, classifies each sensor as healthy / drifting / stuck /
  unplugged.
- [ ] **Actuator-stuck detector.**  Cross-correlate commanded $u$
  with downstream power telemetry (or temperature response in the
  absence of telemetry) to detect a valve that no longer responds.
- [ ] **Window-open / door-open detector from residual signature.**
  Sudden negative-step infiltration spike with characteristic decay
  is recognisable even without a contact sensor; surfaced as a
  diagnostic event and used to clamp $u$.
- [ ] **Forecast-quality monitor.**  Track the prediction skill of
  the weather entity (mean absolute error vs the realised
  temperature) and down-weight or fail over to a backup forecast
  source when skill drops.

---

### 17.12 Phase 11 — Whole-home energy co-optimisation

**Why:** With Phases 1–10 in place, the controller becomes the obvious
optimal scheduler for *all* low-rate flexible loads in the home — not just
heaters.  This phase widens the OCP to the whole house energy budget.

**Depends on:** Phases 3, 6, 8.

- [ ] **DHW tank as an additional thermal node in the OCP.**  Co-
  optimise reheat windows with the space-heating plan, respecting
  legionella minimums and draw-pattern forecasts.
- [ ] **PV + battery + house-thermal-mass joint MPC.**  Battery SOC
  enters the state vector; the OCP decides, for every cycle, whether
  to charge the battery, dump PV into the DHW tank, or pre-heat
  rooms.
- [ ] **EV charger as a flexible load.**  Charge-by-time constraint
  + max-import constraint + dynamic tariff; the EV charge plan and
  the heating plan share the household import budget so the main
  fuse never trips.
- [ ] **Demand-response signal compliance.**  React to a published
  DR signal (OpenADR / Tibber Pulse / EDS DK1) by tightening the
  energy budget for the affected window; the comfort corridor
  widens, the cost objective dominates.
- [ ] **Multi-vector cost objective.**  €(t) for electricity, gas, and
  district heating combined into a single marginal-cost field; the
  Phase-6 hybrid-source switch is now a decision the OCP makes
  every minute.
- [ ] **Grid-import peak-shaving constraint.**  Hard $P_\text{import}
  \le P_\text{contract}$ inequality + soft peak-tariff
  ($\max_t P$) penalty; relevant in countries with kW-band tariffs
  (NL, DK, partly DE).

---

### 17.13 Sequencing & dependency diagram

```
   Main model-fidelity chain                Independent robustness track
   ─────────────────────────                ─────────────────────────────

   ┌────────────────────────────┐           ┌──────────────────────────┐
   │ Phase 1 — Plant model      │           │ Phase 2 — Outlier         │
   │ (2R2C + slab + UFH +       │           │ rejection (predictive-    │
   │ sol-air + infiltration +   │           │ likelihood gate on the    │
   │ implicit Euler — the new   │           │ existing CD-EKF)          │
   │ default per-room model)    │           └──────────────┬────────────┘
   └──┬─────────────┬─────────┬─┘                          │ rejection
      │             │         │                            │ events
      ▼             ▼         ▼                            │
   ┌──────────┐ ┌────────┐ ┌──────────┐                    │
   │ Phase 5  │ │ Phase 3│ │ Phase 4  │                    │
   │ Forecasts│ │ OCP    │ │ System ID│                    │
   │ (cloud,  │ │ (MILP, │ │(Bayesian,│                    │
   │ occ.,    │ │ RTI,   │ │ multi-   │                    │
   │ wind,    │ │ scen., │ │ step,    │                    │
   │ ground)  │ │ PMV)   │ │ regime)  │                    │
   └─────┬────┘ └────┬───┘ └─────┬────┘                    │
         │           │            │                        │
         │assimilate │richer u   │posterior θ              │
         └─────┬─────┴────────────┘                        │
               ▼                                           │
    ┌──────────────────────────────────┐                   │
    │ Phase 6 — Heat-source models     │                   │
    │ (variable-speed HP, defrost,     │                   │
    │ boiler cycling, GSHP, buffer     │                   │
    │ tank, solar thermal, full water- │                   │
    │ loop emitter)                    │                   │
    └─────────────────┬────────────────┘                   │
                      │realistic u→Q                       │
                      ▼                                    │
    ┌──────────────────────────────────┐                   │
    │ Phase 7 — Solver / numerics      │                   │
    │ (CasADi, acados, sparse Hessians)│                   │
    └─────────────────┬────────────────┘                   │
                      │sub-second cycles                   │
                      ▼                                    │
    ┌──────────────────────────────────┐                   │
    │ Phase 8 — V&V (HiL, golden       │                   │
    │ traces, Monte-Carlo, KPIs,       │                   │
    │ certification)                   │                   │
    └─────┬──────────────────────┬─────┘                   │
          │                      │                         │
          ▼                      ▼                         ▼
    ┌─────────────────┐  ┌───────────────────────────────────────┐
    │ Phase 9 —       │  │ Phase 10 — Fault & anomaly detection  │
    │ Learning-       │  │ (CUSUM/Page-Hinkley, GLR for COP      │
    │ augmented       │  │ drop, actuator-stuck, forecast-quality│
    │ control         │  │ monitor — consumes Phase 2 rejection  │
    │ (residual NN,   │  │ events + Phase 8 residual stats)      │
    │ RL distill)     │  └────────────────────┬──────────────────┘
    └────────┬────────┘                       │
             │                                │
             └──────────────┬─────────────────┘
                            ▼
              ┌──────────────────────────────┐
              │ Phase 11 — Whole-home        │
              │ co-optimisation (DHW, PV,    │
              │ battery, EV, DR)             │
              └──────────────────────────────┘
```

- **Phase 1 is the single blocker** for the model-fidelity chain
  (Phases 3, 4, 5, 6, 7) and for the validation that gates the later
  phases (Phase 8).  Inside Phase 1 the steps are themselves
  sequenced (N1 → C1 → A1 → A2+B1 → B2 → finishing pass).
- **Phase 2 is independent.** It only consumes $\hat y^-$ and $S$ from
  the existing CD-EKF and can ship before, alongside, or after Phase 1.
  Its rejection events feed Phase 10's longer-term fault diagnosis.
- **Phases 3, 4, 5 can be developed in parallel** once Phase 1 lands;
  they converge into Phase 6.
- **Phase 8 (validation) runs alongside every later phase** and is the
  gate for Phases 9–11.

---

### 17.14 Distribution & non-functional (one-liners)

These items are tracked for completeness but are *not* part of the
technical roadmap above.  They unblock adoption rather than capability.

- HACS publication and Home Assistant Core integration submission once
  the v2 surface is stable.
- Translated UI for the major HA locales.
- `mypy --strict` coverage and `pytest` coverage ≥ 90 % gated in CI.
- Explainability dashboard card decomposing each control action into
  the tracking / energy / smoothing / constraint cost contributions.
- Reversible / audit-logged user actions for every parameter, schedule,
  and reset.

Ideas, votes, and contributions are very welcome — open an issue or a PR
on [GitHub](https://github.com/marcuskrogh/HeatingAssistant) to discuss
any of the above.

---
