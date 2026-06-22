# Research Literature: HVAC Building & Room Temperature Control

Compiled 2026-06-22. Focused on literature that directly validates the current HeatingAssistant implementation (2R2C model, CD-EKF state estimator, linearised MPC, CD-EKF PED parameter estimation) and supports the three targeted improvements described in ACTION_PLAN.md.

---

## 1. Validation of the Current 2R2C + CD-EKF + MPC Architecture

These papers confirm that the core design choices in HeatingAssistant — lumped-parameter RC models, Kalman filtering, and linearised MPC — are well-supported by the literature.

| # | Title | Authors / Venue | Year | Link |
|---|-------|-----------------|------|------|
| 1 | **Building Thermal-Network Models: A Comparative Analysis, Recommendations, and Perspectives** | *Energies* 15(4):1328, MDPI | 2022 | https://www.mdpi.com/1996-1073/15/4/1328 |
| 2 | **Comparing Building Thermal Dynamics Models and Estimation Methods for Grid-Edge Applications** | arXiv:2508.09118 | 2025 | https://arxiv.org/abs/2508.09118 |
| 3 | **Online Model Estimation for Predictive Thermal Control of Buildings** | arXiv:1601.02947 | 2016 | https://arxiv.org/abs/1601.02947 |
| 4 | **Hybrid Modeling Approach for Better Identification of Building Thermal Network Model and Improved Prediction** | arXiv:2512.05400 | 2025 | https://arxiv.org/abs/2512.05400 |
| 5 | **Field Demonstration of Predictive Heating Control for an All-Electric House in a Cold Climate** | arXiv:2402.07032 | 2024 | https://arxiv.org/abs/2402.07032 |
| 6 | **Toward a Foundational Thermal Model for Residential Buildings** | arXiv:2605.01364 | 2026 | https://arxiv.org/abs/2605.01364 |

**Key takeaways:**

- Paper #1 shows that 2R2C and 3R2C models give near-identical accuracy compared to 20th-order models for control purposes, directly validating HeatingAssistant's 2R2C design.
- Paper #2 benchmarks RC-network models with MLE estimation (HeatingAssistant's exact approach) against structured regression models; MLE RC performs best under matched training conditions but degrades when the model is misidentified or the training window doesn't match current dynamics — motivating Improvement 1 (rolling re-estimation).
- Paper #3 (Candanedo et al., 2016) is the foundational paper for continuous/periodic re-estimation of RC grey-box models for MPC. It explicitly motivates replacing batch-only identification with rolling identification.
- Paper #5 is a real field demonstration in a cold climate (Indiana, outdoor temps to −15°C) with an air-source heat pump, using a grey-box RC building model and MPC — the closest published analogue to HeatingAssistant's architecture. It achieves ~20% HVAC energy savings and models COP as a quadratic function of outdoor temperature fitted to manufacturer data, motivating Improvement 2.
- Paper #6 confirms that grey-box RC models' main weakness is calibration staleness and limited generalisability — both addressed by rolling re-estimation.

---

## 2. Supporting Literature for Improvement 1: Periodic Rolling Re-Estimation

The current parameter estimator runs once after 14 days and is not scheduled thereafter. These papers establish why this matters and what re-estimation frequency is appropriate.

| # | Title | Authors / Venue | Year | Link |
|---|-------|-----------------|------|------|
| 7 | **Self-Excitation: An Enabler for Online Thermal Estimation and Model Predictive Control of Buildings** | arXiv:1512.08169 | 2016 | https://arxiv.org/abs/1512.08169 |
| 8 | **Parameter-Input Estimation of RC Thermal Models of Buildings Using UKF and Nonlinear Least Squares** | *Indoor & Built Environment*, PMC:11798724 | 2025 | https://pmc.ncbi.nlm.nih.gov/articles/PMC11798724/ |
| 9 | **Transfer Learning for Neural Parameter Estimation Applied to Building RC Models** | arXiv:2604.05904 | 2026 | https://arxiv.org/abs/2604.05904 |
| 10 | **Grey-Box Modeling for Thermal Dynamics of Buildings Under the Presence of Unmeasured Internal Heat Gains** | *Energy & Buildings*, ScienceDirect | 2024 | https://www.sciencedirect.com/science/article/abs/pii/S0378778824003451 |

**Key takeaways:**

