# Research Literature: HVAC Building & Room Temperature Control

Compiled 2026-06-22. This document catalogues state-of-the-art academic literature relevant to the methods implemented in HeatingAssistant and to potential improvements of the system. Papers are organised by topic; each entry includes a relevance note relative to the current implementation.

---

## 1. Thermal Modelling – RC-Network Grey-Box Models

HeatingAssistant uses a **2R2C** (two-resistor, two-capacitor) grey-box model per room. The papers below cover the broader family of RC-network approaches, model-order selection, and comparative studies.

### 1.1 Foundational & Comparative

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 1 | **Comparing Building Thermal Dynamics Models and Estimation Methods for Grid-Edge Applications** | arXiv:2508.09118 | 2025 | https://arxiv.org/abs/2508.09118 | Directly compares RC-network vs. regression grey-box models; evaluates NLS, batch, and MLE estimation—identical to HeatingAssistant's CD-EKF PED approach. Highlights where grey-box models fail under arbitrary control policies. |
| 2 | **Building Thermal-Network Models: A Comparative Analysis, Recommendations, and Perspectives** | *Energies* 15(4):1328 (MDPI) | 2022 | https://www.mdpi.com/1996-1073/15/4/1328 | Compares 1R1C through 4R3C models; concludes that 2R2C and 3R2C give near-identical accuracy to 20th-order models for control purposes. Validates HeatingAssistant's 2R2C design choice. |
| 3 | **Toward a Foundational Thermal Model for Residential Buildings** | arXiv:2605.01364 | 2026 | https://arxiv.org/abs/2605.01364 | Identifies key limitations: physics-based models need building-specific calibration; data-driven models need large datasets; grey-box RC models struggle with nonlinearities. Discusses time-series foundation models as a future direction. |
| 4 | **JanusBM: A Dual-Fidelity Multi-Zone White-Box Building Modeling Framework** | arXiv:2603.23015 | 2026 | https://arxiv.org/abs/2603.23015 | Multi-zone model with explicit inter-zone heat transfer; dual-fidelity (simplified and detailed). Relevant to HeatingAssistant's inter-room resistance coupling. |
| 5 | **Real-world and Simulated Thermal Data from 960 Residential Multi-Zone Buildings in Central Europe** | arXiv:2606.01994 | 2026 | https://arxiv.org/abs/2606.01994 | Large-scale validation dataset for residential RC models across diverse building types. Could serve as benchmark for HeatingAssistant model accuracy. |

### 1.2 Higher-Order and Physics-Informed Approaches

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 6 | **Physics-Informed Machine Learning for Building Performance Simulation — A Review** | arXiv:2504.00937 | 2025 | https://arxiv.org/abs/2504.00937 | Comprehensive review of PIML for buildings; covers integration of RC models into deep-learning frameworks for predicting room and thermal-mass temperatures. Opportunity to improve HeatingAssistant's surrogate model for parameter estimation. |
| 7 | **Physics-Informed Neural Networks for Building Thermal Modeling and Demand Response Control** | *Energy & Buildings* / ResearchGate | 2023 | https://www.researchgate.net/publication/368969509 | PINNs constrained by thermodynamic laws; can replace or augment the current linearised model used in HeatingAssistant's MPC QP. |
| 8 | **Modularized Neural Network Incorporating Physical Priors for Smart Building Control** | arXiv:2412.02943 | 2024 | https://arxiv.org/abs/2412.02943 | Embeds physical structure into neural network layers; bridges grey-box (low data) and black-box (high accuracy) models. Potential for improved disturbance forecasting. |
| 9 | **Hybrid Modeling Approach for Better Identification of Building Thermal Network Model** | arXiv:2512.05400 | 2025 | https://arxiv.org/abs/2512.05400 | Two-step RC identification combining white-box priors with data-driven correction; directly applicable to HeatingAssistant's parameter_estimator. |
| 10 | **Grey-Box Modeling for Thermal Dynamics of Buildings Under the Presence of Unmeasured Internal Heat Gains** | *Energy & Buildings* (ScienceDirect) | 2024 | https://www.sciencedirect.com/science/article/abs/pii/S0378778824003451 | Two-step parameter identification framework for RC models under unobservable internal gains—directly relevant to HeatingAssistant's Q_int identifiability problem. |

---

## 2. State Estimation – Kalman Filtering & Parameter Identification

