# Room Model Review & Upgrade Plan

*June 2026 — review of the room thermal model in response to inconsistent
multi-hour prediction quality during price-driven anticipatory heating.*

## 0. Executive summary

The reported symptom — anticipatory preheating during cheap hours that the
plant later "pays back" with aggressive high-price heating to recover comfort
constraints — is a **multi-hour open-loop prediction error**, and the review
traced it to three causes, in order of expected impact:

1. **Structural: the 1R1C room model cannot represent thermal storage shifting.**
   A single node has one time constant; real rooms have a fast air response
   (0.5–1.5 h) and a slow envelope/slab response (5–30 h).  Preheating stores
   energy in the envelope and returns it later — exactly the physics the price
   optimiser is trying to exploit, and exactly what one (R, C) pair cannot
   capture.  The 2R2C model is the right fix, but it was tried before
   (Phase 1 A1/A2) and reverted in PR #124; §3 Phase C explains what must be
   done differently this time, with observability as a first-class design
   constraint rather than an afterthought.
2. **The solar gain *magnitude* is a pure prior — it is never identified from
   data.**  SHGC is fixed at 0.6, exposure presets are coarse (1/3/6 m²·SHGC),
   and shading/curtains/trees are unmodelled.  Because the model-computed solar
   gain is recorded into the history buffer and then treated as a *known*
   disturbance by both the EKF and the parameter estimator, any aperture error
   (±50 % is normal) biases sunny-day predictions directly **and** corrupts the
   C/R_ext/q_int estimates that absorb its average.  This is the cheapest,
   lowest-risk fix on the list (§3 Phase A).
3. **Horizon-disturbance handling gaps**: wind is frozen at its current value
   over the whole MPC horizon, the online internal-gain deviation Δg is frozen
   rather than decayed, and the fallback cloud attenuation is applied equally
   to beam and diffuse (§3 Phase B).

Verdict per area reviewed:

| Area | State | Notes |
|---|---|---|
| Solar geometry & decomposition | **Good** | Sun position, Erbs GHI→DNI/DHI, isotropic transposition, Kasten–Czeplak fallback all correct and well-factored |
| Solar magnitude | **Not satisfactory** | No identified per-room scale; no albedo/ground-reflected term; no incidence-angle modifier |
| Outdoor temperature influence | **Good** | Identified R_ext, SG infiltration overlay, sky/bridge conductances, forecast interpolation, L-stable integration |
| Horizon disturbances | **Adequate, improvable** | Wind constant over horizon; Δg frozen over horizon |
| Model structure (1R1C) | **Not satisfactory for this use case** | Single time constant cannot represent preheat storage/payback; upgrade to 2R2C recommended with the safeguards in §3 Phase C |
| Identification machinery | **Good** | CD-EKF PED ML, analytic sensitivities, IPOPT multistart, identifiability gates, log-space + priors |
| Diagnostics/observability tooling | **Good foundation** | Open-loop RMSE, innovation/ACF sensors exist; needs 2R2C-specific additions (§3 Phase C3) |

---

## 1. What was reviewed

* `thermal_model.py` — `Room` / `HouseModel` (1R1C network, infiltration
  overlay, sky/sol-air/bridge corrections, implicit-Euler stepping)
* `solar_model.py` — solar position, clear-sky model, Erbs decomposition,
  transposition, cloud attenuation, window/exposure gain paths
* `controller.py` — `HouseThermalSDE` (the model the EKF and MPC actually
  use: state layout, G_d disturbance coupling, emitter filter, Δg gain state,
  SG overlay) and the horizon disturbance-sequence construction
* `parameter_estimator.py` — θ layout, identifiability gates, objective
* `sysid.py`, `model_diagnostics.py` — replay/validation tooling
* `coordinator.py` — disturbance pipeline (cloud EMA, GHI forecast, history
  buffer records)
