# Research brief: Why 2R2C parameter estimation is poor, and what the literature applies

## Question

Why does current 2R2C parameter estimation from cheap indoor air-temperature
sensors perform poorly (implementation, unmeasured wall-node \(T_w\) during
fitting, identifiability / excitation, data windows), and what methods might
we apply to improve effectiveness — without changing the model family or adding
sensors beyond indoor climate (temperature / humidity)?

## Axes covered

| Axis | Status | Notes |
|------|--------|-------|
| Preprints (arXiv) | covered | Radecki & Hencey (constrained-load learning, self-excitation); fallback Atom/abs pages (no `scripts/arxiv_research.py` in this tree) |
| Formal written | covered | Bacher & Madsen 2011; IEA EBC Annex 58 ST3b; Deconinck & Roels 2017; Reynders et al. 2014 (DOI, abstract); Chen et al. 2026; PMC UKF+NLS 2025 |
| Web discovery | covered | MDPI Energies 2026 review; Annex 58 project page; CTSM-R docs hub |
| Informal / practitioner | covered | This repo (primary current-state evidence); CTSM-R reference / Dynastee notes; always labelled informal |

## Search strategy

- **Preprints:** `site:arxiv.org` grey-box RC / 2R2C identification; Kalman/EKF
  parameter estimation with hidden thermal state; identifiability and experiment
  design; plus lookup of arXiv:1512.08169, 1601.02947.
- **Formal:** Bacher & Madsen 2011 (open PDF); IEA EBC Annex 58 ST3b PDF;
  Deconinck & Roels 2017; Reynders et al. 2014 DOI; Chen et al. 2026
  Energy and Buildings; PMC11798724 (UKF+NLS).
- **Web:** complementary queries on 2R2C identifiability, nighttime vs solar
  windows, CTSM-R, unmeasured wall node.
- **Informal / codebase:** `KalmanMLEstimator.estimate`, `initial_state_estimator`,
  `identifiability.py`, `regularization.py`, `sysid_services.py`,
  Parameter Estimation UI, Schedules experiments; CTSM-R user guide; Dynastee
  CTSM-R case notes.

## Executive summary

Sources agree that **poor RC estimates from indoor air temperature alone are
expected** when (1) the envelope/mass node is unmeasured, (2) closed-loop
comfort control suppresses excitation, and (3) solar, heater, and occupancy
inputs are collinear on the same window. They do **not** treat a different
model family or extra wall sensors as the required fix; they treat
**identifiability of the existing structure**, **how the hidden state is
handled**, and **what data / experiments are used**.

**Current implementation (this repo, informal).** Production fitting is
**joint open-loop simulation MSE** over \(C\), \(R_\mathrm{ext}\), \(Q_\mathrm{int}\),
heater scale \(\alpha\), solar scale, 2R2C splits (when heater-excited), and
**\(T_{w,0}\) in the same \(\theta\)** (`kalman_ml.py`). CD-EKF PED exists only
as a diagnostic. A **leading-window** CD-EKF / wall-only fit for \(T_w\) is
implemented and documented as the non-circular approach
(`initial_state_estimator.py`) but is wired to **EKF reconstruction and
open-loop replay**, not to `handle_estimate_parameters_ml`. Wall initials are
pulled toward a \((T_a+T_\mathrm{out})/2\) prior with
\(\lambda_{T_w}=\max(\lambda,10)\) vs default \(\lambda=0.01\)
(`regularization.py`) — about **1000×** heavier than other parameters. Solar
and envelope splits have variance gates; \(C\), \(R\), \(Q_\mathrm{int}\),
\(\alpha\), and \(T_{w,0}\) are always in the joint vector. There is **no
regime-batched** estimation (night vs solar vs heater-off). The UI can save
datasets, jointly fit several windows, lock parameters, and schedule heater
PRBS/step **experiments** (default overnight), but index/detail **guidance** is
fit-KPI and “run automatic estimation”, not “collect this kind of data next”.

