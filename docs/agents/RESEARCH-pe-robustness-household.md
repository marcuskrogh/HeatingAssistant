# Research brief: Synthesising household-like 2R2C traces and PE that recovers true parameters under unmodelled heat

## Question

How should we synthesise a single-room 2R2C household-like trace (open
window/door, occupancy, and similar extra heat the estimator does not see),
and which PE procedures or extra terms recover true 2R2C parameters reliably
enough to identify a model with high predictive power?

Plant and estimator are independent choices; both 2R2C for this investigation.
Score is true-parameter recovery. Predictive power is why that score matters.

## Axes covered

| Axis | Status | Notes |
|------|--------|-------|
| Preprints (arXiv) | covered | Atom API + abs/html: Guo et al. 2001.09141; Coffman/Barooah lineage; BuilDyn 2605.29849; Radecki 1512.08169 already in SWD-324 brief. No `scripts/arxiv_research.py` in this tree. |
| Formal written | covered | Coffman & Barooah 2018 (NSF PAR + DOI); Kim et al. 2016 DOI; Bacher & Madsen 2011 PDF; IBPSA 2015 Faggianelli et al.; Annex 71 PSR + Fordatis spec; Harb et al. 2016 DOI (metadata) |
| Web discovery | covered | IEA EBC Annex 71 dataset DOI; MDPI Energies 2026 RC review; IBPSA / OSTI grey-box surveys |
| Informal / practitioner | covered | This repo (`KalmanMLEstimator`, `internal_gain`, SWD-322 window exclusion); always labelled informal |

## Search strategy

- **Preprints:** arXiv Atom queries on occupancy / occupant-induced / internal
  heat gain + RC/grey-box identification; `ti:"unmeasured disturbance"`;
  window opening + grey-box (zero hits — window treated under ventilation /
  natural-ventilation papers instead).
- **Formal:** Coffman & Barooah SPDI; Kim/Cai/Braun lumped disturbance;
  Bacher & Madsen 2011; IBPSA 2015 naturally ventilated grey-box; Annex 71
  twin-house synthetic occupancy/window/door; Harb 2016 occupied buildings.
- **Web:** Annex 71 Fordatis dataset; complementary queries on window-as-UA
  vs occupancy-as-Q; synthetic occupancy generators (BuilDa/BuilDyn).
- **Informal / codebase:** `internal_gain` in `kalman_ml.py`; SWD-322
  exclude-open-window PE; SWD-326 bake-off (no occupancy/window extras);
  control-side window Q-inflation (not PE).

## Executive summary

Sources agree that **unmeasured internal heat (people, appliances) is not a
small residual**. It can be comparable to HVAC power, and **ignoring it
biases RC parameters** (Coffman & Barooah 2018; Guo et al. arXiv:2001.09141;
Kim et al. 2016). They also treat **open windows as a different physical
effect** than occupancy: extra air change / conductance to outdoor, not an
additive occupant wattage (Faggianelli et al. 2015; Annex 71 User-2 window
and door operation). Mixing the two into one `Q_int` term is a modelling
error the literature warns against by using separate occupancy and
ventilation terms.

**On synthesis.** Annex 71 deliberately used **synthetic occupancy** (family
schedules from time-use surveys, plus operated windows/doors) because real
occupants make heat, moisture, and CO₂ loads too uncertain for a validation
experiment (Kersken & Strachan Fordatis spec; E3S 2020 design paper). That
matches this Task: a 2R2C plant plus **known extra occupancy heat** and
**known extra window/door loss**, so true \(C,R\) still exist. BuilDa/BuilDyn
(arXiv:2605.29849) add stochastic occupancy, window-opening for natural
ventilation, and **excitation** (PRBS/steps), because closed-loop comfort
data is information-poor (also Bacher & Madsen 2011; Harb et al. 2016).
Commercial piecewise-constant occupancy (office step in/out) is **not** the
right household prior (Coffman & Barooah say the piecewise-constant insight
is based on commercial use). Household synthesis should use **schedule-like
or bursty occupancy**, not one daily square wave.