HeatingAssistant uses a **Continuous-Discrete EKF (CD-EKF)** for state estimation and **CD-EKF prediction-error decomposition (PED)** for maximum-likelihood parameter identification.

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 11 | **Parameter-Input Estimation of RC Thermal Models of Buildings Using Unscented Kalman Filter and Nonlinear Least Squares** | *Indoor & Built Environment* / PMC:11798724 | 2025 | https://pmc.ncbi.nlm.nih.gov/articles/PMC11798724/ | UKF-based joint state-parameter estimation; comparison point for HeatingAssistant's EKF approach. UKF achieves 3rd-order accuracy for nonlinear systems at similar cost. |
| 12 | **An Online Grey-Box Model Based on Unscented Kalman Filter to Predict Temperature Profiles in Smart Buildings** | *Energies* 13(8):2097 | 2020 | https://mdpi.com/1996-1073/13/8/2097/htm | Early demonstration of online UKF for building thermal grey-box; foundational reference for comparing EKF vs UKF in HeatingAssistant's estimator. |
| 13 | **Model-Based Monitoring and State Estimation for Digital Twins: The Kalman Filter** | arXiv:2305.00252 | 2023 | https://arxiv.org/abs/2305.00252 | General Kalman-filter digital-twin framework; anomaly detection use case. Relevant to future digital-twin dashboard capabilities. |
| 14 | **Online Model Estimation for Predictive Thermal Control** | arXiv:1601.02947 | 2016 | https://arxiv.org/abs/1601.02947 | Early online parameter estimation for MPC thermal control using EKF—foundational reference for HeatingAssistant's parameter_estimator design. |
| 15 | **Identifying Grey-box Thermal Models with Bayesian Neural Networks** | arXiv:2009.05889 | 2021 | https://arxiv.org/abs/2009.05889 | Bayesian NN for grey-box identification; quantifies parameter uncertainty. Could augment HeatingAssistant's scalar-valued parameter estimates with full posterior distributions. |
| 16 | **Sequential Bayesian Parameter-State Estimation in Dynamical Systems via a Variational Framework** | arXiv:2512.25056 | 2025 | https://arxiv.org/abs/2512.25056 | Joint parameter-state Bayesian estimation in continuous-discrete SDEs—theoretical backing for HeatingAssistant's CD-EKF PED approach and potential upgrade path. |

---

## 3. Model Predictive Control for Buildings

HeatingAssistant uses **linearised nonlinear MPC** (QP via OSQP/HiGHS) with an 8–12 hour receding horizon.

### 3.1 Deterministic MPC

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 17 | **Model Predictive HVAC Control with Online Occupancy Model** | arXiv:1403.4662 | 2014 | https://arxiv.org/abs/1403.4662 | Seminal MPC paper with occupancy-based setpoint adaptation; HeatingAssistant's comfort schedule feature covers this, but dynamic occupancy estimation is not yet implemented. |
| 18 | **Towards Machine Learning-based MPC for HVAC Control in Multi-Context Buildings at Scale via Ensemble Learning (ReeM)** | arXiv:2505.02439 | 2025 | https://arxiv.org/abs/2505.02439 | Ensemble-learning surrogate models to replace physics-based MPC at scale; on-site experiments at Osaka University Feb 2025. Comparison benchmark for HeatingAssistant's MPC performance. |
| 19 | **Distributed Model Predictive Control for Energy and Comfort Optimization in Large Buildings Using Piecewise Affine Approximation** | arXiv:2602.05376 | 2026 | https://arxiv.org/abs/2602.05376 | Decentralised MPC with piecewise-affine models; relevant to multi-room coordination in HeatingAssistant. |
| 20 | **Smart Building Energy Management using Nonlinear Economic MPC** | arXiv:1906.00362 | 2019 | https://arxiv.org/abs/1906.00362 | Economic MPC with time-varying electricity prices; validates HeatingAssistant's price-aware objective and highlights opportunity for full economic MPC formulation. |
| 21 | **Occupant-Oriented Demand Response with Multi-Zone Thermal Building Control** | arXiv:2301.03376 | 2023 | https://arxiv.org/abs/2301.03376 | Per-occupant comfort objectives in multi-zone MPC; directly applicable to HeatingAssistant's per-room comfort-band formulation. |