**Literature (what sources say).** Hidden envelope states should be
reconstructed from **leading / prior-day data with structural parameters
held**, not freely traded against \(C\) and \(R\) on the scored window (Chen
et al. 2026; this repo’s own `initial_state_estimator` docstring). Parameters
should be learned when loads are **known or constrained**, then remaining
disturbances characterised (Radecki & Hencey, arXiv:1601.02947). Nighttime /
low-solar windows reduce collinearity for envelope parameters; solar aperture
needs solar-rich data (Annex 58; MDPI 2026 citing nighttime gradients; Dynastee
CTSM-R notes). Closed-loop thermostat data is **information-poor**; designed
PRBS/ROLBS or self-excitation improves identifiability (Annex 58; arXiv:1512.08169).
Joint state–parameter filters (UKF/EKF) are a documented alternative when
states are unmeasured (PMC11798724). Operator involvement — residual checks,
physical plausibility, experiment design — is part of the classical grey-box
procedure (Bacher & Madsen 2011; Annex 58), not an optional UI nicety.

This brief does **not** choose a product design. It supplies evidence that the
user’s \(T_w\) hypothesis and regime-window examples are well-supported, and
that several applicable methods already have partial hooks in the code.

## Current implementation (codebase)

Label: **informal / practitioner** (this repository). Paths under
`heating_assistant/heatingassistant/`.

| Piece | What it does today | Implication for the question |
|-------|--------------------|------------------------------|
| `engine/thermal_model.py` | 2R2C; measurements observe air only; wall reconstructed by EKF | Matches the scoped model; hidden \(T_w\) is structural |
| `engine/estimation/kalman_ml.py` | Joint simulation-MSE; \(T_{w,0}\) is a decision variable; PED not used in production | Circular \(T_w\) vs structural \(\theta\) on the same window |
| `engine/initial_state_estimator.py` | Leading 6 h (or ≤25 % prefix) CD-EKF then wall-only opt; docstring warns same-window \(T_w\) fit “tends to underestimate heater influence” | The non-circular method exists but is not the PE path |
| `app/sysid_services.py` | ML estimate: history + optional per-dataset \(T_{w,0}\) blocks. Leading window used in `handle_run_sysid_simulation` / open-loop, **not** in `handle_estimate_parameters_ml` | Replay can be well-initialised while the fit that produced \(\theta\) was not |
| `engine/estimation/regularization.py` | \(\lambda_{T_w}\ge 10\), prior std 5 °C, prior = air/outdoor midpoint | Hidden state is tightly pinned to a possibly wrong blend; other parameters compensate |
| `engine/estimation/identifiability.py` | Gates: \(\mathrm{std}(T_i-T_j)\), \(\mathrm{std}(u)\), \(\mathrm{std}(Q_\mathrm{solar})\); splits only if heater excited | Partial identifiability; no night/solar/heater-off **partition** of \(\theta\) |
| Parameter Estimation UI | Apply/lock params, save windows, joint datasets, R²/RMSE warnings | User can run a fit; not told *which* window or experiment the data needs |
| Schedules experiments | Step/PRBS/pulse, default start 23:00, 6 h, auto-save dataset | Experiment machinery exists; not tied to “estimate \(R\) from this night, \(s_i\) from this sunny heater-off day” |

## Key sources