* History: PR #124 ("Revert thermal model from 2R2C+slab back to 1R1C"),
  README §17 Phase 1 plan

## 2. Findings in detail

### 2.1 Solar gain model

**Sound parts.**  The geometry pipeline is textbook-correct and cleanly
factored: Spencer equation of time, Cooper declination, Kasten–Young air mass,
ASHRAE clear-sky DNI; the Erbs diffuse-fraction decomposition makes the
forecast-GHI path geometry-only (no transmittance assumption); transposition
is shared between both intensity sources; the cloud EMA
(`CLOUD_SMOOTHING_TAU_S = 1800 s`) avoids step-shaped attenuation from the
coarse weather attribute.  Precedence (forecast GHI → cloud-attenuated
clear-sky → clear-sky) is sensible and observable via `solar_source`.

**Gap S1 — magnitude is never identified (high impact).**
The estimation parameter vector is

```
θ = [log C (n), log R_ext (n), q_int (n), log α_heaters (*), log R_ij (*)]
```

— there is no solar term.  The room's collecting aperture comes entirely from
configuration (windows × SHGC 0.6, or the `low/medium/high` preset = 1/3/6
m²·SHGC).  Coordinator records `d_solar = self.solar_gains` (the *model
output*) into the history buffer, and `sysid.py` / `parameter_estimator.py`
feed it back as a known disturbance.  Consequences:

* A mis-scaled aperture produces a temperature-prediction error proportional
  to insolation — large on clear days, zero at night — matching the "bad at
  times" symptom.
* The constant `q_int` and the OU gain state Δg absorb the *average* error,
  silently biasing C and R_ext (the estimator trades them off against the
  wrong heat input), so the error contaminates night-time predictions too.

Unlike SHGC or shading, the *product* (effective aperture per room) is well
identified from data: solar gain is a strongly excited input with a distinct
diurnal signature that is not collinear with heater power or outdoor
temperature over a multi-day window.

**Gap S2 — no ground-reflected (albedo) component.**
`transpose_to_surface` is beam + isotropic sky only.  For vertical windows
the missing isotropic ground term is `ρ · GHI · (1 − cos 90°)/2 = ρ · GHI/2`.
With grass (ρ ≈ 0.2) that is ~10 % of GHI; with snow cover (ρ ≈ 0.7–0.8) it
is a *dominant* term at Nordic winter sun angles — a systematic winter
under-prediction of solar gain precisely in the heating season.

**Gap S3 — no incidence-angle modifier.**
SHGC is applied as a constant; real glazing transmittance falls off steeply
above ~50–60° incidence.  The model therefore over-predicts beam gain at
grazing incidence (low sun on east/west windows, high summer sun on south
windows).

**Gap S4 — fallback cloud attenuation applied equally to DNI and DHI.**
Acknowledged in the code comment.  Under partial cloud, beam collapses much
faster than diffuse, so south-facing rooms get over-predicted directional
gain on partly-cloudy days when no GHI forecast is configured.  Cleanest fix:
compute the cloud-attenuated GHI (clear-sky GHI × Kasten–Czeplak) and push it
through the *existing* `ghi_to_dni_dhi` Erbs path, so both intensity sources
share one decomposition and the special case disappears.

(Second-order, not recommended now: Hay–Davies circumsolar diffuse; per-window
horizon shading.)

### 2.2 Outside temperature influence

This part is in good shape:

* R_ext is identified per room with sensible bounds and priors; the outdoor
  coupling correctly bundles conduction + sky-radiative UA + thermal bridge
  in both `HouseModel` and `HouseThermalSDE` (single source of truth via
  `model._B_ext`).
* The Sherman–Grimsrud overlay is correctly formulated as a *delta* around
  typical conditions so the identified R_ext keeps its meaning, and it
  degrades to a no-op without wind data.
* Outdoor forecast is interpolated to the horizon; implicit Euler keeps the
  integration unconditionally stable.

Improvable:

* **W1 — wind frozen over the horizon.**  `set_wind_speed` holds one value
  for all OCP steps although `HouseModel.predict` already accepts per-step
  wind and the weather entity provides a wind forecast.  A frontal passage
  inside the horizon (common at the 12–24 h horizons used for price
  anticipation) shifts the effective UA by 10–30 % in a leaky envelope.
* **W2 — infiltration fraction is a fixed preset**, never identified; a wrong
  tightness preset scales the leakage area and hence the entire wind
  sensitivity.  Low priority: identify only if residual-vs-wind correlation
  stays after W1.
* **W3 — ΔT_sky is a constant 6 K** irrespective of cloud cover (effective
  only when `sky_radiative_ua > 0`, default off).  If/when enabled, overcast
  nights are over-cooled; scale by (1 − cloud_cover) — the cloud signal is
  already in the coordinator.

### 2.3 Model structure — why 1R1C breaks anticipation specifically

The current per-room model is a single node:  `C dT/dt = Q + (T_out − T)/R`.
The MPC's economic value comes from *storing* cheap energy and *releasing* it
in expensive hours.  In a 1R1C world the only storage is the single C at the
single T — so the model believes:

* during preheat, all injected energy shows up as immediate room-temperature
  rise (over-predicting how warm the room gets, hitting the comfort ceiling
  too early in the plan), and
* after preheat, the temperature decays with the single τ = RC (mis-predicting
  the coast-down — the envelope's slow heat return doesn't exist in the
  model).

Whichever way the identified (R, C) is biased by the excitation mix, the
multi-hour open-loop trajectory drifts, the comfort floor is crossed earlier
or later than predicted, and the controller reacts with exactly the
"aggressive correction at high price" the user observes.  Residual
fingerprints to confirm on live data (all already exposed by existing
diagnostics): lag-1 residual autocorrelation well above the white-noise band,
open-loop RMSE growing super-linearly with horizon, innovation sign flipping
between heat-up and coast-down segments.

The B2 emitter filter does **not** substitute: φ lags the *commanded power*
into the node, it does not store and later release energy to the room.

**Recommendation: reintroduce 2R2C — but engineered around the reason the
first attempt failed.**  PR #124 reverted a 3-state (air/wall/slab) model
that was default-on for every room.  The failure mode is predictable in
hindsight: with only one measured output per room (T_air), one slow hidden
node per room whose parameters enter the likelihood as a strongly-correlated
(C_w, R_aw, R_we) triple ("banana" likelihood — the README's own Phase 1
acceptance criterion mentions it), plus optional bias and gain states
competing for the same slow residual, the estimator was solving a borderline
unidentifiable problem on whatever excitation happened to be in the buffer.
§3 Phase C lays out the redo plan.

### 2.4 Estimation & observability — current state

* Live controller builds *both* the EKF model and the control model with
  `augment_offsets=False` (controller.py:2001, 2011); the measurement-bias
  block exists only in code paths that opt in.  Good — fewer hidden slow
  states competing per measurement.
* The OU internal-gain state Δg (optional) is estimated by the EKF and
  **frozen** at its current value over the control horizon
  (`_fixed_gain_dev`).  A transient unmodeled gain (sun through an
  unmodelled window, oven, party) is thus extrapolated unchanged across a
  12–24 h plan even though the model itself says it mean-reverts with κ.
* The identification objective combines one-step PED with multi-step
  open-loop windows (`_max_window_steps = 48` ≈ 12 h at 900 s) — the right
  call for slow parameters; keep it for 2R2C.
* Docs nit: `MODEL_FIT_GUIDE.md` describes open-loop segments as
  "30 steps = 30 min at 60 s/step"; the shipped default sampling is 900 s,
  so the same 30 steps are 7.5 h.  Update the guide when touching Phase D.

---

## 3. Upgrade plan

Ordered so each phase delivers value on its own and de-risks the next.
Phases A and B are low-risk and address the solar/outdoor verification gaps;
Phase C is the structural 2R2C upgrade.

