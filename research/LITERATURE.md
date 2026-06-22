# Literature Review: HVAC Building & Room Temperature Control

Compiled 2026-06-22. This document surveys free online literature (arXiv, open-access journals, field-study preprints, and reference texts) relevant to **Heating Assistant** — a Home Assistant integration using a **2R2C grey-box thermal model**, **continuous-discrete EKF** state estimation, **CD-EKF PED** batch parameter identification, and **linearised convex MPC** with soft comfort corridors.

Each section maps literature to the current implementation and to opportunities documented in [`improvements/ACTIONS.md`](../improvements/ACTIONS.md).

---

## Current Heating Assistant Architecture (baseline for comparison)

| Component | Implementation | Key defaults (`const.py`) |
|-----------|----------------|---------------------------|
| Thermal model | Per-room 2R2C (+ optional slab node, infiltration, solar, sky radiation) | `DEFAULT_C_AIR_FRACTION=0.05`, `DEFAULT_R_AW_FRACTION=0.05` |
| State estimation | CD-EKF with offset states (`sigma_w`, `sigma_v`, `sigma_b`) | 0.1 / 0.5 / 0.002 K |
| Parameter ID | CD-EKF PED + IPOPT, triggered once after ~14 days | `HISTORY_BUFFER_SIZE=480` (120 h at 15 min) |
| MPC | Linearised QP (OSQP/HiGHS), soft output bounds | `horizon=100` steps (~25 h), `update_interval=900` s |
| Comfort | Symmetric corridor `[setpoint ± comfort_offset]` | `DEFAULT_COMFORT_OFFSET=2.0` °C |
| Cost weights | Q (tracking), R (energy), S (smoothing), ρ (soft bound), P=λQ (terminal) | `tracking_weight=0`, `soft_constraint_weight=1000`, `terminal_weight=100` |
| Heat pump COP | Carnot-scaled from rated COP | `DEFAULT_COP_RATED=3.5` at 7 °C |
| Disturbance forecast | Weather entity outdoor temperature (no bias correction) | — |

---

## 1. Validation of the 2R2C + EKF + MPC Architecture

These papers confirm that lumped RC networks, Kalman filtering, and linearised MPC are the dominant paradigm for residential and light-commercial building climate control.

| # | Title | Venue | Year | Link | Relevance |
|---|-------|-------|------|------|-----------|
| 1 | **Building Thermal-Network Models: A Comparative Analysis, Recommendations, and Perspectives** | *Energies* 15(4):1328 | 2022 | https://www.mdpi.com/1996-1073/15/4/1328 | 2R2C/3R2C models match 20th-order models for control; validates model order choice |
| 2 | **Comparing Building Thermal Dynamics Models and Estimation Methods for Grid-Edge Applications** | arXiv:2508.09118 | 2025 | https://arxiv.org/abs/2508.09118 | MLE RC-network estimation (Heating Assistant's approach) performs best when training window matches current dynamics |
| 3 | **Online Model Estimation for Predictive Thermal Control of Buildings** | arXiv:1601.02947 | 2016 | https://arxiv.org/abs/1601.02947 | Foundational UKF/EKF online RC parameter + disturbance estimation for MPC; <2 weeks training → reliable 24 h predictions |
| 4 | **Hybrid Modeling Approach for Better Identification of Building Thermal Network Model** | arXiv:2512.05400 | 2025 | https://arxiv.org/abs/2512.05400 | Hybrid grey-box identification improves prediction over pure physics or pure ML |
| 5 | **Field Demonstration of Predictive Heating Control for an All-Electric House in a Cold Climate** | arXiv:2402.07032 | 2024 | https://arxiv.org/abs/2402.07032 | Closest field analogue: RC model + MPC + air-source heat pump; ~20% energy savings; quadratic COP from manufacturer data |
| 6 | **Toward a Foundational Thermal Model for Residential Buildings** | arXiv:2605.01364 | 2026 | https://arxiv.org/abs/2605.01364 | Grey-box RC weakness is calibration staleness — motivates rolling re-estimation |
| 7 | **Solving Inverse Problems in Building Physics: Guidelines for Optimal Use of Data** | *Energy & Buildings* (review) | 2018 | https://srouchier.github.io/files/2018-enb-review.pdf | Canonical 2R2C inverse-problem framework; identifiability, regularisation, residual analysis |
| 8 | **Parameter Identification Methods for Low-Order Gray Box Building Energy Models: A Critical Review** | OSTI report | 2023 | https://www.osti.gov/servlets/purl/2405068 | Survey of EKF/MLE/CTSM methods for RC models; confirms EKF for state estimation during parameter optimisation |
| 9 | **Basics of Control Theory for Buildings (Lecture 9)** | MIT 12.770 course notes | — | https://sites.inferlab.org/courses/12-770/lectures/Lecture-9/lecture-9-notes.html | RC → discrete state-space for MPC; simpler model preferred when receding horizon compensates |
| 10 | **MPC-Based Hierarchical Control of a Multi-Zone Commercial HVAC System** | arXiv:2102.02914 | 2021 | https://arxiv.org/abs/2102.02914 | 2R2C aggregate zone model in production MPC; 24 h horizon, 15 min control interval |
| 11 | **Economic MPC for Building HVAC: Model-Based vs Data-Driven (BOPTEST)** | *Applied Energy* | 2024 | https://repository.hkust.edu.hk/ir/Record/1783.1-141552 | RC-MPC achieves 17–34% cost savings, 30–95% discomfort reduction vs rule-based; horizon and objective formulation are critical hyperparameters |
| 12 | **Demand Response for Residential Heating: PiNN + MCTS with 2R2C** | arXiv:2312.03365 | 2023 | https://arxiv.org/abs/2312.03365 | 2R2C physics-informed neural network for multi-step prediction; validates RC structure for residential heating |