| Source | Axis | ID / URL | Triage | Relevance |
|--------|------|----------|--------|-----------|
| Bacher & Madsen, *Energy and Buildings* 2011 | Formal | [doi:10.1016/j.enbuild.2011.02.005](https://doi.org/10.1016/j.enbuild.2011.02.005); [open PDF](http://henrikmadsen.org/wp-content/uploads/2014/05/Journal_article_-_2011_-_Identifying_suitable_models_for_the_heat_dynamics_of_buildings.pdf) | Core | Grey-box hierarchy, likelihood tests, designed winter experiments, modeller-in-the-loop, solar residuals |
| Madsen et al., IEA EBC Annex 58 ST3b (2016) | Formal | [PDF](https://www.iea-ebc.org/Data/publications/EBC_Annex_58_Final_Report_ST3b.pdf) | Core | Experiment design, PRBS, solar pitfalls, grey-box validation, parameter correlation |
| Chen, Korolija & Rovas, *Energy and Buildings* 2026 | Formal | [doi:10.1016/j.enbuild.2026.117139](https://doi.org/10.1016/j.enbuild.2026.117139) | Core | Unmeasured RC states via **moving-horizon on prior-day data**; air node set to measured \(T\) |
| Radecki & Hencey, arXiv:1601.02947 | Preprint | [arXiv:1601.02947](https://arxiv.org/abs/1601.02947) | Core | UKF learns parameters during **known/constrained loads**, then unknown loads |
| Radecki & Hencey, arXiv:1512.08169 | Preprint | [arXiv:1512.08169](https://arxiv.org/abs/1512.08169) | Core | Controller **self-excitation** improves estimation vs thermostat-only data |
| Deconinck & Roels, *J. Building Physics* 2017 | Formal | [doi:10.1177/1744259116688384](https://doi.org/10.1177/1744259116688384) | Supporting | Profile-likelihood identifiability of grey-box parameters from on-site data |
| Reynders, Diriken & Saelens, *Energy and Buildings* 2014 | Formal | [doi:10.1016/j.enbuild.2014.07.025](https://doi.org/10.1016/j.enbuild.2014.07.025) | Supporting | Parameter quality vs input/observation accuracy (DOI retrieved; full PDF 406) |
| Zamani et al., *Indoor & Built Environment* 2025 | Formal | [PMC11798724](https://pmc.ncbi.nlm.nih.gov/articles/PMC11798724/) | Supporting | UKF+NLS joint parameters, inputs, and **unmeasured states**; sparse indoor measurements |
| *Energies* 19(1):77 (2026) review | Web | [MDPI](https://www.mdpi.com/1996-1073/19/1/77) | Supporting | Closed-loop data lacks spectral excitation; nighttime gradients cited to cut collinearity; 7–14 day windows |
| CTSM-R reference / Dynastee notes | Informal | [ctsm.info](https://ctsm.info/documentation.html); [Dynastee PDF](https://dynastee.info/wp-content/uploads/2020/06/DynamicAnalysisApplied2EPB.pdf) | Supporting | Residual ACF for missing states; select low-solar periods when heater is the input of interest |
| This repository | Informal | paths above | Core (current state) | Joint \(T_w\) in \(\theta\); unused leading-window PE; heavy \(T_w\) prior; experiments vs weak guidance |

## Themes and trends

### 1. Hidden wall/mass state vs structural parameters

**Agreement.** Indoor air is observed; envelope/mass is not. Fitting the hidden
initial state on the **same** interval as \(C\) and \(R\) lets the optimiser
explain transients with the wrong \(T_w\) (repo docstring; Chen et al. initialise
air from measurement and reconstruct other states from **prior-day** MHE).
Annex 58 requires an initial identification of how mass is lumped and then
validation that residuals are not concentrated at heater steps (a missing
fast/slow state). Joint filters (UKF) are used in the literature to carry
unmeasured temperatures with parameters rather than as unconstrained open-loop
initials (PMC11798724; arXiv:1601.02947).

**This codebase.** Joint \(\theta\) includes \(T_{w,0}\) per dataset start;
leading-window reconstruction is implemented for **simulation**, not for ML
estimation. The MAP prior on \(T_w\) is much tighter than on \(C\)/\(R\), so a
bad midpoint prior is sticky and other parameters move instead.

### 2. Regime-specific / batched data (night, solar, heater-off)

**Agreement, not a settled algorithm.** Annex 58 and Bacher & Madsen treat
solar as a separate input whose residuals often remain after envelope dynamics
are captured; gA is not a constant. The MDPI 2026 review reports methods that
“exploit nighttime thermal gradients to reduce parameter collinearity” and
two-stage fits (time-of-day grouped RC, then internal loads). Dynastee
CTSM-R notes explicitly select periods with **less solar** when the heater is
the input of interest. Radecki: learn when loads are constrained, then
estimate remaining disturbances.

**This codebase.** Solar scale is gated by \(\mathrm{std}(Q_\mathrm{solar})\);
splits by heater excitation. There is **no** staged objective (night → \(R,C\);
sunny heater-off → solar scale). Joint multi-dataset fit shares structural
\(\theta\) but still one MSE over concatenated windows.

### 3. Experiment design and closed-loop poverty

**Agreement.** Thermostatic closed-loop data “lacks sufficient spectral
excitation” (MDPI 2026 citing Serasinghe). Annex 58: for indoor temperature as
output, use **PRBS or ROLBS** on heat input; PRBS should excite both short and
long time scales and be uncorrelated with weather. arXiv:1512.08169:
self-excitation that “minimally disrupts normal operations” improved
estimation and downstream MPC in simulation.

**This codebase.** Schedules already offer step / PRBS / pulse, default
overnight 6 h, auto-save dataset. PE does not consume experiment metadata
(which parameters this dataset is *for*). Guidance does not tell the user when
a night step vs a sunny heater-off window is the missing piece.

### 4. Operator guidance and validation, not only a better optimiser

Bacher & Madsen: “a purely algorithmic procedure is not possible, hence the
modeller must be involved” to judge residuals and physical reality. Annex 58:
write the experimental setup first; watch solar on sensors; check parameter
correlation and significance; discard physically impossible solar aperture.
CTSM-R: ACF of residuals indicating extra states; compare simulation (open
loop) vs filter (closed loop).

**This codebase.** Warnings are mostly R²/RMSE/open-loop drift and plausible
\(C\)/\(R\) ranges (`model_diagnostics.py`). They do not say “this window has
no solar variance, so solar scale was not identified” or “schedule a night
PRBS because heater \(\mathrm{std}(u)\) is below the split gate”.

### 5. Estimator family (PED / MSE / dual EKF)

Literature uses CTSM-R MLE (Kalman innovations), UKF dual estimation, and
open-loop simulation error. This repo **abandoned production PED** because it
“diverges on unevenly-spaced data from controller restarts” (`kalman_ml.py`)
and uses backward-Euler open-loop MSE with L-BFGS-B. Sources do not crown one
objective as universally best; they do warn that the **objective plus initial
state plus data content** jointly determine whether \(\theta\) is physical.

## Gaps and limitations

- No retrieved paper evaluates **this** estimator on **this** house; poor
  field performance is asserted by the user and is consistent with the
  mechanisms above, not measured here.
- Regime batching is cited as practice (night gradients, low-solar heater
  windows) but not as a single canonical algorithm with proven gains on 2R2C
  air-only residential rooms.
- Chen et al. 2026 also discuss higher-order RC; that part is **out of
  destination scope**. Their MHE initialisation of unmeasured states is in
  scope.
- Reynders 2014 full text was not retrieved (DOI 406); claims limited to
  title/abstract and citations in Deconinck & Roels / Annex 58.
- arXiv script missing; preprint coverage is targeted lookups + search, not a
  full arXiv dump. Preprints are not peer-reviewed.
- MDPI 2026 review includes buildings >500 m²; residential cheap-sensor
  transfer is imperfect (labelled web).
- Whether PED can be restored (even timestamps after SWD-318) is an
  implementation question, not settled by literature.

## Recommended reading order

1. This brief’s **Current implementation** table (what the product actually does).
2. Bacher & Madsen 2011 (procedure, experiments, solar, modeller-in-the-loop).
3. Annex 58 ST3b §§4–5 and Appendix E (PRBS, solar, grey-box validation).
4. Chen et al. 2026 paragraph on unmeasured-state MHE from prior-day data.
5. Radecki & Hencey arXiv:1601.02947 then 1512.08169 (constrained-load
   learning; self-excitation).
6. Deconinck & Roels 2017 (profile likelihood / physical interpretability).
7. PMC11798724 (UKF with unmeasured nodes) and CTSM-R residual practice.

## Role in pipeline

Finding docs for `/explore` (graduate method vs guidance Tasks) and later
`/model` or `/define`. Supportive context only — not a product plan and not
acceptance criteria.

## Sources

1. Bacher, P. & Madsen, H. (2011). Identifying suitable models for the heat
   dynamics of buildings. *Energy and Buildings* 43(7), 1511–1522.
   doi:10.1016/j.enbuild.2011.02.005. **Formal.** Open PDF retrieved.
2. Madsen, H. et al. (2016). *Thermal performance characterisation using time
   series data – statistical guidelines* (IEA EBC Annex 58, ST3 part 2).
   https://www.iea-ebc.org/Data/publications/EBC_Annex_58_Final_Report_ST3b.pdf
   **Formal.** PDF retrieved.
3. Chen, G., Korolija, I. & Rovas, D. (2026). Evaluating single-zone grey-box
   thermal models… *Energy and Buildings*.
   doi:10.1016/j.enbuild.2026.117139. **Formal.** Abstract/body snippet retrieved.
4. Radecki, P. & Hencey, B. (2015). Online Model Estimation for Predictive
   Thermal Control of Buildings. arXiv:1601.02947. **Preprint.** Abs retrieved.
5. Radecki, P. & Hencey, B. (2015). Self-Excitation: An Enabler for Online
   Thermal Estimation and MPC of Buildings. arXiv:1512.08169. **Preprint.**
   Abs retrieved.
6. Deconinck, A.-H. & Roels, S. (2017). Is stochastic grey-box modelling suited
   for physical properties estimation… *Journal of Building Physics* 40(5),
   444–471. doi:10.1177/1744259116688384. **Formal.** Abstract retrieved.
7. Reynders, G., Diriken, J. & Saelens, D. (2014). Quality of grey-box models
   and identified parameters as function of the accuracy of input and
   observation signals. *Energy and Buildings* 82, 263–274.
   doi:10.1016/j.enbuild.2014.07.025. **Formal.** DOI/abstract only (PDF 406).
8. Zamani et al. (2025). Parameter-input estimation of RC thermal models…
   *Indoor & Built Environment*. PMC11798724. **Formal.** Full PMC HTML retrieved.
9. *Grey-Box RC Building Models for Intelligent Management…* *Energies* 19(1):77
   (2026). https://www.mdpi.com/1996-1073/19/1/77 **Web discovery.** HTML retrieved.
10. CTSM-R documentation / reference. https://ctsm.info/documentation.html
    **Informal.**
11. Dynastee (2020). Dynamic analysis applied to EPB (CTSM-R case notes).
    https://dynastee.info/wp-content/uploads/2020/06/DynamicAnalysisApplied2EPB.pdf
    **Informal.**
12. HeatingAssistant engine/UI as cited in **Current implementation**. **Informal.**

## Tracker

- Task: [SWD-324](https://marcusknielsen.atlassian.net/browse/SWD-324)
- Story: [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Artifact: `docs/agents/RESEARCH-pe-effectiveness.md`
- Branch: `cursor/swd-326-pe-effectiveness-747e` (delivery; research originally on `cursor/swd-323-pe-effectiveness-747e`)
- Model: `docs/agents/MODEL-pe-hidden-tw.md`
- PR: — (research never opens a PR)

## Next

`/define SWD-326` — Improve PE effectiveness and in-app guidance (research +
model are supportive).