**On PE procedures (what sources say, not a product choice).** Three
families recur:

1. **Use clean windows for fabric.** Night / unoccupied / window-closed
   periods for envelope \(R,C\); do not fit fabric on mixed occupancy+solar
   (SWD-324 brief; Annex 58/71 staged experiments; this repo already
   **drops open-window samples** in PE — SWD-322 — and does **not** model
   open-window plant dynamics). Faggianelli et al. show a model calibrated
   only with continuous ventilation **fails when windows close**.
2. **Put occupancy in the estimator as a disturbance, not as a constant
   \(Q_\mathrm{int}\) only.** Simultaneous plant-and-disturbance ID: treat
   occupant load as a slowly varying / piecewise-constant **state**, then
   outer-loop \(R,C\) (Coffman & Barooah SPDI; Guo et al. 2001.09141 with
   non-negativity constraints). Kim et al. 2016: closed-loop ID without a
   lumped disturbance model is biased. This repo already estimates a
   **scalar** `internal_gain` jointly with \(C,R\) (`kalman_ml.py`) — that
   is a constant bias, not a time-varying occupancy state.
3. **If occupancy or window state is known, feed it as an input.** Li et
   al. 2023 (IBPSA) give occupant/appliance/ventilation fluxes to the 2R2C
   identification to reduce bias. This product already has door/window
   contacts for control; PE currently uses them only as an **exclude mask**,
   not as extra UA.

**2R2C.** Reynders et al. 2014 (cited in Li 2023 / MDPI 2026) still support
2nd–4th order for ~1 °C RMSE on Belgian houses; Harb et al. 2016 preferred
4R2C for occupied offices/homes on **prediction** error, not true-\(\theta\)
recovery. Sources do not say 2R2C is wrong for this investigation; they say
**unmodelled occupancy/ventilation will make 2R2C parameters look wrong**
even when the structure is adequate.

This brief does **not** choose the analysis recipe. It supplies evidence
that a household-like synthetic should inject occupancy and window/door as
**separate, known extras**, that true \(C,R\) recovery needs either clean
regimes or those extras in the estimator, and that SWD-326’s three
procedures (combined / separated / staged) did not include these extras.

## Key sources