**Takeaways:** Heating Assistant's architectural choices are well-aligned with the state of the art. The main literature gap is not model structure but **operational robustness**: keeping parameters, disturbance forecasts, and actuator models current over seasons.

---

## 2. Parameter Estimation & Rolling Re-Identification

Heating Assistant runs CD-EKF PED once after ~14 days and does not automatically re-run. Literature strongly supports periodic batch re-estimation.

| # | Title | Venue | Year | Link | Relevance |
|---|-------|-------|------|------|-----------|
| 13 | **Self-Excitation: Enabler for Online Thermal Estimation and MPC** | arXiv:1512.08169 | 2016 | https://arxiv.org/abs/1512.08169 | Online re-estimation directly improves MPC energy and comfort, not just model fit |
| 14 | **Parameter-Input Estimation of RC Models Using UKF and NLS** | *Indoor & Built Environment* | 2025 | https://pmc.ncbi.nlm.nih.gov/articles/PMC11798724/ | Parameter drift degrades MPC; periodic batch re-estimation recommended |
| 15 | **Transfer Learning for Neural Parameter Estimation Applied to Building RC Models** | arXiv:2604.05904 | 2026 | https://arxiv.org/abs/2604.05904 | 12-day training windows give good estimates; 21-day windows improve with diminishing returns |
| 16 | **Grey-Box Modeling Under Unmeasured Internal Heat Gains** | *Energy & Buildings* | 2024 | https://www.sciencedirect.com/science/article/abs/pii/S0378778824003451 | Unmeasured internal gains drift → parameter staleness in `Q_int` |
| 17 | **Parameter Estimation of RC Models Using UKF** | *Journal of Building Performance Simulation* | 2019 | https://www.sciencedirect.com/science/article/abs/pii/S2352710219330311 | Moving-window UKF for online RC calibration; validates rolling-window approach |
| 18 | **Online Simultaneous State Estimation and Parameter Adaptation** | TerraSwarm / Michigan Tech | 2016 | https://ptolemy.berkeley.edu/projects/terraswarm/pubs/90.html | EKF/UKF dual estimation for building predictive control |
| 19 | **Dual EKF/UKF with Implicit Euler at 15 min Sampling** | TU Delft thesis chapter | — | https://repository.tudelft.nl/file/File_8be6f093-3b61-41c2-bc66-c090c8743f1f | At 15 min sampling, explicit discretisation fails; implicit Euler required — matches Heating Assistant's integrator choice |

**Takeaways:** Re-estimate every 7–14 days over a 12–21 day rolling window. Guard against regression when a short noisy window produces worse innovation RMS. → **Action 1** in ACTIONS.md.

---

## 3. Heat Pump COP Modelling

Current implementation: Carnot-scaled COP from rated value. Field studies uniformly prefer manufacturer datasheet curves.