### Phase A — Solar magnitude identifiability (do first)

* **A1. Per-room solar scale in θ.**  Append `log s_i` (prior `log 1`, bounds
  ≈ [log 0.2, log 3]) to the parameter vector; multiply the solar disturbance
  channel (`G_d[i, 1+i]` in `HouseThermalSDE`, solar dispatch in
  `HouseModel.step`) by `s_i`.  Gate identifiability like α/R_ij: require
  sufficient daytime excitation (e.g. `std(d_solar_i) > threshold` and a
  minimum number of daylight samples in the window).  Surface `s_i` on the
  parameter-confidence sensor; a converged `s_i` far from 1 is itself a
  useful config-quality diagnostic ("your window list / preset is off by ×s").
  *Acceptance:* sunny-day open-loop RMSE drops; residual-vs-`d_solar`
  correlation → within ±0.05; night-time RMSE does not regress (guards
  against C/R re-biasing).
* **A2. Ground-reflected irradiance.**  Add `ρ · GHI · (1 − cos tilt)/2` to
  `transpose_to_surface` (needs GHI, which both intensity paths already
  have).  Config: site-level albedo, default 0.2, with an optional
  snow-albedo override (manual toggle or driven by a snow/condition signal
  later).
* **A3. Incidence-angle modifier** on the beam term, e.g. the ASHRAE form
  `IAM = 1 − b₀(1/cos θ − 1)` with b₀ ≈ 0.1 for double glazing, clamped at 0.
  Three lines in `transpose_to_surface`'s beam branch.
* **A4. Unify cloud fallback through Erbs.**  Replace the dual-attenuation
  fallback with: cloudy GHI = clear-sky GHI × Kasten–Czeplak →
  `ghi_to_dni_dhi`.  Removes the acknowledged DNI/DHI-equal-attenuation
  approximation and deletes a special case.

### Phase B — Horizon disturbance handling

* **B1. Per-step wind over the horizon.**  Parse the wind forecast from the
  weather entity (same plumbing as cloud forecast), feed per-step values into
  the OCP/EKF prediction (the SG overlay already recomputes from `(v, ΔT)`
  per evaluation; replace the scalar `_wind_speed` with a step-indexed
  lookup).
* **B2. Decay Δg over the horizon.**  In the control model use
  `Δg(t_k) = Δg₀ · exp(−κ t_k)` instead of the frozen value — consistent with
  the OU model the EKF assumes, one line per step, prevents transient
  unmodeled gains from biasing the tail of a long plan.
* **B3. Cloud-dependent sky depression** (only meaningful when
  `sky_radiative_ua > 0`): `ΔT_sky,eff = ΔT_sky · (1 − cloud_cover)`.

### Phase C — 2R2C reintroduction (observability-first)

**Structure.**  Per room: fast air node `T_a` (C_a) and slow mass node `T_w`
(C_w), coupled by `R_aw`; `T_w` conducts to outdoors via `R_we`.  Inter-room
connections stay mass-to-mass as in the original design.  UFH rooms route
emitter output into `T_w` (subsumes the old slab node — **two states per
room, not three**; the separate slab node returns only if data demands it).
Solar splits between nodes with a *fixed* fraction (e.g. 0.6 air / 0.4 mass)
— do not identify the split initially.

**C1. Reparametrise for identifiability.**  This is the main difference from
the reverted attempt.  Do not put raw `(C_a, C_w, R_aw, R_we)` in θ; their
likelihood is a curved ridge because the data constrain combinations, not
coordinates.  Identify instead the near-orthogonal set:

| Parameter | What pins it down |
|---|---|
| `UA_tot = 1/(R_aw + R_we)` | steady-state heat balance (same information that pins 1R1C R_ext — strongly identified) |
| `C_tot = C_a + C_w` | long-horizon energy storage (multi-hour windows) |
| `τ_fast` (air-node time constant) | heater step responses — strongly excited by normal MPC action |
| `f_split = C_a/C_tot`, `r_split = R_aw/(R_aw+R_we)` | weakly identified → bounded (e.g. f ∈ [0.02, 0.3]) with **tight typology priors**; recover raw parameters algebraically |

The existing log-space + Gaussian-prior machinery applies unchanged; only the
pack/unpack layer and the analytic sensitivity chain rule are new.

**C2. Per-room gating with automatic fallback.**  Fit 1R1C and 2R2C per room
on the same window; promote a room to 2R2C only when *both*:
(a) multi-hour open-loop RMSE (4 h and 12 h horizons) improves by ≥ 20–30 %,
and (b) the slow-parameter posterior (Laplace approximation from the existing
forward-sensitivity Fisher information) tightens below a threshold.
Demote back to 1R1C if the posterior re-inflates or the EKF's `T_w` variance
stops contracting.  The state-vector machinery must therefore support *mixed*
households (some rooms 1-state, some 2-state) — this also keeps the QP small.

**C3. Observability instrumentation (the "how we know it's working" layer).**

* **Per-room observability metric**: condition number (or smallest singular
  value) of the discrete observability Gramian of the linearised 2-state room
  subsystem, computed at each estimation run and each EKF cycle re-linearisation;
  exposed as a diagnostic sensor (`..._wall_state_observability`) with
  documented green/yellow/red bands.
* **`T_w` posterior std sensor** per 2R2C room; alert automation example in
  docs ("wall-state uncertainty not contracting → demotion imminent").
* **Hidden-state budget rule, enforced in code**: when a room runs 2R2C, that
  room's measurement-bias state stays disabled and the Δg gain state is
  disabled *during identification* (re-enabled for runtime EKF only after the
  2R2C fit converges).  One measured output cannot fund three slow hidden
  states; today this exclusivity is implicit, make it explicit.
* **Optional direct observation of `T_w`** (single highest-value mitigation):
  per-room optional `mass_temp_entity` (a cheap surface/floor sensor).  When
  configured, extend that room's measurement to `[T_a, T_w]` — observability
  stops being a reconstruction problem entirely.  Design the measurement
  masking in from day one (the CD-EKF already supports per-channel masks).

**C4. Excitation.**  The slow mode is identifiable only across heat-up/
coast-down cycles.  Extend the identification window for 2R2C candidates to
≥ 48–72 h of active samples, and require at least one preheat/coast event in
the window (detectable from the recorded `u`).  Optional later: an
"identification burn" service that schedules a deliberate gentle preheat on a
cheap-price day — the price optimiser already creates these events naturally,
which is convenient: the regime we need the model for is the regime that
identifies it.

**C5. Migration & state init.**  Initialise `T_w` at its steady-state value
given current `T_a` and the trailing 24 h mean outdoor temperature.  Keep the
room's 1R1C parameters persisted as the fallback set (the `Room` constructor
already tolerates the 2R2C kwargs, and PR #124 deliberately kept call-site
compatibility, so the config-schema groundwork exists).  Bump the persisted
`format_version`.

### Phase D — Validate against the actual objective

* Extend the open-loop diagnostics to report RMSE at 4 h / 12 h / 24 h
  horizons (not just one segment length), and segment the statistics over
  *preheat events* vs quiescent periods.
* Add two KPIs that map 1:1 to the complaint: **post-preheat comfort debt**
  (°C·h below the comfort floor within N hours after a preheat decision) and
  **price regret** (kWh bought in top-quartile price hours as a fraction of
  total).  These, not global RMSE, are the success criteria for Phases A–C.
* Fix the stale 60 s-sampling wording in `MODEL_FIT_GUIDE.md`.

## 5. Implementation status (this branch)

All four phases are implemented, with the simplifications requested in
review (keep the model simple and clear; **no slab node** — a slab may
return later if underfloor heating with significant lag is introduced):