### 3.2 Stochastic and Uncertainty-Aware MPC

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 22 | **Chance-Constrained Stochastic Framework for Building Thermal Control Under Forecast Uncertainties** | *Energy & Buildings* (ScienceDirect) | 2025 | https://www.sciencedirect.com/science/article/abs/pii/S037877882500115X | CS-MPC outperforms deterministic MPC at 95% confidence; adds up to 7.53% energy cost. Directly relevant to replacing HeatingAssistant's hard soft-constraints with chance constraints. |
| 23 | **Partially Stochastic Deep Learning with Uncertainty Quantification for Model Predictive Heating Control** | arXiv:2504.03350 | 2025 | https://arxiv.org/abs/2504.03350 | Hybrid deterministic-stochastic MPC with UQ; balances robustness and efficiency. Potential upgrade to HeatingAssistant's QP formulation. |
| 24 | **Disturbance-Adaptive Data-Driven Predictive Control: Trading Comfort Violations for Savings** | arXiv:2412.09238 | 2024 | https://arxiv.org/abs/2412.09238 | Adaptive relaxation of comfort constraints when forecast errors are detected; relevant to HeatingAssistant's sigma_w / sigma_b noise tuning. |
| 25 | **Adaptive Relaxation Based Non-Conservative Chance Constrained Stochastic MPC** | arXiv:2406.01973 | 2024 | https://arxiv.org/abs/2406.01973 | Non-conservative chance constraints with adaptive relaxation; reduces conservatism vs. fixed tightening—applicable to HeatingAssistant's comfort corridor. |
| 26 | **Probabilistic Forecasting for Building Energy Systems Using Time-Series Foundation Models** | arXiv:2506.00630 | 2025 | https://arxiv.org/abs/2506.00630 | Probabilistic disturbance forecasts (temperature, solar, occupancy) for uncertainty-aware building control; benchmarks current foundation models for building forecasting. |

---

## 4. Reinforcement Learning for HVAC Control

RL approaches are a growing alternative to model-based MPC; relevant as benchmarks and as potential complementary controllers.

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 27 | **Experimental Evaluation of Offline Reinforcement Learning for HVAC Control in Buildings** | arXiv:2408.07986 | 2024 | https://arxiv.org/abs/2408.07986 | Offline RL reduces temp violations by 28.5% and energy by 12.1% vs. baseline; comparison baseline for HeatingAssistant. |
| 28 | **Efficient and Assured RL-based Building HVAC Control with Heterogeneous Expert-Guided Training** | *Scientific Reports* (Nature) | 2025 | https://www.nature.com/articles/s41598-025-91326-z | 8.8× DRL training speedup using physics expert functions; 'assured' = safety-constrained RL. Could seed RL policy from HeatingAssistant's MPC. |
| 29 | **Continual Reinforcement Learning for HVAC Control: Hypernetworks and Transfer Learning** | arXiv:2503.19212 | 2025 | https://arxiv.org/abs/2503.19212 | Model-based RL with hypernetworks for varying action spaces; relevant to HeatingAssistant's multi-source rooms (different actuator dimensionalities per room). |
| 30 | **RL Meets Urban Climate Modeling: Investigating RL-Based HVAC Control** | arXiv:2505.07045 | 2025 | https://arxiv.org/abs/2505.07045 | RL at city scale with climate-model coupling; long-term future direction if HeatingAssistant expands to district/neighbourhood scale. |
| 31 | **Quantifying the Energy Floor: SAC-Based HVAC Control on sbsim** | arXiv:2606.01665 | 2026 | https://arxiv.org/abs/2606.01665 | SAC RL energy-floor analysis; practical lower-bound benchmark for MPC comparison. |
| 32 | **Explainable Data-Driven Deep RL for Optimal Energy Management in Buildings** | arXiv:2606.02049 | 2026 | https://arxiv.org/abs/2606.02049 | LSTM-forecast + DRL with SHAP explainability; relevant to interpretability of HeatingAssistant's control decisions. |

---

## 5. Demand Response, Grid Flexibility & Energy Storage