| # | Title | Venue | Year | Link | Relevance |
|---|-------|-------|------|------|-----------|
| 20 | **Field Demonstration of Predictive Heating (cold climate)** | arXiv:2402.07032 | 2024 | https://arxiv.org/abs/2402.07032 | Quadratic COP(T_out) from manufacturer data sufficient for MPC |
| 21 | **MPC Building Climate Control for Heat Pump Noise** | arXiv:2504.04182 | 2025 | https://arxiv.org/abs/2504.04182 | Piecewise-linear manufacturer COP beats Carnot scaling |
| 22 | **Predictive COP Model for Air-Source Heat Pumps (no experimental data)** | *Energy & Buildings* | 2025 | https://www.sciencedirect.com/article/pii/S0378778825004797 | Carnot-derived models systematically over-predict at extreme cold |
| 23 | **Protecting Residential Electrical Panels via MPC: Field Study** | arXiv:2409.04884 | 2024 | https://arxiv.org/abs/2409.04884 | Quadratic COP(T_out) from manufacturer data; outdoor-temperature-only sufficient |
| 24 | **Model Predictive Control of Residential Central Heating (economic MPC)** | Aalborg University report | 2023 | https://projekter.aau.dk/projekter/files/538104909/CA10_1033_2023_Residential_Heating_Economic_MPC.pdf | RC model + Luenberger observer + price-weighted MPC; dynamic horizon tied to price forecast |

**Takeaways:** Add optional `cop_table` interpolation with Carnot fallback. Surface instantaneous COP as diagnostic sensor. → **Action 2** in ACTIONS.md.

---

## 4. Weather Forecast & Disturbance Handling

Outdoor temperature from the weather entity is passed directly to the MPC horizon with no bias correction.

| # | Title | Venue | Year | Link | Relevance |
|---|-------|-------|------|------|-----------|
| 25 | **Disturbance-Adaptive Data-Driven Predictive Control** | arXiv:2412.09238 | 2024 | https://arxiv.org/abs/2412.09238 | Rolling forecast-error correction reduces comfort violations and energy waste |
| 26 | **Probabilistic Forecasting for Building Energy Systems** | arXiv:2506.00630 | 2025 | https://arxiv.org/abs/2506.00630 | Systematic NWP bias motivates post-processing |
| 27 | **Temperature Bias in AI Weather and Climate Models** | arXiv:2509.22359 | 2025 | https://arxiv.org/abs/2509.22359 | Measurable, learnable biases in modern NWP (incl. Met.no-class outputs) |
| 28 | **Post-Processing Weather Forecasts with ML** | arXiv:2604.19340 | 2026 | https://arxiv.org/abs/2604.19340 | Simple linear/affine correction captures majority of improvable error |
| 29 | **Adaptive MPC Climate Control Using Weather Forecast Data** | *Journal of Building Performance Simulation* | 2020 | https://www.sciencedirect.com/science/article/abs/pii/S2352710219323769 | UKF-adaptive model + MPC using weather forecast; startup vs steady-state phases |
| 30 | **An Adaptive MPC Scheme for Energy-Efficient HVAC Control** | arXiv:2102.03856 | 2021 | https://arxiv.org/abs/2102.03856 | Periodic model re-learning + disturbance predictor; occupied/unoccupied comfort bounds |
| 31 | **Scenario-based Nonlinear MPC for Building Heating** | arXiv:2012.02011 | 2020 | https://arxiv.org/abs/2012.02011 | 24 h prediction horizon (N_p=24 at 1 h steps); time-varying comfort bounds by occupancy |

**Takeaways:** Implement rolling 1-step-ahead bias EMA with horizon decay. Store paired forecast/actual in history buffer. → **Action 3** in ACTIONS.md.

---

## 5. MPC Prediction Horizon & Control Interval

Heating Assistant defaults: **100 steps × 900 s ≈ 25 h** horizon, **15 min** control interval.