- Coffman & Barooah, *Building and Environment* 128 (2018) 153–160.
  DOI [10.1016/j.buildenv.2017.10.020](https://doi.org/10.1016/j.buildenv.2017.10.020).
  NSF PAR [10076822](https://par.nsf.gov/servlets/purl/10076822).
  **Formal.** Occupant load unmeasured and large; SPDI: piecewise-constant
  disturbance as augmented state + outer \(R,C\); commercial occupancy prior.
- Guo, Coffman, Munk, Im, Kuruganti, Barooah, arXiv:[2001.09141](https://arxiv.org/abs/2001.09141)
  (2020). **Preprint.** 2R2C single-zone with \(q_\mathrm{int}\) as the hard
  unknown; constrained disturbance ID; **known-truth** ORNL testbed with
  heaters mimicking occupancy — the evaluation pattern this Task wants.
- Kim, Cai, Ariyur, Braun, *Building and Environment* 107 (2016) 169–180.
  DOI [10.1016/j.buildenv.2016.07.007](https://doi.org/10.1016/j.buildenv.2016.07.007).
  **Formal.** Closed-loop ID under unmeasured disturbance: lumped
  disturbance model; omitting it biases plant parameters.
- Faggianelli, Brun, Wurtz, Muselli, IBPSA BS2015
  [bs2015_2798](https://publications.ibpsa.org/proceedings/bs/2015/papers/bs2015_2798.pdf).
  **Formal.** Naturally ventilated grey-box: **calibrate with and without
  airflow**; model calibrated only while windows stay open fails when they
  close.
- Kersken & Strachan, Annex 71 Twin House dataset.
  DOI [10.24406/fordatis/76](https://doi.org/10.24406/fordatis/76);
  spec PDF on Fordatis; design paper
  [10.1051/e3sconf/202017222003](https://doi.org/10.1051/e3sconf/202017222003).
  **Formal / web.** Synthetic family occupancy, internal heat, **operated
  window and door**; staged User-1 vs User-2. Public dataset.
- Koch et al., BuilDyn, arXiv HTML [2605.29849](https://arxiv.org/html/2605.29849v1)
  (2026). **Preprint.** BuilDa stochastic occupancy + window-opening
  schedules; excitation vs PI/hysteresis for identifiability.
- Bacher & Madsen, *Energy and Buildings* 43 (2011) 1511–1522.
  [PDF](http://henrikmadsen.org/wp-content/uploads/2014/05/Journal_article_-_2011_-_Identifying_suitable_models_for_the_heat_dynamics_of_buildings.pdf).
  **Formal.** Grey-box SDE + likelihood; PRBS with short and long constants;
  stochastic term for model deficiency (not a substitute for missing occupancy
  input if the goal is physical \(R,C\)).
- Harb, Boyanov, Hernández, Streblow, Müller, *Energy and Buildings* 117
  (2016) 199–207. DOI [10.1016/j.enbuild.2016.02.021](https://doi.org/10.1016/j.enbuild.2016.02.021).
  **Formal (abstract/metadata).** Occupied-building grey-box **forecast**
  accuracy; 4R2C best among tested on their sites — prediction, not true
  \(\theta\).
- Li, IBPSA 2023, DOI [10.26868/25222708.2023.1360](https://doi.org/10.26868/25222708.2023.1360).
  **Formal.** Inverse RC ID; occupant/appliance/ventilation fluxes **given
  to** the 2R2C fit so they do not bias \(R,C\); notes those extras still
  induce seasonal bias if omitted.
- This repository (SWD-322 PLAN; `kalman_ml.py` `internal_gain`; SWD-326
  report). **Informal.** PE excludes override-active window samples; does
  not model extra UA; joint constant `internal_gain`; bake-off had no
  occupancy/window extras.

## Themes and trends

**Occupancy ≠ window.** Occupancy is an internal heat input on the air node.
An open window/door is extra exchange with outdoor (and sometimes a
neighbour room). Sources that recover fabric parameters keep those channels
separate, or drop the open-window samples.

**Known-truth synthesis is the evaluation pattern.** Guo et al. and Annex 71
both manufacture occupancy (heaters or synthetic users) so disturbance and
fabric can be scored. That is stronger than fit-RMSE alone when the question
is “did we recover \(C,R\)?”

**Constant \(Q_\mathrm{int}\) is weaker than a time-varying disturbance
state** when people come and go. Joint scalar `internal_gain` (this repo)
matches “neglect time variation”. SPDI / lumped-disturbance papers exist
because that neglect biases \(R,C\).

**Household occupancy is burstier than commercial.** Do not copy the office
step-occupancy prior into a residential synthetic without a schedule/bursty
variant.

**Excitation still matters.** Occupancy and window events are disturbances,
not a substitute for heater/solar richness. BuilDyn and Bacher/Madsen still
want PRBS/step-like heat when the goal is parameter recovery.

**Prediction can look fine with wrong \(R,C\).** Occupied-building papers
often report °C RMSE. That can be achieved by absorbing occupancy into
biased envelope parameters. The user’s bar (true parameters) is stricter
than forecast RMSE.

## Gaps and limitations

- Few papers recover **known true 2R2C \(C,R\)** on a plant that also has
  household window opening; Annex 71 is a real house (not a 2R2C truth),
  Guo et al. mimic occupancy with heaters (not windows).
- Residential piecewise-constant occupancy is weakly supported; commercial
  SPDI assumptions may fail on a home.
- No arXiv hits on “window opening” + grey-box identification; ventilation
  grey-box is conference/IBPSA instead.
- Harb 2016 full text was not retrieved (DOI/metadata only).
- This repo’s window path is **exclude-from-PE**, not extra-UA estimation;
  whether that recovers true \(R\) when openings are frequent is untested
  here (SWD-326 did not inject openings).

## Recommended reading order

1. Coffman & Barooah 2018 (why occupancy must be identified with the plant).
2. Guo et al. arXiv:2001.09141 (2R2C + known-truth occupancy disturbance).
3. Annex 71 spec (how to synthesise household occupancy, window, door).
4. Faggianelli et al. 2015 (do not calibrate window-open and window-closed
   as if they were one plant).
5. Kim et al. 2016 (closed-loop + lumped disturbance).
6. This repo SWD-322 + `internal_gain` (what PE already does).

## Role in pipeline

Finding docs for `/define SWD-329` (offline robustness analysis) and any
later `/model` if extra occupancy-state or window-UA terms need formulation.
Supportive context only — not a product plan.

## Sources

1. A. R. Coffman, P. Barooah, “Simultaneous identification of dynamic model
   and occupant-induced disturbance for commercial buildings,” *Building and
   Environment*, 128:153–160, 2018. DOI 10.1016/j.buildenv.2017.10.020.
   Axis: **formal**. Retrieved: NSF PAR PDF + publisher abstract.
2. Z. Guo et al., “Aggregation and data driven identification of building
   thermal dynamic model and unmeasured disturbance,” arXiv:2001.09141, 2020.
   Axis: **preprint**. Retrieved: ar5iv HTML.
3. D. Kim, J. Cai, K. B. Ariyur, J. E. Braun, “System identification for
   building thermal systems under the presence of unmeasured disturbances in
   closed loop operation: Lumped disturbance modeling approach,” *Building
   and Environment*, 107:169–180, 2016. DOI 10.1016/j.buildenv.2016.07.007.
   Axis: **formal**. Retrieved: DOI page / citing LBNL PDF.
4. G. A. Faggianelli, A. Brun, E. Wurtz, M. Muselli, “Grey-box modelling for
   naturally ventilated buildings,” IBPSA BS2015, paper 2798.
   Axis: **formal**. Retrieved: IBPSA PDF.
5. M. Kersken, P. Strachan, Twin House Experiment IEA EBC Annex 71,
   Fordatis DOI 10.24406/fordatis/76, 2019; experimental specification PDF;
   Kersken & Strachan, E3S Web Conf. 172:22003, 2020
   (DOI 10.1051/e3sconf/202017222003). Axis: **formal** / **web**.
6. F. Koch et al., “BuilDyn: Excitation-Driven Data Generation…,”
   arXiv:2605.29849, 2026. Axis: **preprint**. Retrieved: arXiv HTML.
7. P. Bacher, H. Madsen, “Identifying suitable models for the heat dynamics
   of buildings,” *Energy and Buildings*, 43:1511–1522, 2011.
   Axis: **formal**. Retrieved: henrikmadsen.org PDF.
8. H. Harb et al., “Development and validation of grey-box models for
   forecasting the thermal response of occupied buildings,” *Energy and
   Buildings*, 117:199–207, 2016. DOI 10.1016/j.enbuild.2016.02.021.
   Axis: **formal**. Retrieved: DOI metadata (full PDF not fetched).
9. “Machine learning driven parameter identification for grey-box thermal
   modelling for buildings,” IBPSA 2023, DOI 10.26868/25222708.2023.1360.
   Axis: **formal**. Retrieved: DOI HTML.
10. IEA EBC Annex 71 project status report (PSR PDF on iea-ebc.org).
    Axis: **web**.
11. Heating Assistant `kalman_ml.py`, SWD-322 PLAN, SWD-326 report.
    Axis: **informal / practitioner**.

## Tracker

- Task: SWD-328 (supportive-only; delivery SWD-329)
- Artifact: `docs/agents/RESEARCH-pe-robustness-household.md`
- Branch: `cursor/swd-329-pe-robustness-747e`
- PR: — (research never opens a PR)

## Next

`/define SWD-329` — offline robustness analysis using this brief as
supportive input (dataset extras + which PE procedures/extra terms to compare).