HeatingAssistant supports price-aware pre-heating via the `price_entity`; these papers cover the broader demand-response ecosystem.

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 33 | **Unlocking Energy Flexibility From Thermal Inertia of Buildings: A Robust Optimization Approach** | arXiv:2312.05108 | 2023 | https://arxiv.org/abs/2312.05108 | Robust optimisation that explicitly exploits thermal inertia as flexibility asset—directly maps to HeatingAssistant's wall thermal mass. |
| 34 | **Trajectory-Independent Flexibility Envelopes of Energy-Constrained Systems with State-Dependent Losses** | arXiv:2505.16396 | 2025 | https://arxiv.org/abs/2505.16396 | Analytic flexibility envelopes for RC building models; enables HeatingAssistant to report its available demand-response capacity to the grid. |
| 35 | **Uncertainty-Aware Flexibility of Buildings: From Quantification to Provision** | arXiv:2510.00858 | 2025 | https://arxiv.org/abs/2510.00858 | Linear state-space model for uncertainty-aware flexibility provision; relevant to probabilistic flexibility reporting from HeatingAssistant. |
| 36 | **An Optimal Battery-Free Approach for Emission Reduction by Storing Solar Surplus in Building Thermal Mass** | arXiv:2603.28217 | 2026 | https://arxiv.org/abs/2603.28217 | Solar surplus stored directly in building thermal mass without batteries; complements HeatingAssistant's solar gain model + price-aware pre-heating. |
| 37 | **MuFlex: A Scalable, Physics-based Platform for Multi-Building Flexibility Analysis** | arXiv:2508.13532 | 2025 | https://arxiv.org/abs/2508.13532 | Multi-building flexibility aggregation; future path if multiple HeatingAssistant instances coordinate. |
| 38 | **Protecting Residential Electrical Panels via MPC: A Field Study** | arXiv:2409.04884 | 2024 | https://arxiv.org/abs/2409.04884 | MPC field study with real heat pump, electrical panel constraints, and quadratic COP model fitted to manufacturer data—useful reference for HeatingAssistant's heat pump COP parameterisation. |

---

## 6. Heat Pump Modelling & COP

HeatingAssistant uses a Carnot-derived COP curve. These papers examine more detailed COP models.

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 39 | **A Predictive COP Model for Air-Source Heat Pumps Under Extreme Heat Conditions Using No Experimental Data** | *Energy & Buildings* (ScienceDirect) | 2025 | https://www.sciencedirect.com/science/article/pii/S0378778825004797 | Purely theory-derived COP curves; validates HeatingAssistant's Carnot approach and suggests enhancements for extreme-condition accuracy. |
| 40 | **Determining Optimal Thermal Energy Storage Charging Temperature for Cooling Using Integrated Building and Coil Modeling** | arXiv:2601.10976 | 2026 | https://arxiv.org/abs/2601.10976 | Integrated building + refrigerant-loop model; identifies COP degradation at extreme supply temperatures—relevant to HeatingAssistant's heat pump cooling mode. |

---

## 7. Solar Irradiance Forecasting

HeatingAssistant has a detailed clear-sky solar model; forecast integration uses Open-Meteo or a sensor entity.

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 41 | **SPIRIT: Short-term Prediction of Solar IRradIance for Zero-Shot Transfer Learning Using Foundation Models** | arXiv:2502.10307 | 2025 | https://arxiv.org/abs/2502.10307 | 70% error reduction over persistence baseline; zero-shot transfer = no local calibration. Could replace/augment HeatingAssistant's solar clear-sky model with local nowcasting. |
| 42 | **Short-Term Solar Irradiance Forecasting Under Data Transmission Constraints** | arXiv:2403.12873 | 2024 | https://arxiv.org/abs/2403.12873 | Lightweight ML model (MAE 74 W/m² vs. 134 W/m² persistence); relevant for edge-device solar forecasting within Home Assistant. |
| 43 | **Data-Driven Solar Forecasting Enables Near-Optimal Economic Decisions** | arXiv:2509.06925 | 2025 | https://arxiv.org/abs/2509.06925 | Links solar forecast quality to economic decision quality—justifies investing in better solar forecasting for HeatingAssistant's price-aware control. |

---

## 8. Occupancy Prediction & Thermal Comfort Modelling