| # | Title | Venue | Year | Link | Relevance |
|---|-------|-------|------|------|-----------|
| 32 | **MPC-Based Hierarchical Control (multi-zone)** | arXiv:2102.02914 | 2021 | https://arxiv.org/abs/2102.02914 | **N=96 steps × 15 min = 24 h** — industry-standard planning horizon |
| 33 | **Scenario-based NMPC for Building Heating** | arXiv:2012.02011 | 2020 | https://arxiv.org/abs/2012.02011 | **N_p=24 at 1 h = 24 h**; captures diurnal weather cycle |
| 34 | **Economic MPC Residential Heating (AAU)** | Aalborg report | 2023 | https://projekter.aau.dk/projekter/files/538104909/CA10_1033_2023_Residential_Heating_Economic_MPC.pdf | **Dynamic horizon** matched to electricity price forecast availability |
| 35 | **BOPTEST Economic MPC Comparison** | *Applied Energy* | 2024 | https://repository.hkust.edu.hk/ir/Record/1783.1-141552 | "Proper control horizon" is crucial; too short misses pre-heat opportunities, too long amplifies forecast error |
| 36 | **Coordination Architecture for Building Districts** | arXiv:2605.01362 | 2026 | https://arxiv.org/abs/2605.01362 | MPC horizon H as explicit tuning parameter; thermal mass exploited over multi-hour horizon |
| 37 | **Humidity-Aware MPC for Residential AC: Field Study** | arXiv:2407.01707 | 2024 | https://arxiv.org/abs/2407.01707 | Residential field MPC; setpoint tracking weight π trades comfort vs energy |

**Literature consensus on horizon:**

