# Research brief: Parameter-estimation methods for grey-box 2R2C / RC models

## Question

Which parameter-estimation *methods* (beyond this product’s open-loop
simulation MSE and the SWD-326 / SWD-329 staged/Kalman harness) do sources
recommend for grey-box RC / 2R2C building models with a hidden wall node and
cheap indoor air sensors — and which are claimed to recover parameters more
reliably?

This brief is about **estimator families** (how \(\theta\) is scored and
optimised). It does **not** re-do occupancy-vs-window (SWD-328) or hidden
\(T_w\) formulation (SWD-325) except where those choices interact with the
algorithm. Supportive context only — not a product plan.

## Axes covered

| Axis | Status | Notes |
|------|--------|-------|
| Preprints (arXiv) | covered | Gaikwad et al. arXiv:2508.09118 (NLS / batch / MLE); neural/GA RC PE (arXiv:2604.05904) peripheral. No `scripts/arxiv_research.py` in this tree; HTML/PDF retrieved. |
| Formal written | covered | Kristensen/Madsen/Jørgensen 2004 (CTSM); Bacher & Madsen 2011; Annex 58 ST3b; Rouchier et al. 2018; De Coninck et al. 2016; Nespoli et al. IBPSA 2015 |
| Web discovery | covered | CTSM-R reference; ScienceDirect CTSM MLE feature assessment; IOP 2069/012101 (MLR/ARX/grey-box HLC) |
| Informal / practitioner | covered | This repo (`KalmanMLEstimator.estimate`); Alanqar/Ellis JCI 2018 two-step PEM; Bagge Carlson thesis (OE vs PEM); always labelled informal |

## Search strategy

- **Preprints:** grey-box RC / 2R2C parameter estimation; NLS vs MLE vs moving
  horizon; Kalman innovation likelihood; CasADi/IPOPT RC identification.
- **Formal:** CTSM / stochastic grey-box ML (Kristensen 2004, Bacher 2011);
  Annex 58 statistical guidelines; stochastic vs deterministic calibration
  (Rouchier 2018); collocation OE (De Coninck 2016); IBPSA MLE vs OE.
- **Web:** CTSM-R user/reference docs; complementary queries on prediction-error
  vs simulation-error for building RC models.
- **Informal / codebase:** production `estimate()` vs unused CD-EKF PED;
  SWD-329 Kalman path (PED NLL wrap, no diffusion-term ID); HVAC two-step PEM.

## Executive summary

Sources that care about **physical** \(C\), \(R\), HLC (not only one-step
prediction RMSE) recommend **continuous-time stochastic maximum likelihood**
via a Kalman / EKF innovations likelihood — the CTSM / Annex 58 grey-box
procedure — and they recommend **estimating diffusion (process noise) as well
as the RC parameters**. Deterministic output-error (open-loop simulation MSE)
is widely used and can look well-fitted, but it is claimed to **absorb
misspecification into \(\theta\)**, producing **inconsistent** \(C/R\) across
datasets and **overconfident** intervals.

**This product’s production PE is the method those sources warn against for
physical-parameter recovery:** joint open-loop simulation MSE (SciPy L-BFGS-B)
in `KalmanMLEstimator.estimate`. CD-EKF prediction-error likelihood exists as
a diagnostic only. The SWD-329 Kalman harness path wraps PED NLL without
identifying diffusion terms and with a short optimiser cap — it is not full
CTSM.

A second, independent recommendation is **how** to solve output-error if OE is
kept: **direct collocation / multiple shooting** rather than single-shooting
simulation (De Coninck 2016; system-ID literature). That improves the *solver*,
not the *criterion*.

ARX / linear regression is recommended when the goal is **HLC / gA / time
constants**, not a 2R2C \(\theta\) map. Joint UKF/EKF state–parameter filters
are documented alternatives (already in SWD-324). Neural/GA PE is peripheral
for recovering true physical \(\theta\).

This brief does **not** choose a shipping estimator.

## Current implementation (codebase)