HeatingAssistant uses time-based comfort schedules; occupancy estimation is static (not dynamic).

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 44 | **Evaluation of Thermal Control Based on Spatial Thermal Comfort with Reconstructed Environmental Data** | arXiv:2505.00468 | 2025 | https://arxiv.org/abs/2505.00468 | Spatial PMV/PPD control integrating reconstructed sensor data; relevant to extending HeatingAssistant's scalar setpoint to comfort-index-based control. |
| 45 | **Optimizing HVAC Systems with MPC: Integrating Ontology-Based Semantic Models for Energy Efficiency and Comfort** | *Frontiers in Energy Research* | 2025 | https://www.frontiersin.org/articles/10.3389/fenrg.2025.1542107/full | MPC with PMV-based comfort constraint; increases comfort time by 86.51%. Directly applicable to replacing HeatingAssistant's temperature setpoint with a comfort index. |
| 46 | **Experimental Study on Surveillance Video-Based Indoor Occupancy Measurement with Occupant-Centric Control** | arXiv:2603.26081 | 2026 | https://arxiv.org/abs/2603.26081 | Vision-based occupancy detection for HVAC; could inform HeatingAssistant's occupancy signal via a local camera integration. |
| 47 | **Data-Driven Thermal Comfort Modeling: Comparing AI-Based Predictions with PMV-PPD Models** | *Building & Environment* (ScienceDirect) | 2025 | https://www.sciencedirect.com/science/article/abs/pii/S0378778825011405 | AI vs. PMV for comfort prediction; highlights limitations of steady-state PMV for transient MPC scenarios in HeatingAssistant. |

---

## 9. Online Learning, Transfer Learning & Adaptive Models

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 48 | **Forecasting Residential Heating and Electricity Demand with Scalable, High-Resolution, Open-Source Models** | arXiv:2505.22873 | 2025 | https://arxiv.org/abs/2505.22873 | Building-level energy forecasting with transfer across building archetypes; 18% lower RMSE vs. ResStock. Useful for cold-start parameter seeding in HeatingAssistant. |
| 49 | **Human-in-the-Loop Simulation for Real-Time Exploration of HVAC Demand Flexibility** | arXiv:2508.07314 | 2025 | https://arxiv.org/abs/2508.07314 | Interactive simulation for scenario exploration; relevant to HeatingAssistant's dashboard preview/forecast visualisation. |
| 50 | **Emerging Paradigms in the Energy Sector: Forecasting and System Control Optimisation** | arXiv:2507.12373 | 2025 | https://arxiv.org/abs/2507.12373 | Survey of ML + MPC hybrid approaches for smart building optimisation with weather forecasting—broad context for HeatingAssistant's roadmap. |

---

## 10. Validation Datasets & Benchmarks

| # | Title | Authors / Venue | Year | Link | Relevance |
|---|-------|-----------------|------|------|-----------|
| 51 | **CityLearn Dataset: 247 Residential Buildings (EnergyPlus / RESSTOCK)** | *NeurIPS* datasets track | 2022 | https://citylearn.net | Standard benchmark for building RL and MPC controllers; HeatingAssistant's model could be validated against this dataset. |
| 52 | **sbsim: SAC-Based HVAC Simulation Benchmark** | arXiv:2606.01665 | 2026 | https://arxiv.org/abs/2606.01665 | Open simulation benchmark for comparing RL vs. MPC energy floors in buildings. |
| 53 | **Real-world and Simulated Thermal Data from 960 Residential Buildings (Central Europe)** | arXiv:2606.01994 | 2026 | https://arxiv.org/abs/2606.01994 | Largest publicly available residential thermal dataset; could replace HeatingAssistant's 14-day cold-start with architecture-informed priors. |

---

## Summary of Key Gaps vs. Current Implementation

| Gap | Most Relevant Papers |
|-----|---------------------|
| Static comfort schedules — no dynamic occupancy | #17, #44, #45, #46 |
| Deterministic disturbance forecasts — no uncertainty propagation to QP | #22, #23, #24, #25, #26 |
| Carnot-only COP model — no part-load or defrost modelling | #39, #40, #38 |
| No solar irradiance nowcasting | #41, #42, #43 |
| 2R2C fixed order — no model-order selection or higher-order option | #2, #6, #9 |
| Offline-only parameter estimation (runs after 14 days) | #11, #14, #16 |
| No demand-response / flexibility API | #33, #34, #35, #36, #37 |
| No PMV/comfort-index-based setpoints | #44, #45, #47 |
| EKF only — no UKF or posterior uncertainty on parameters | #11, #12, #15, #16 |
| No RL benchmark comparison | #27, #28, #31 |