- **Minimum useful horizon:** ≥ 1 full diurnal cycle (**18–24 h**) for pre-heating against cold nights and price-aware scheduling.
- **Practical upper bound:** Beyond **36 h**, NWP forecast error growth dominates benefit; papers rarely exceed 48 h.
- **Heating Assistant's 25 h default is well-placed** but should be **documented as "~1 day"** and optionally **auto-sized to weather/price forecast length** (Action 4).
- **Control interval 15 min** is standard (papers #10, #19); 5–15 min for fast rooms, up to 30 min for high-inertia UFH.

**Thermal time constant rule of thumb:** Horizon should cover ≥ **3–5× the slowest room time constant**. For a typical room with τ_slow ≈ 4–8 h, 24 h covers 3–6τ — adequate for pre-heat scheduling.

---

## 6. Comfort Corridor, Soft Constraints & Setpoint Tracking

Heating Assistant uses **zone control**: `tracking_weight=0` by default, relying on soft corridor `[setpoint ± comfort_offset]` with `DEFAULT_COMFORT_OFFSET=2.0` °C and `soft_constraint_weight=1000`.

| # | Title | Venue | Year | Link | Relevance |
|---|-------|-------|------|------|-----------|
| 38 | **An Adaptive MPC Scheme for HVAC** | arXiv:2102.03856 | 2021 | https://arxiv.org/abs/2102.03856 | Occupied: [21.9, 23.6] °C (±0.85 °C); unoccupied: [21.1, 24.4] °C (±1.65 °C); slack variables for feasibility |
| 39 | **Scenario-based NMPC** | arXiv:2012.02011 | 2020 | https://arxiv.org/abs/2012.02011 | Occupied: [21.5, 24.0] °C (±1.25 °C); unoccupied: [18.0, 26.0] °C (±4 °C) |
| 40 | **MPC for Hydronic Heating (residential)** | ResearchGate / IFAC | 2017 | https://www.researchgate.net/publication/318653730 | Soft output constraints with scaled weight matrices; Q scaled by expected temperature range |
| 41 | **EPFL Building MPC Thesis (comfort slack formulation)** | EPFL Infoscience | — | https://infoscience.epfl.ch/bitstreams/313b0e12-2036-4f88-8259-4909ae583e00/download | Hard vs soft comfort constraints; smoothing term essential; ISO 7730 comfort limits |
| 42 | **Chance-Constraint MPC with RC Uncertainty (IBPSA ASim 2024)** | IBPSA proceedings | 2024 | https://publications.ibpsa.org/proceedings/asim/2024/papers/E10_asim2024_1109.pdf | Tighter confidence → narrower effective comfort band → less discomfort but more energy |
| 43 | **Adaptive Comfort Model (ASHRAE 55)** | CBE Berkeley | — | https://cbe.berkeley.edu/research/adaptive-comfort-model/ | Acceptable band widens with outdoor temperature in naturally ventilated buildings; ±2–3 °C adaptive range |
| 44 | **Analysis of Adaptive Building Controller** | *Energies* 15(3):1100 | 2022 | https://www.mdpi.com/1996-1073/15/3/1100 | Deadband widening when comfortable reduces energy; dynamic setpoint bounds |

**Literature guidance for `comfort_offset`:**

| Mode | Typical half-width | Total band | Source |
|------|-------------------|------------|--------|
| Occupied / comfort | 0.5–1.5 °C | 1–3 °C | Papers #38, #39 (tight occupied bounds) |
| Eco / setback | 1.5–3.0 °C | 3–6 °C | Paper #39 unoccupied |
| Heating Assistant default | **2.0 °C** | **4.0 °C** | Reasonable for eco; **slightly wide for tight occupied tracking** |

**Literature guidance for `soft_constraint_weight` (ρ):**

- ρ must be large enough that violations are rare but not so large the QP becomes ill-conditioned.
- Paper #38 uses high ρ with explicit slack ε; Paper #40 scales Q by 40× output range.
- Heating Assistant's ρ=1000 with 2 °C corridor implies ~4000 cost units per °C² violation — reasonable starting point.
- **Linear penalty** (`soft_constraint_linear_weight`) adds asymmetry for over-cooling vs under-heating — under-explored in current defaults (Action 5).

**`tracking_weight=0` (zone control only):** Supported by MPC practice when soft bounds carry the comfort objective (Paper #41). Enable small tracking weight (0.01–0.1) only when setpoint centre-tracking is desired alongside corridor.

---

## 7. MPC Cost Weight Tuning (R, S, Terminal, Price)

| # | Title | Venue | Year | Link | Relevance |
|---|-------|-------|------|------|-----------|
| 45 | **Tuning Guidelines for Model-Predictive Control** | NSF PAR | 2020 | https://par.nsf.gov/servlets/purl/10299968 | Systematic Q/R/Δu tuning; scale weights by expected operating ranges |
| 46 | **MPC Hydronic Heating (residential)** | IFAC | 2017 | https://www.researchgate.net/publication/318653730 | Input/output scaling before weight selection; R for power, Q for temperature |
| 47 | **Economic MPC Residential Heating** | Aalborg | 2023 | https://projekter.aau.dk/projekter/files/538104909/CA10_1033_2023_Residential_Heating_Economic_MPC.pdf | Price term in reference-tracking weight; 7.3% cost reduction |
| 48 | **BOPTEST EMPC Comparison** | *Applied Energy* | 2024 | https://repository.hkust.edu.hk/ir/Record/1783.1-141552 | Objective formulation accuracy matters as much as model accuracy |

**Current defaults vs literature:**

| Parameter | Current default | Literature recommendation | Action |
|-----------|----------------|---------------------------|--------|
| `energy_weight` (R) | 0.01 | Scale so typical u² term ≈ typical comfort violation cost; 0.001–0.5 range documented | Add tuning wizard with scaled preview |
| `smoothing_weight` (S) | 0.1 | Essential for oscillation suppression (Paper #41); 0.05–0.5 for heat pumps | Document; increase default for HP-heavy installs |
| `terminal_weight` (λ) | 100 | Force convergence by horizon end; 50–500 typical; P=λQ | Keep; expose schedule-dependent λ |
| `tracking_weight` (Q) | 0 | Zone control valid; 0.01–1.0 if centre tracking desired | Document trade-off in TUNING.md |
| `energy_price_weight` | 1.0 | Active when price entity configured | Validate against Paper #47 price-weight placement |

→ **Action 6** in ACTIONS.md.

---

## 8. EKF Process & Measurement Noise

| # | Title | Venue | Year | Link | Relevance |
|---|-------|-------|------|------|-----------|
| 49 | **Online Model Estimation (UKF/EKF comparison)** | arXiv:1601.02947 | 2016 | https://arxiv.org/abs/1601.02947 | UKF covariance metric prevents unnecessary parameter updates |
| 50 | **Dual EKF at 15 min with implicit Euler** | TU Delft | — | https://repository.tudelft.nl/file/File_8be6f093-3b61-41c2-bc66-c090c8743f1f | 15 min sampling validated for RC state/parameter estimation |
| 51 | **RC Parameter Estimation Critical Review** | OSTI | 2023 | https://www.osti.gov/servlets/purl/2405068 | Process noise covariance selection often undocumented; needs tuning |

**Heating Assistant defaults:** σ_w=0.1 K/√s, σ_v=0.5 K, σ_b=0.002 K/√s.

- σ_v=0.5 K accommodates typical residential sensor noise (±0.1–0.3 K precision).
- σ_w=0.1 allows ~0.04 K per 15 min step — reasonable for unmodelled disturbances.
- σ_b enables slow bias tracking for persistent model mismatch.
- **Window-open Q inflation (10×)** aligns with treating open windows as large process noise.

→ **Action 7**: Add innovation-based σ_v auto-tuning and document σ_w scaling with model confidence.

---

## 9. Emitter Dynamics & Slow Actuators

Heating Assistant models first-order emitter lag (`emitter_time_constant`) per source type — validated by hydronic/UFH literature.

| Source type | Default τ_em | Physical basis |
|-------------|-------------|----------------|
| Heat pump | 60 s | Compressor + refrigerant loop |
| Hydronic radiator | 600 s | Water mass + radiator body (~10 min) |
| Oil radiator | 1800 s | Oil reservoir buffer |
| UFH (electric/hydronic) | 3600 s | Screed slab thermal mass |

Paper #40 (hydronic MPC) explicitly models hydraulic delays as system parameters identified online. Heating Assistant's typology defaults are consistent; identified τ_em per installation would further improve slow-actuator rooms.

---

## 10. Summary: Literature → Implementation Priority

| Priority | Topic | Literature support | Heating Assistant gap |
|----------|-------|-------------------|----------------------|
| **P1 High** | Rolling parameter re-estimation | #2, #3, #13–16 | One-shot ID after 14 days |
| **P1 High** | Manufacturer COP table | #20–23 | Carnot-only |
| **P2 Medium** | Weather forecast bias correction | #25–28 | Raw NWP passed to MPC |
| **P2 Medium** | Horizon auto-sizing to forecast | #32–35 | Fixed 100 steps; no link to forecast length |
| **P2 Medium** | Comfort corridor guidance | #38–44 | Fixed 2 °C default; no schedule-aware presets |
| **P3 Lower** | MPC weight tuning wizard | #45–48 | Manual tuning only |
| **P3 Lower** | EKF noise auto-calibration | #49–51 | Fixed σ defaults |
| **P4 Future** | Chance-constraint / uncertainty MPC | #42 | Deterministic QP only |
| **P4 Future** | Adaptive comfort (ASHRAE 55) | #43–44 | Fixed symmetric corridor |

---

## References (quick index)

All links are open-access or author-hosted preprints unless marked otherwise.

1. https://www.mdpi.com/1996-1073/15/4/1328
2. https://arxiv.org/abs/2508.09118
3. https://arxiv.org/abs/1601.02947
4. https://arxiv.org/abs/2512.05400
5. https://arxiv.org/abs/2402.07032
6. https://arxiv.org/abs/2605.01364
7. https://srouchier.github.io/files/2018-enb-review.pdf
8. https://www.osti.gov/servlets/purl/2405068
9. https://sites.inferlab.org/courses/12-770/lectures/Lecture-9/lecture-9-notes.html
10. https://arxiv.org/abs/2102.02914
11. https://repository.hkust.edu.hk/ir/Record/1783.1-141552
12. https://arxiv.org/abs/2312.03365
13. https://arxiv.org/abs/1512.08169
14. https://pmc.ncbi.nlm.nih.gov/articles/PMC11798724/
15. https://arxiv.org/abs/2604.05904
16. https://www.sciencedirect.com/science/article/abs/pii/S0378778824003451
17. https://www.sciencedirect.com/science/article/abs/pii/S2352710219330311
18. https://ptolemy.berkeley.edu/projects/terraswarm/pubs/90.html
19. https://repository.tudelft.nl/file/File_8be6f093-3b61-41c2-bc66-c090c8743f1f
20–23. See Section 3
24. https://projekter.aau.dk/projekter/files/538104909/CA10_1033_2023_Residential_Heating_Economic_MPC.pdf
25. https://arxiv.org/abs/2412.09238
26. https://arxiv.org/abs/2506.00630
27. https://arxiv.org/abs/2509.22359
28. https://arxiv.org/abs/2604.19340
29. https://www.sciencedirect.com/science/article/abs/pii/S2352710219323769
30. https://arxiv.org/abs/2102.03856
31. https://arxiv.org/abs/2012.02011
32–37. See Section 5
38–44. See Section 6
45. https://par.nsf.gov/servlets/purl/10299968
46. https://www.researchgate.net/publication/318653730
47. https://projekter.aau.dk/projekter/files/538104909/CA10_1033_2023_Residential_Heating_Economic_MPC.pdf
48. https://repository.hkust.edu.hk/ir/Record/1783.1-141552
49–51. See Section 8