| Item | Status | Notes |
|---|---|---|
| A1 per-room solar scale in θ | ✅ | Gated on solar excitation (std ≥ 30 W); analytic gradient; persisted; FD-verified |
| A2 ground-reflected irradiance | ✅ | Site-level `ground_albedo` (default 0.2) |
| A3 incidence-angle modifier | ✅ | ASHRAE form, b₀ = 0.1, beam term only |
| A4 unified cloud fallback | ✅ | All intensity sources supply a GHI; the Erbs correlation does the split everywhere — exact continuity at zero cloud, exact Kasten–Czeplak GHI factor, beam collapses under cloud |
| B1 per-step wind over the horizon | ✅ | Forecast parsed from the weather entity; per-step in the prediction rollout, horizon-mean in the QP linearisation |
| B2 Δg decay over the horizon | ✅ | OU decay in both the QP disturbance forecast (as a decrement from the linearisation point) and the rollout |
| B3 cloud-scaled ΔT_sky | ✅ | `set_cloud_cover` on model and SDE; only active when `sky_radiative_ua > 0` |
| C 2R2C (air + wall per room) | ✅ | Two nodes, no slab.  Splits parametrised as bounded fractions of the unchanged user-facing (C, R_ext) so steady state and existing configs are preserved exactly; collapse limit ≡ 1R1C |
| C1 reparametrisation | ✅ simplified | Instead of the (UA_tot, C_tot, τ_fast, …) coordinate change, θ keeps the legacy (log C, log R, q_int, α, R_ij) blocks unchanged and appends bounded split fractions with tight priors (σ = 0.1) — same identifiability protection, far less machinery |
| C2 per-room gating + fallback | ✅ simplified | Splits enter θ only for rooms with an excited heat source; ungated rooms keep typology defaults (= the 1R1C-equivalent behaviour).  Fallback is by prior shrinkage rather than a dual-fit comparison |
| C3 observability diagnostics | ✅ | Per-room wall-temperature sensor (EKF estimate) with `posterior_std` and `observability` (Gramian conditioning) attributes; honest (4 K²) initial wall variance in the EKF |
| C3 optional T_w sensor | ⏸ deferred | Highest-value mitigation if reconstruction proves weak in practice; the measurement-mask plumbing in the CD-EKF already supports it |
| C5 migration | ✅ | Wall states initialise at their (T_a, T_out) equilibrium; persisted snapshots gain `solar_scale` / split keys; old snapshots load unchanged |
| D multi-horizon open-loop RMSE | ✅ | `run_open_loop_simulation` reports `rmse_by_horizon` at ~4/12/24 h; surfaced on the Open-Loop RMSE sensor |
| D preheat-segmented KPIs (comfort debt, price regret) | ⏸ deferred | Needs price-series bookkeeping; follow-up once a few weeks of 2R2C operation exist to baseline against |

Two pre-existing defects were found and fixed along the way:

* the estimator's analytic-gradient pass silently degraded to a **zero
  gradient** whenever the model state grew beyond n (the
  `_skip_analytic_grad` gate) — very plausibly the mechanism that made the
  first 2R2C attempt unidentifiable; the sensitivities are now exact for
  the full 2R2C θ (verified against finite differences to ~10⁻⁶), and
* HiGHS's QP solver (≤ 1.14) returns "Solve error" on well-posed QPs with
  unbounded slack columns; the backend now boxes slack variables at 1e10,
  which fixed an MPC fallback-to-zero on large comfort violations.

### Explicitly out of scope (and why)

* **3-state air/wall/slab for every room** — that is the shape of the
  reverted attempt; the slab's role is covered by routing UFH into `T_w`.
* **Per-window SHGC identification** — the data support one scale per room,
  no more.
* **Hay–Davies / Perez diffuse, horizon shading** — second-order until A1–A4
  and Phase C land and the residuals say otherwise.