Label: **informal / practitioner** (this repository).

| Piece | What it does today | Method family |
|-------|--------------------|---------------|
| `KalmanMLEstimator.estimate` | Multi-step open-loop simulation MSE; L-BFGS-B; \(Q_\mathrm{var}\)/\(R_\mathrm{var}\) unused | Deterministic **output-error (OE)** / simulation-error NLS |
| `compute_log_likelihood` / `_cd_ped_neg_ll` | CD-EKF innovations NLL | Stochastic **prediction-error ML** — diagnostic only in production |
| SWD-329 `kalman` harness path | Same `estimate()` entry with OE swapped for PED NLL; maxiter 25; no \(\sigma\) diffusion ID | Partial PED; not CTSM |
| SWD-326 / SWD-329 procedures | Combined vs staged windows; occupancy/UA extras | **Data / structure** forks, not estimator-family forks |
| SciPy only (SWD-315) | IPOPT removed from product PE | Collocation NLPs in the literature typically use IPOPT |

## Key sources

| Source | Axis | ID / URL | Triage | Relevance |
|--------|------|----------|--------|-----------|
| Kristensen, Madsen & Jørgensen (2004) | Formal | [doi:10.1016/j.automatica.2003.10.001](https://doi.org/10.1016/j.automatica.2003.10.001); [open PDF](http://henrikmadsen.org/wp-content/uploads/2014/05/Journal_article_-_2004_-_Parameter_estimation_in_stochastic_grey-box_models.pdf) | Core | CTSM: SDE grey-box; ML/MAP via EKF; diffusion terms → less bias, more reproducible \(\theta\) |
| Bacher & Madsen (2011) | Formal | [doi:10.1016/j.enbuild.2011.02.005](https://doi.org/10.1016/j.enbuild.2011.02.005) | Core | Building heat dynamics; CTSM-R; likelihood-ratio model selection. Already in SWD-324 — cited here for the *estimator*, not the \(T_w\)/regime story |
| Madsen et al., Annex 58 ST3b (2016) | Formal | [PDF](https://www.iea-ebc.org/Data/publications/EBC_Annex_58_Final_Report_ST3b.pdf) | Core | ARX for HLC/gA; grey-box SS when internal physical params or irregular sampling needed; **BIC for physical \(\theta\), AIC for forecast/control** |
| Rouchier, Rabouille & Oberlé (2018) | Formal | [doi:10.1016/j.buildenv.2018.02.046](https://doi.org/10.1016/j.buildenv.2018.02.046); [open PDF](https://srouchier.github.io/files/2018-bae-sde.pdf) | Core | 1R1C/2R2C/3R3C; MCMC on Kalman likelihood vs deterministic SSE; deterministic HLC/\(C\) inconsistent across datasets |
| De Coninck, Magnusson, Åkesson & Helsen (2016) | Formal | [doi:10.1080/19401493.2015.1046933](https://doi.org/10.1080/19401493.2015.1046933) | Core | JModelica **direct collocation** OE for RC grey-box; better OE landscape than single shooting |
| Nespoli et al., IBPSA BS2015 | Formal | [PDF](https://publications.ibpsa.org/proceedings/bs/2015/papers/bs2015_3006.pdf) | Supporting | MLE in CTSM-R vs OE: ML estimates noise + parameter SDs; one-step predictions; PRBS |
| Gaikwad et al. (2025) | Preprint | [arXiv:2508.09118](https://arxiv.org/abs/2508.09118) | Supporting | Head-to-head NLS / batch (process+meas noise) / KF MLE / ALS on RC; all struggle on arbitrary closed-loop policy; long-data NLPs fail to scale |
| CTSM-R reference | Informal | [PDF](https://ctsm.info/ctsmr-reference.pdf) | Supporting | ML via KF; k-step vs **deterministic simulation** (condition only on \(x_0\)); 2-state can fit locally and fail long simulation |
| Alanqar, Ellis et al., JCI Purdue IHPBC 2018 | Informal | [PDF](https://docs.lib.purdue.edu/ihpbc/255) | Supporting | Two-step: simulation PEM for plant, then 1-step PEM + disturbance for Kalman gain |
| Bagge Carlson thesis | Informal | LU PDF (Ljung lineage) | Supporting | PEM if equation-error; OE/simulation-error is nonconvex; multiple shooting / collocation mitigate OE |
| This repository | Informal | `kalman_ml.py` | Core (current state) | Production = OE MSE; PED unused; SWD-329 Kalman ≠ full CTSM |

## Themes and trends

### 1. Stochastic ML (prediction-error) vs deterministic OE (simulation-error)

**Agreement.** Kristensen et al. (2004) state that grey-box estimation with
explicit diffusion and measurement-noise terms “tends to give more reproducible
results and less bias, because random effects due to process and measurement
noise are not absorbed into the parameter estimates.” Rouchier et al. (2018)
test that claim on a test cell with 1R1C/2R2C/3R3C: **deterministic** SSE
calibration gives HLC and \(C\) posteriors that **do not overlap** across
similar 3–5 day sequences; **stochastic** Kalman-likelihood MCMC posteriors
overlap and are more robust. They also warn that a one-step Kalman fit
*always looks good* — residual ACF, not in-sample overlay, is the model-order
check.

Nespoli et al. (IBPSA 2015) contrast MLE in CTSM-R with OE: ML can estimate
noise intensities and parameter standard deviations and is asymptotically
unbiased (citing Ljung 1999); likelihood uses one-step predictions.

Annex 58: use grey-box state-space when **internal physical parameters** or
irregular sampling matter; ARX may suffice for HLC/gA. For non-nested model
choice: **BIC if identifying physical parameters; AIC if forecasting/control.**

**Disagreement / nuance.** Gaikwad et al. (arXiv:2508.09118) compare NLS (open-
loop LS), batch estimation (states as decisions with process + measurement
noise), and KF MLE on simulated RC houses. They report **similar** difficulty
on the hardest closed-loop-like case (Sim3); NLS/BE/MLE NLPs become expensive
and often fail to converge on long data. Their claim that MLE “has not been
directly applied to building thermal dynamics” is **wrong relative to
CTSM/Bacher** — they mean their CasADi/IPOPT MLE formulation. Score in that
paper is prediction accuracy under a new control policy, not true-\(\theta\)
recovery.

**This codebase.** Production PE is Rouchier’s deterministic setting (open-loop
SSE, no Kalman in the objective). PED exists but is unused because it was
observed to diverge on uneven timestamps (comment in `kalman_ml.py`; SWD-318
later aligned ID write cadence — whether PED is now viable is an
implementation question, not settled here).

### 2. What “Kalman PE” means (three different things)

Sources and this repo use “Kalman” for distinct estimators:

| Name | Criterion | Hidden \(T_w\) | Noise \(\sigma\) in \(\theta\)? |
|------|-----------|----------------|----------------------------------|
| Joint UKF/EKF state–parameter (SWD-324: Radecki; PMC11798724) | Filter covariance / constrained-load | Filter state | Usually tuned, not ML-identified |
| CTSM / PED ML | Innovations log-likelihood \(\sum(\varepsilon^\top S^{-1}\varepsilon + \log\|S\|)\) | Filter mean/cov | **Yes** (diffusion ID is the point) |
| SWD-329 harness `kalman` | PED NLL wrap of `estimate()` | Filter | **No** (fixed \(Q_\mathrm{var}\)/\(R_\mathrm{var}\); maxiter 25) |
| Production `estimate()` | Open-loop simulation MSE | \(T_{w,0}\) in \(\theta\) | Unused |

A “switch to Kalman” that does not estimate diffusion is **not** the method
Kristensen/Rouchier credit for less-biased physical \(\theta\).

### 3. Better OE solvers (if the criterion stays simulation-error)

De Coninck et al. (2016) identify RC grey-box models with JModelica **direct
collocation**: states (and derivatives) are decision variables; continuity
ties elements; IPOPT solves the NLP. Post-simulation RMSE is the validation
metric. Collocation is argued to be more reliable than single-shooting OE
(this product’s free-run of backward-Euler then L-BFGS-B). Bagge Carlson
(informal, Ljung lineage): OE is hard (nonconvex, exploding gradients);
**multiple shooting / collocation** are the standard mitigations.

This is a **numerical** recommendation for OE, not a claim that OE recovers
true \(\theta\) better than stochastic ML.

### 4. Batch / moving-horizon with process + measurement noise

Gaikwad’s **batch estimation** treats the full state trajectory as decisions
and penalises both process and measurement residuals (Robertson/Lee/Rawlings
MHE lineage). Chen et al. 2026 (already in SWD-324) use MHE on **prior-day
data** to reconstruct unmeasured RC states with structural \(\theta\) held —
that is a *state* treatment, not a replacement estimator for \(C,R\). Combining
MHE-for-\(T_w\) with ML-for-\(\theta\) is a documented pattern, not a single
named product method.

### 5. Two-step PEM (simulation then one-step + disturbance)

Alanqar / Ellis (JCI 2018, informal): (1) **simulation PEM** for the plant
model; (2) augment a disturbance and run **1-step PEM** to get the Kalman
gain. Remove saturation; careful initialisation. This splits the two criteria
this product currently conflates (long-horizon OE vs one-step PED).

### 6. ARX / ALS — right tool for HLC, wrong map for 2R2C \(\theta\)

Annex 58 recommends ARX when the quantities of interest are HLC, gA, and time
constants. IOP 2069/012101 (web) reports MLR, ARX, and grey-box giving similar
HLC, with ARX often **narrower CIs** than grey-box. Gaikwad’s ALS regression
beats RC NLPs when the test reuses the training control policy, and fails when
the policy changes (no physics structure). For this destination — recover
**true 2R2C parameters** on synthetic data, then predict real rooms — ARX is
not a drop-in estimator of \(C,R,R_i,C_w\).

### 7. Neural / GA PE

arXiv:2604.05904 (preprint) compares neural/transfer-learning RC PE vs genetic
algorithms on 2R2C and notes sensitivity to initialisation. Peripheral for
recovering physically meaningful \(\theta\) under the SWD-323 sensor constraint.

## Gaps and limitations

- Few head-to-heads score **true-\(\theta\) recovery** (SWD-323’s synthetic
  bar). Most papers score indoor-temperature RMSE, HLC, or closed-loop
  prediction under a new policy.
- Gaikwad’s “MLE not applied to buildings” is a formulation-scope claim, not
  a literature fact.
- SWD-329 Kalman path is not evidence for or against CTSM: no diffusion ID,
  short data, maxiter 25.
- No `scripts/arxiv_research.py` in this tree; preprint coverage used
  HTML/PDF fetches of known IDs plus complementary search.
- Collocation / CasADi / IPOPT toolchains conflict with this product’s
  SciPy-only PE (SWD-315) if OE-collocation were adopted as-is.
- Occupied homes, openings, and cheap sensors remain the SWD-328/329 data
  problem; a better estimator does not remove the need for clean regimes or
  extra terms.

## Recommended reading order

1. This brief’s **Current implementation** table (what the product actually
   optimises).
2. Kristensen, Madsen & Jørgensen 2004 (why diffusion ML vs absorbing noise
   into \(C,R\)).
3. Rouchier et al. 2018 (deterministic vs stochastic 2R2C on repeated
   datasets — the closest empirical analogue).
4. Annex 58 ST3b (when ARX vs grey-box; BIC vs AIC).
5. Bacher & Madsen 2011 (CTSM-R procedure already cited in SWD-324).
6. De Coninck et al. 2016 (collocation OE, if the criterion stays
   simulation-error).
7. Gaikwad arXiv:2508.09118 (NLS vs batch vs MLE scalability; read against
   CTSM, not as the first application of MLE).
8. CTSM-R reference (k-step vs deterministic simulation) and Alanqar/Ellis
   two-step PEM.

## Role in pipeline

Finding docs for `/explore` (whether SWD-323 should add an estimator-family
bake-off) and later `/model` or `/define`. Supportive context only — not a
product plan, not a winner among SWD-326/329 procedures, and not acceptance
criteria.

## Sources

1. Kristensen, N. R., Madsen, H. & Jørgensen, S. B. (2004). Parameter
   estimation in stochastic grey-box models. *Automatica* 40(2), 225–237.
   doi:10.1016/j.automatica.2003.10.001. **Formal.** Open PDF retrieved.
2. Bacher, P. & Madsen, H. (2011). Identifying suitable models for the heat
   dynamics of buildings. *Energy and Buildings* 43(7), 1511–1522.
   doi:10.1016/j.enbuild.2011.02.005. **Formal.** Open PDF retrieved
   (also SWD-324).
3. Madsen, H. et al. (2016). *Thermal performance characterisation using time
   series data – statistical guidelines* (IEA EBC Annex 58, ST3 part 2).
   https://www.iea-ebc.org/Data/publications/EBC_Annex_58_Final_Report_ST3b.pdf
   **Formal.** PDF retrieved.
4. Rouchier, S., Rabouille, M. & Oberlé, P. (2018). Calibration of simplified
   building energy models for parameter estimation and forecasting: stochastic
   versus deterministic modelling. *Building and Environment* 134, 181–190.
   doi:10.1016/j.buildenv.2018.02.046. **Formal.** Open PDF retrieved.
5. De Coninck, R., Magnusson, F., Åkesson, J. & Helsen, L. (2016). Toolbox
   for development and validation of grey-box building models for forecasting
   and control. *Journal of Building Performance Simulation* 9(3), 288–303.
   doi:10.1080/19401493.2015.1046933. **Formal.** PDF retrieved.
6. Nespoli, L., Salani, M. & Medici, V. (2015). Towards unsupervised
   identification of building thermal models: maximum likelihood estimation.
   *Proc. BS2015*, paper 3006.
   https://publications.ibpsa.org/proceedings/bs/2015/papers/bs2015_3006.pdf
   **Formal.** PDF retrieved.
7. Gaikwad, N. et al. (2025). Evaluating grey-box modeling approaches for
   building thermal dynamics. arXiv:2508.09118. **Preprint.** HTML retrieved.
8. CTSM-R reference manual. https://ctsm.info/ctsmr-reference.pdf
   **Informal.**
9. Alanqar, A., Ellis, M. et al. (2018). Two-step identification for HVAC
   models used in MPC. Purdue IHPBC. https://docs.lib.purdue.edu/ihpbc/255
   **Informal.**
10. Bagge Carlson, F. thesis (Ljung lineage) on PEM vs OE / collocation.
    **Informal.**
11. HeatingAssistant `engine/estimation/kalman_ml.py` as cited in **Current
    implementation**. **Informal.**
12. SWD-324 brief `docs/agents/RESEARCH-pe-effectiveness.md` (UKF, hidden
    \(T_w\), excitation — complementary, not duplicated). **Informal** as a
    repo artifact; its formal citations stand on their own.

## Tracker

- Task: [SWD-331](https://marcusknielsen.atlassian.net/browse/SWD-331)
- Story: [SWD-323](https://marcusknielsen.atlassian.net/browse/SWD-323)
- Artifact: `docs/agents/RESEARCH-pe-methods.md`
- Branch: `cursor/swd-331-pe-methods-747e` (finding docs; SWD-329 delivery PR
  #612 already open — not mixed onto that PR)
- PR: — (research never opens a PR)

## Next

`/explore SWD-323` — rechart whether to add an estimator-family bake-off
(stochastic ML / collocation OE / two-step PEM vs current simulation MSE)
or finish SWD-329 review-fix first. This brief does not pick a shipping
method. SWD-329 remains In Review independently (`/review-fix SWD-329`).