- Paper #3 (above) and paper #7 together make the most direct case: building thermal dynamics are time-varying (seasonal occupancy changes, solar angle changes, insulation degradation), and a controller that re-identifies the model periodically maintains accuracy far better than one calibrated once.
- Paper #7 (Radecki & Hencey) demonstrates through simulation that online model re-estimation directly improves both energy savings and comfort satisfaction in MPC — not just model fit quality.
- Paper #8 (2025) shows that parameter drift is a real phenomenon in RC building models and is a primary cause of MPC performance degradation. It evaluates several re-estimation strategies (batch, recursive, periodic batch) and recommends periodic batch re-estimation over a rolling window as the best balance of accuracy and computational cost — exactly what Improvement 1 proposes.
- Paper #9 (2026) provides empirical evidence that **12-day training windows** yield good RC parameter estimates, and longer windows (up to ~21 days) improve accuracy with diminishing returns. This supports the rolling 21-day window proposed in the action plan.
- Paper #10 highlights the challenge of unmeasured internal gains drifting over time (occupancy, appliances), which is a direct source of parameter staleness in HeatingAssistant's `Q_int` term.

---

## 3. Supporting Literature for Improvement 2: Heat Pump COP via Manufacturer Data

HeatingAssistant's current COP model uses a Carnot-derived curve. These papers establish what models real deployments use and why manufacturer data matters.

| # | Title | Authors / Venue | Year | Link |
|---|-------|-----------------|------|------|
| 11 | **Field Demonstration of Predictive Heating Control for an All-Electric House in a Cold Climate** (see also #5) | arXiv:2402.07032 | 2024 | https://arxiv.org/abs/2402.07032 |
| 12 | **Model Predictive Building Climate Control for Mitigating Heat Pump Noise Pollution** | arXiv:2504.04182 | 2025 | https://arxiv.org/abs/2504.04182 |
| 13 | **A Predictive COP Model for Air-Source Heat Pumps Under Extreme Heat Conditions Using No Experimental Data** | *Energy & Buildings*, ScienceDirect | 2025 | https://www.sciencedirect.com/article/pii/S0378778825004797 |
| 14 | **Protecting Residential Electrical Panels via Model Predictive Control: A Field Study** | arXiv:2409.04884 | 2024 | https://arxiv.org/abs/2409.04884 |

**Key takeaways:**

- Paper #11 (Pergantis et al.) is the most directly relevant: a real deployment of RC-model MPC with an air-to-air heat pump uses a **quadratic COP curve fitted to manufacturer data** as a function of outdoor temperature only, and reports it provides sufficient accuracy for MPC. They explicitly compare this to first-principles approaches and find the data-fitted model superior.
- Paper #12 uses piecewise-linear heat pump COP approximations derived from empirical manufacturer data and shows this gives more accurate energy predictions than Carnot scaling.
- Paper #13 benchmarks several purely theory-based COP models (including Carnot-derived) against measured data and finds systematic over-prediction at extreme cold temperatures — exactly the operating regime where HeatingAssistant needs accuracy most.
- Paper #14 (field study with an MPC residential heat pump) explicitly states: "COP was modelled as a quadratic function of outdoor temperature fitted to manufacturer data for varying indoor and outdoor temperatures; modelling COP as a function only of outdoor temperature gave sufficient accuracy." This is a direct recommendation for HeatingAssistant.

---

## 4. Supporting Literature for Improvement 3: Adaptive Forecast Bias Correction

The MPC disturbance forecast currently uses the weather entity's outdoor temperature directly. These papers establish that systematic NWP forecast bias is a primary driver of MPC sub-optimality.

| # | Title | Authors / Venue | Year | Link |
|---|-------|-----------------|------|------|
| 15 | **Disturbance-Adaptive Data-Driven Predictive Control: Trading Comfort Violations for Savings in Building Climate Control** | arXiv:2412.09238 | 2024 | https://arxiv.org/abs/2412.09238 |
| 16 | **Probabilistic Forecasting for Building Energy Systems Using Time-Series Foundation Models** | arXiv:2506.00630 | 2025 | https://arxiv.org/abs/2506.00630 |
| 17 | **Forecasting the Future with Yesterday's Climate: Temperature Bias in AI Weather and Climate Models** | arXiv:2509.22359 | 2025 | https://arxiv.org/abs/2509.22359 |
| 18 | **Improvements to the Post-Processing of Weather Forecasts Using Machine Learning and Feature Selection** | arXiv:2604.19340 | 2026 | https://arxiv.org/abs/2604.19340 |

**Key takeaways:**

- Paper #15 (Schalbetter et al., 2024) is the most operationally relevant: it shows that **adapting the MPC to track recent forecast-vs-actual discrepancies** significantly reduces comfort violations and energy waste compared to using raw NWP forecasts. The correction mechanism they demonstrate is simple — a rolling bias estimate — and is directly implementable in HeatingAssistant's `weather.py` / `coordinator.py`.
- Paper #16 quantifies how probabilistic building energy forecasts improve over deterministic baselines; its analysis of systematic NWP temperature bias motivates the bias-correction approach.
- Papers #17 and #18 confirm that modern NWP outputs (including Met.no, the most common weather entity in Home Assistant) have consistent, measurable biases that are partially correctable with simple post-processing. Paper #18 shows that even a linear correction captures the majority of the improvable error, which validates the rolling affine correction in Improvement 3.
