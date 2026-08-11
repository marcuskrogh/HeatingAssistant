# Physics, Models & Control Theory

> In-depth description of the methods behind Heating Assistant — the room
> thermal model, the solar-gain pipeline, the heat-source models, and the
> model-predictive controller (MPC) with its continuous-discrete extended
> Kalman filter (CD-EKF) state estimator.

This document is for readers who want to understand *why* the App behaves the
way it does. For installation and day-to-day use, start with the
[main README](../README.md). For estimating the parameters these models need,
and for tuning the controller, see the [Tuning guide](TUNING.md).

**Contents**

- [3. Physics and Mathematical Models](#3-physics-and-mathematical-models)
- [4. Model Predictive Controller](#4-model-predictive-controller)
- [References](#18-references)

---

## 3. Physics and Mathematical Models

### 3.1 Lumped RC thermal model (2R2C)

Each room is treated as a **two-node** lumped-parameter thermal circuit:

- A fast **air node** $T_{a,i}$ — room air plus light furnishings.  This is the temperature you measure, perceive, and set.
- A slow **wall node** $T_{w,i}$ — walls, floor, ceiling and heavy furniture.  Never measured directly; the state estimator reconstructs it (§4.2).
- An internal coupling resistance $R_{aw,i}$ between the two nodes.
- A conduction resistance $R_{we,i}$ from the wall to the outdoor air, plus a direct air↔outdoor **infiltration** conductance $g_{\text{inf},i}$ (air exchange does not pass through the wall mass).
- Optional inter-room resistances $R_{ij}$ coupling adjacent rooms **wall-to-wall**.

The continuous-time energy balance per room is:

$$C_{a,i} \cdot \frac{dT_{a,i}}{dt} = Q_{\text{heater},i} + Q_{\text{int},i} + (1 - w_s)\, s_i\, Q_{\text{solar},i} + \frac{T_{w,i} - T_{a,i}}{R_{aw,i}} + g_{\text{inf},i} \,(T_{\text{outdoor}} - T_{a,i})$$

$$C_{w,i} \cdot \frac{dT_{w,i}}{dt} = \frac{T_{a,i} - T_{w,i}}{R_{aw,i}} + \frac{T_{\text{outdoor}} - T_{w,i}}{R_{we,i}} + w_s\, s_i\, Q_{\text{solar},i} + \sum_{j \in \text{adj}(i)} \frac{T_{w,j} - T_{w,i}}{R_{ij}}$$

**Why two nodes.**  A single node has a single time constant, but real rooms respond on two: a fast air response (½–1½ h) and a slow envelope response (5–30 h).  That second mode is precisely what price-driven anticipatory heating exploits — pre-heating during cheap hours stores energy in the walls and releases it later — so a model that cannot represent storage-then-release systematically mispredicts the multi-hour trajectories the optimiser plans over.

**How the user-facing parameters keep their old meaning.**  You still configure (and the estimator still identifies) a single total `thermal_mass` $C_i$ and total `r_external` $R_{i,\text{ext}}$ per room.  Two bounded **split fractions** derive the node-level quantities:

| Derived quantity | Formula | Source |
|------------------|---------|--------|
| $C_{a,i}$ | `c_air_fraction` · $C_i$ | split fraction (default 0.05; identified when the data allow) |
| $C_{w,i}$ | $(1 - $`c_air_fraction`$)$ · $C_i$ | — |
| $g_{\text{inf},i}$ | `infiltration_fraction` $/ R_{i,\text{ext}}$ | envelope-tightness preset (wind-modulated at runtime, §3.1b) |
| $g_{\text{cond},i}$ | $(1 - $`infiltration_fraction`$) / R_{i,\text{ext}}$ | the conductive remainder |
| $R_{aw,i}$ | `r_aw_fraction` $/\, g_{\text{cond},i}$ | split fraction (default 0.05; identified when the data allow) |
| $R_{we,i}$ | $(1 - $`r_aw_fraction`$) /\, g_{\text{cond},i}$ | — |

By construction $g_{\text{inf}} + (R_{aw} + R_{we})^{-1} = 1/R_{\text{ext}}$, so at steady state the air node settles at exactly $T_{\text{outdoor}} + Q \cdot R_{\text{ext}}$ — identical to the previous single-node model.  In the limit `r_aw_fraction` → 0 the two nodes lock together and the model degenerates to the old 1R1C with $(C, R_{\text{ext}})$.  Existing configurations and previously identified parameters therefore carry over unchanged.

**Symbol table**

| Symbol | Unit | Meaning |
|--------|------|---------|
| $C_i$ | J/K | Total thermal mass of room $i$ (user input `thermal_mass`). |
| $T_{a,i}$, $T_{w,i}$ | °C | Air-node and wall-node temperatures (state variables; only $T_{a,i}$ is measured). |
| $R_{i,\text{ext}}$ | K/W | Total steady-state resistance to outdoors (user input `r_external`). |
| $R_{ij}$ | K/W | Inter-room (wall-to-wall) resistance (user input `r_value` on connections). |
| $Q_{\text{heater},i}$ | W | Heat-source power — lands on the **air** node. |
| $Q_{\text{int},i}$ | W | Identified internal heat gain — air node. |
| $Q_{\text{solar},i}$ | W | Modelled solar gain (§3.4), split between the nodes. |
| $s_i$ | – | Per-room **solar scale**, identified from data (default 1; §3.4 Step 6). |
| $w_s$ | – | `SOLAR_WALL_FRACTION` = 0.5 — the share of transmitted solar absorbed by floor/wall surfaces rather than the air.  Fixed, not identified (nearly collinear with the split fractions). |

Three optional envelope corrections attach to the **wall** node: a linearised long-wave radiative conductance to the sky (`sky_radiative_ua`, with a constant cooling drift $-\text{UA}_{\text{sky}} \cdot \Delta T_{\text{sky}}$ attenuated by the live cloud cover), the sol-air facade share (`facade_absorptance` · `facade_solar_share` of the solar gain), and a thermal-bridge correction (`thermal_bridge_psi_l`).  All default to off.

**Observability.**  The wall node is reconstructed by the EKF from the air measurement alone.  Two diagnostics watch the health of that reconstruction: the per-room *wall-temperature* sensor exposes the EKF posterior std (should contract after start-up and stay bounded) and an *observability* metric — the conditioning of the room's observability Gramian (1 = ideal, ≈ 0 = the wall is practically invisible).  Rooms whose split fractions cannot be identified simply keep their typology defaults; the identification gates and tight priors (§14) prevent the estimator from chasing parameters the data cannot constrain — the failure mode that sank the first 2R2C attempt.

### 3.2 State-space matrix form

For a house with *n* rooms, the 2R2C network assembles into a compact matrix form once at startup.  The physical state vector stacks the air block first, then the wall block: $\mathbf{x} = [T_{a,1}, \ldots, T_{a,n},\; T_{w,1}, \ldots, T_{w,n}]$ of length $2n$:

$$\mathbf{C} \cdot \frac{d\mathbf{x}}{dt} = \mathbf{A} \cdot \mathbf{x} + \mathbf{B}_{\text{ext}} \cdot T_{\text{outdoor}} + \mathbf{Q}(t)$$

where:

- $\mathbf{C}$ is a $2n$-vector of capacitances: $[C_{a,1}, \ldots, C_{a,n}, C_{w,1}, \ldots, C_{w,n}]$.
- $\mathbf{A}$ is a $2n \times 2n$ conductance matrix.  Air row $i$: diagonal $-(g_{\text{inf},i} + 1/R_{aw,i})$ and coupling $+1/R_{aw,i}$ to its own wall column.  Wall row $n{+}i$: coupling $+1/R_{aw,i}$ to its air column, diagonal $-(1/R_{aw,i} + 1/R_{we,i} + \text{UA}_{\text{sky},i} + \Psi L_i + \sum_j 1/R_{ij})$, and $+1/R_{ij}$ to connected rooms' wall columns.
- $\mathbf{B}_{\text{ext}}$ is a $2n$-vector: $g_{\text{inf},i}$ on the air rows, $1/R_{we,i} + \text{UA}_{\text{sky},i} + \Psi L_i$ on the wall rows.
- $\mathbf{Q}(t)$ is the $2n$-vector of heat inputs: heater + internal gains + the air share of solar on the air rows; the wall share of solar (plus the sol-air facade term) on the wall rows.

Keeping the measured air block in positions $0\ldots n{-}1$ means the measurement model stays the identity on the leading block, and everything downstream (EKF update, comfort constraints, dashboards) keeps addressing rooms by the same indices as before.  Each `step()` call remains a single $2n \times 2n$ linear solve — the per-room eigenvalue pair (one fast, one slow, stiffness ratio $10^2$–$10^4$) is exactly the regime the implicit-Euler integrator in §3.3 was adopted for.

### 3.3 Continuous-discrete integration

The MPC controller treats the thermal model as a **continuous-discrete stochastic differential equation (CD-SDE)**.  Given the nonlinear continuous-time drift:

$$\dot{\mathbf{x}}(t) = \mathbf{f}(\mathbf{x}, \mathbf{u}, \mathbf{d}, t) = \mathbf{F}\,\mathbf{x} + \mathbf{G}_u(T_{\text{out}})\,\mathbf{u} + \mathbf{G}_d\,\mathbf{d}$$

with $\mathbf{F} = \mathbf{C}_{\text{cap}}^{-1}\,\mathbf{A}$, the state is propagated over each sub-step $h = dt / n_{\text{int\_steps}}$ using **implicit (backward) Euler**:

$$\mathbf{x}(t + h) = \mathbf{x}(t) + h\,\mathbf{f}(\mathbf{x}(t + h), \mathbf{u}, \mathbf{d}, t + h)$$

The implicit form is solved by Newton iteration on the residual

$$R(\mathbf{x}_{k+1}) = \mathbf{x}_{k+1} - \mathbf{x}_k - h\,\mathbf{f}(\mathbf{x}_{k+1}, \mathbf{u}, \mathbf{d}, t_{k+1})$$

with Jacobian $\mathbf{I} - h\,\partial \mathbf{f}/\partial \mathbf{x}$.  For the residential thermal model the drift is affine in the state (heat-pump COP varies with the *disturbance* $T_{\text{out}}$, not with the state itself), so the residual is linear in $\mathbf{x}_{k+1}$ and Newton converges in a single iteration — i.e. one $n \times n$ linear solve per sub-step.

**Why implicit Euler.**  The scheme is **L-stable**: it stays accurate on the slow modes regardless of step size and damps fast modes correctly.  The first-order accuracy is acceptable for control purposes — we care about stability and the slow modes, not third-decimal-place fidelity.  This matters concretely for the 2R2C model: the per-room fast/slow eigenvalue spread of $10^2$–$10^4$ would make explicit Euler conditionally stable at best, while the implicit scheme integrates it at the full 15-minute step.

The CD-EKF propagates both the mean state and the error covariance matrix using the same scheme.  In the implementation, the EKF reuses `mbc`'s native `scheme="implicit-euler"` mode (Newton iteration on the mean ODE; covariance propagated by the one-step sensitivity matrix $\Phi = (I - h\,A_{n+1})^{-1}$).  The `HouseModel.step()` / `HouseModel.predict()` methods, the controller's visualisation prediction loop, and the open-loop diagnostic simulator all share a single integration helper (`heatingassistant/engine/integrator.py`) so the integration scheme is uniform across the codebase.

For typical residential buildings (large thermal masses, slow dynamics) a control time step `update_interval ≤ 900 s` (15 minutes) gives accurate results with the default 10 integration sub-steps.  The default `update_interval = 900 s` is a good balance between prediction accuracy and computational load.

### 3.4 Solar heat gain model

The solar disturbance pipeline converts geographic location + time + window geometry into watts of heat entering each room.

#### Step 1 — Solar position

The sun's position is expressed as **altitude** α (angle above the horizon, radians) and **azimuth** A (degrees clockwise from North).

1. **Day-of-year** *n* is extracted from the datetime.
2. **Equation of time** (Spencer 1971) corrects for the eccentricity and obliquity of Earth's orbit:

   $$B = \frac{360}{365} \cdot (n - 81) \quad [\text{degrees}]$$

   $$\text{EoT [min]} = 9.87 \sin(2B) - 7.53 \cos(B) - 1.5 \sin(B)$$

3. **Solar declination** (Cooper equation):

   $$\delta = 23.45° \cdot \sin\!\left(\frac{360}{365} \cdot (n - 81)\right) \quad [\text{converted to radians}]$$

4. **Apparent solar time** corrects UTC for longitude and EoT.
5. **Hour angle** $\omega = 15° \times (\text{solar time} - 12)$ [degrees → radians].
6. **Altitude** from the spherical-trigonometry formula:

   $$\sin \alpha = \sin \varphi \cdot \sin \delta + \cos \varphi \cdot \cos \delta \cdot \cos \omega$$

   where $\varphi$ is the geographic latitude.
7. **Azimuth** from the clockwise-from-South formula, then converted to clockwise-from-North.

#### Step 2 — Clear-sky Direct Normal Irradiance (DNI)

The extra-terrestrial irradiance is corrected for the Earth–Sun distance:

$$G_{on} = 1361 \cdot \left(1 + 0.033 \cdot \cos\!\left(\frac{360 \cdot n}{365}\right)\right) \quad [\text{W/m}^2]$$

The air-mass number uses the Kasten & Young (1989) formula which avoids a singularity at the horizon:

$$am = \frac{1}{\sin \alpha + 0.50572 \cdot (\alpha_{\text{deg}} + 0.07628)^{-1.6364}}$$

Atmospheric transmittance (Meinel & Meinel approximation):

$$\tau_b = 0.56 \cdot \left(e^{-0.65 \cdot am} + e^{-0.095 \cdot am}\right)$$

$$\text{DNI} = G_{on} \cdot \tau_b \quad [\text{W/m}^2]$$

#### Step 3 — Beam/diffuse split (Erbs correlation, all paths)

Every intensity source — clear sky, cloud-attenuated, or forecast — supplies only a **GHI magnitude**; the split into beam (DNI) and diffuse (DHI) is always done by the same **Erbs correlation** (Erbs, Klein & Duffie 1982).  The diffuse fraction follows from the clearness index $K_t = \text{GHI} / (G_{on} \sin \alpha)$:

$$\text{DHI} = f_{\text{Erbs}}(K_t) \cdot \text{GHI}, \qquad \text{DNI} = \frac{\text{GHI} - \text{DHI}}{\sin \alpha}$$

Under a clear sky ($K_t \approx 0.7$) roughly 20 % of the radiation is diffuse; fully overcast ($K_t \lesssim 0.2$) essentially all of it is.  Using one decomposition everywhere means the clear, cloudy, and forecast paths agree exactly at their seams (e.g. ``cloud_cover = 0`` is identical to "no cloud data") and the beam correctly collapses faster than the diffuse as cloud builds — which is what an *oriented* window experiences.

#### Step 4 — Angle of incidence on the window

The angle $\theta$ between the direct beam and the surface normal is:

$$\cos \theta = \cos \alpha \cdot \cos \gamma \cdot \sin \beta + \sin \alpha \cdot \cos \beta$$

where $\beta$ is the surface tilt from horizontal (90° = vertical) and $\gamma$ is the relative azimuth (sun azimuth − surface azimuth).

#### Step 5 — Irradiance on the window

Three components:

$$I_{\text{direct}} = \max(0,\; \text{DNI} \cdot \cos \theta \cdot \text{IAM}(\theta))$$

$$I_{\text{diffuse}} = \text{DHI} \cdot \frac{1 + \cos \beta}{2} \quad \text{(Liu and Jordan isotropic sky model)}$$

$$I_{\text{ground}} = \rho \cdot \text{GHI} \cdot \frac{1 - \cos \beta}{2} \quad \text{(isotropic ground reflection)}$$

$$I_{\text{window}} = I_{\text{direct}} + I_{\text{diffuse}} + I_{\text{ground}} \quad [\text{W/m}^2]$$

The **incidence-angle modifier** $\text{IAM}(\theta) = \max(0,\, 1 - b_0 (1/\cos\theta - 1))$ with $b_0 = 0.1$ accounts for the sharp drop in glazing transmittance at grazing beam incidence (the constant SHGC is quoted at normal incidence).

The **ground-reflected** term uses the site-level albedo $\rho$ (`ground_albedo`, default 0.2 for grass/soil).  For a vertical window it equals $\rho \cdot \text{GHI}/2$ — with snow on the ground ($\rho \approx 0.7$–$0.8$) a dominant winter component at high latitudes, which is exactly the heating season.  Set the albedo higher for sites with persistent winter snow cover.

#### Step 6 — Solar heat gain

$$Q_{\text{solar}} = \text{SHGC} \cdot \text{area} \cdot I_{\text{window}} \quad [\text{W}]$$

The **Solar Heat Gain Coefficient** SHGC = 0.6 is the default (typical clear double glazing).  This constant is defined in `solar_model.py` as `DEFAULT_SHGC`.

**The magnitude is calibrated from data, not trusted.**  The configured windows (or exposure preset) only set the *prior* solar aperture.  The parameter estimator identifies a per-room multiplicative **solar scale** $s_i$ on top of it (§14) — shading, curtains, trees, dirt, frame fraction and preset error all land in $s_i$.  The pipeline above always reports the *unscaled* gain (that is also what gets recorded in the history buffer, so re-estimating the scale later stays meaningful); the scale is applied in exactly one place, inside the model dynamics.  A converged $s_i$ far from 1 is itself useful feedback: your window list or preset is off by that factor.

Inside the thermal model the (scaled) gain splits between the two room nodes: $(1 - w_s)$ to the air and $w_s$ = 0.5 to the wall/mass — transmitted shortwave is mostly absorbed by floor and wall surfaces, which is part of why solar warmth lingers after the sun moves off the facade.

#### Cloud cover correction

When a `weather_entity` is configured, cloud cover is extracted from the weather forecast (as an explicit field or mapped from the weather condition string, e.g. `sunny` → 0.0, `cloudy` → 0.85).  The clear-sky **GHI** is attenuated by the Kasten–Czeplak factor $1 - 0.75\,c^{3.4}$ and the attenuated GHI is re-decomposed through the Erbs correlation (Step 3) — so the *total* horizontal radiation follows the classic single-factor model exactly, while the beam/diffuse balance shifts toward diffuse as cloud builds.

#### Step 9 — Optional: drive the intensity from a solar-radiation forecast

> **This is about the sun's radiation, not solar panels.**  The input here is a forecast of **solar irradiance** in W/m² — the energy arriving from the sun — *not* PV / solar-panel production.  No panel geometry, peak power, or inverter data is involved.

The pipeline above is factored into three layers so the **intensity** can be swapped for a measured forecast while the geometry stays put:

1. **Geometry** — sun position, incidence angle per surface, sky-view factor, and the extra-terrestrial reference $G_{on}$.  Pure almanac/trigonometry; shared by both intensity sources.
2. **Intensity** *(swappable)* — the irradiance for the current sky:
   * **Fallback** — the clear-sky model (Steps 2–5) attenuated by the Kasten–Czeplak cloud factor.  This is what runs with no forecast configured.
   * **Forecast** — a measured/forecast **Global Horizontal Irradiance (GHI)** in W/m² from a solar-radiation sensor.
3. **Window coupling** — decompose the intensity into beam (DNI) + diffuse (DHI) and transpose onto each window (Step 6), then apply SHGC·area.

When a forecast GHI is available it is decomposed into beam/diffuse using the **Erbs correlation** (`erbs_diffuse_fraction`), which derives the diffuse fraction from the clearness index $K_t = \text{GHI} / (G_{on}\sin\alpha)$ — geometry plus the forecast only, **no clear-sky transmittance assumption**.  The beam then follows the cosine of the incidence angle and the diffuse the sky-view factor, exactly as in Step 6.  So the forecast supplies the *magnitude* of the sun's radiation (including real cloud, haze, and the cloud-driven shift toward diffuse light) while the model supplies only the *geometry* that redistributes it onto your specific windows.

Configure it with the optional `solar_radiation_entity` — a sensor whose value is solar irradiance in W/m², ideally exposing an hourly forecast series in its attributes (a `forecast`/`shortwave_radiation`/`ghi`/`irradiance` list of `{datetime, value}` or a `{timestamp: value}` map).  The current state is used for the present step; forecast entries cover the horizon, and any step beyond the forecast's coverage falls back to the clear-sky model.  The forecast drives every window/room; a room with no enumerated windows uses its `solar_exposure` preset instead.  The fallback is automatic and total: if the entity is missing, unavailable, stale, or carries no usable irradiance, the cycle silently reverts to the clear-sky + cloud model — so configuring a forecast can only help, never break, the solar estimate.  The diagnostic `sensor.heating_assistant_solar_radiation_forecast_status` reports whether the forecast or the analytical model is active, the current GHI, and the horizon GHI series.

##### Where to get a solar-radiation forecast in Home Assistant

Most Home Assistant "solar forecast" integrations (Forecast.Solar, Solcast, Open-Meteo **Solar** Forecast) predict **PV-panel production** and are **not** what this needs — they answer "how many watts will my panels make", not "how much sun is hitting the ground".  Look instead for a source of **solar irradiance / shortwave radiation in W/m²**:

1. **[Open-Meteo](https://open-meteo.com/en/docs) `shortwave_radiation` (recommended, free, no API key).**  The Open-Meteo forecast API exposes `shortwave_radiation` (GHI), plus `direct_radiation` / `diffuse_radiation`, in W/m².  Expose it as a Home Assistant sensor — e.g. via a [RESTful sensor](https://www.home-assistant.io/integrations/rest/) hitting the forecast API, putting the hourly series in an attribute — and point `solar_radiation_entity` at it.  This is the cleanest fit: a true GHI forecast over the full horizon.
2. **A physical pyranometer / solar-radiation sensor** (e.g. many weather stations report W/m²).  Gives an accurate *current* GHI; without a forecast attribute only the present step uses it and later steps fall back to the clear-sky model, so it mainly sharpens "now".
3. **[OpenWeatherMap Solar Irradiance API](https://openweathermap.org/api/solar-energy-prediction)** or similar paid irradiance APIs, surfaced as a W/m² sensor.

**Met.no and other standard weather integrations do *not* expose solar irradiance.**  The Met.no integration provides **cloud coverage** (which the analytical model already consumes via the `weather_entity` for its Kasten–Czeplak attenuation — Step 4) and a *current* **UV index**, but no broadband GHI and no radiation *forecast*.  UV index is a narrow-band proxy that varies independently of total shortwave radiation, so it isn't used as an irradiance source.  If all you have is Met.no, you already get its benefit through the cloud-cover path; to genuinely replace the modelled radiation with a forecast, add an irradiance source such as Open-Meteo's `shortwave_radiation`.

### 3.5 Heat source models

**Emitter filter.**  Each heat source carries an `emitter_time_constant` $\tau_\text{em} \ge 0$ that captures the dominant valve / metal-mass / water-loop lag without requiring supply-temperature telemetry.  When $\tau_\text{em} > 0$ the source's *commanded* fraction $u_j(t)$ is passed through a first-order filter to produce an *effective* fraction $\phi_j(t)$:

$$\frac{d\phi_j}{dt} = \frac{u_j - \phi_j}{\tau_{\text{em},j}}$$

The model's thermal-power calculation then uses $\phi_j$ in place of $u_j$:

$$Q_j(t) = \text{thermal\_power}(\phi_j(t),\; T_\text{out})$$

Adding the filter introduces one state variable per filtered source.  The EKF and OCP track $\phi$ alongside the temperature states, so the controller anticipates the emitter lag rather than treating commanded power as instantaneous.

**Typology defaults**

| Source type | Default $\tau_\text{em}$ | Rationale |
|---|---|---|
| `electric_heater` | 0 s | Resistive coils heat in seconds — effectively instantaneous. |
| `heat_pump`       | 60 s | Indoor unit + refrigerant loop have ~1 minute of internal thermal mass. |
| Hydronic radiator | 600 s (user-configured) | Water loop + metal mass; set explicitly via the per-source `emitter_time_constant` field. |

When $\tau_\text{em} = 0$ the filter is bypassed — $u_j$ flows directly to `thermal_power`, recovering the pre-filter behaviour for that source.

#### Electric heater

$$Q_{\text{thermal}} = P_{\max} \cdot u \cdot \eta$$

- $P_{\max}$ — rated maximum electrical (= thermal) power [W].
- $u$ — control signal in $[0, 1]$ (fractional output chosen by the MPC controller).
- $\eta$ — efficiency, default 1.0.  For a purely resistive heater $\eta = 1.0$ exactly (all electrical energy becomes heat).  Infrared heaters can be specified with $\eta < 1$ if a fraction is radiated outside the thermal envelope.

The outdoor temperature has no effect on an electric heater's output.

#### Air-source heat pump

The electrical input power is fixed at $P_{\text{elec}} = P_{\max} / \text{COP}_{\text{rated}}$.  The actual thermal output is:

$$\text{COP}(T_{\text{outdoor}}) = \max\!\left(1,\; \text{COP}_{\text{rated}} \cdot \frac{\text{COP}_{\text{Carnot}}(T_{\text{outdoor}})}{\text{COP}_{\text{Carnot}}(T_{\text{ref}})}\right)$$

$$Q_{\text{thermal}} = P_{\text{elec}} \cdot u \cdot \text{COP}(T_{\text{outdoor}}) = \frac{P_{\max}}{\text{COP}_{\text{rated}}} \cdot u \cdot \text{COP}(T_{\text{outdoor}})$$

The Carnot COP at temperature $T_{\text{outdoor}}$ is computed assuming a fixed **supply temperature of 35 °C** (typical for low-temperature underfloor or radiator systems):

$$T_{\text{supply}} = 35 + 273.15 \quad [\text{K}]$$

$$\text{COP}_{\text{Carnot}}(T) = \frac{T_{\text{supply}}}{\max(T_{\text{supply}} - T,\; 1)}$$

The `max(…, 1)` guard ensures the COP never falls below 1.0 (even in extreme cold, a heat pump is at least as efficient as direct electric heating).

**Minimum outdoor temperature:** if `T_outdoor < −20 °C` the heat pump shuts off completely (`COP = 0`) to represent the compressor lock-out that real units implement to avoid defrost damage.  This threshold is hardcoded and not currently configurable via YAML.

**Offset-based setpoint control:** when the heat pump is connected via a `climate.*` entity, Heating Assistant reads the heat pump's own internal temperature sensor (`current_temperature` attribute) and sets the heat pump's target temperature to:

$$T_{\text{target}} = T_{\text{hp,internal}} + \text{fraction} \times \text{max temp offset}$$

where `max_temp_offset` (default 5 °C) is the maximum temperature differential at full power.  This makes the heat pump modulate its own output based on the gap between the setpoint it receives and its own temperature reading.  If the heat pump's internal temperature is unavailable, the HA room temperature is used as a fallback.

**Hysteresis deadband:** the integration tracks a per-heat-pump cooling/heating state and uses separate thresholds for mode transitions, so a small temperature fluctuation near the setpoint cannot cause rapid toggling:

| Transition | Temperature condition | Result |
|:---|:---|:---|
| heating/idle → cooling | `room_temp > setpoint + turn_off_deadband` | Enter cooling mode |
| cooling → heating/idle | `room_temp < setpoint − turn_off_deadband` | Exit cooling mode |
| (no transition) | `setpoint − deadband ≤ room_temp ≤ setpoint + deadband` | **Hold** current mode |

Once in either mode, the unit stays there until the temperature crosses the opposite threshold.  Within the dead-band window (width = 2 × `turn_off_deadband`) the mode is locked.

**Dispatch inside each mode** (checked only after hysteresis resolves the mode):

| Mode | MPC fraction | Action |
|:---|:---:|:---|
| Cooling | any | Switch to `dry` (or `fan_only`); target = T_hp_internal − (1 °C + overshoot) |
| Heating/idle | `> 0` | Heat mode; target = T_hp_internal + fraction × max_temp_offset |
| Heating/idle | `= 0` | Idle heat mode; target = T_hp_internal (no offset — HP produces near-zero heat) |

The heat pump **never turns fully off** via HVAC mode in normal operation.  When cooling, the idle setpoint offset grows with overshoot: `target = T_hp_internal − (1.0 + overshoot)` where `overshoot = max(0, room_temp − setpoint)`.  This keeps the cooling effect proportional to how far above the setpoint the room is.

**Cooling mode (dry/dehumidify):** when the room temperature exceeds the setpoint, the heat pump automatically switches to a gentle cooling mode to help bring the temperature down. The integration prefers "dry" (dehumidify) mode if available, which provides passive cooling without running the compressor at full cooling capacity. If "dry" mode is not supported, it falls back to "fan_only" mode.

When in cooling mode, the heat pump actively removes heat from the room. The cooling capacity exposed to the thermal model and the advanced visualisation sensors is **derived from the cooling COP (EER), not from the heating thermal max**. Specifically:

```
electric_max          = max_power / cop_rated         [rated electrical input, W]
cooling_capacity_max  = electric_max × cooling_cop    [rated heat removal, W]
cooling_power         = − cooling_capacity_max × cooling_efficiency × power_scale
```

This corrects an earlier bug where a 6.6 kW *heating* heat pump was reported as having 6.6 kW of cooling capability — the cooling capacity is now the physically correct value (typically ≈ 70 % of the heating max for a heat pump where `cop_rated = 3.5` and `cooling_cop = 2.5`).

Two new heat-source configuration keys control the cooling cycle:

| Key | Default | Description |
|-----|---------|-------------|
| `cooling_cop` | `2.5` | Rated cooling coefficient of performance (EER) used when the heat pump is in dry / fan-only / cool mode. |
| `cooling_efficiency` | `1.0` | Fraction (0–1) of the rated cooling capacity actually delivered.  Lower the value (e.g. `0.4`) when relying on `dry` mode for gentle dehumidification. |

The signed cooling power is exposed as a negative value on the per-room **Heating Power**, **Heating Plan**, and **Energy Balance** sensors, and the per-room **Climate** entity reports `HVACAction.COOLING` (instead of `idle`) whenever a heat pump in the room is removing heat.  Heat-pump-equipped rooms also advertise `HVACMode.HEAT_COOL` so the HA frontend renders the cooling state with the correct icon.

**Example COP curve** (COP_rated = 3.5, T_ref = 7 °C):

| Outdoor temp | COP (approx.) |
|:---:|:---:|
| 15 °C | 4.1 |
| 7 °C | 3.5 (rated) |
| 0 °C | 3.0 |
| −7 °C | 2.6 |
| −15 °C | 2.1 |
| −20 °C | 0.0 (shut-off) |

---

## 4. Model Predictive Controller

### 4.1 Overview

The controller (`heatingassistant/engine/controller/`) implements a **linearised
model-predictive control (MPC)** architecture built on the `mbc` (model-based
control) package:

| Component | Class (from `mbc`) | Role |
|-----------|-------|------|
| **System model** | `ContinuousDiscreteModel` (ABC) | Defines the continuous-discrete SDE: `dx = f(x,u,d,p,t)dt + σdw`, `ym = hm(x,...)`. |
| **State estimator** | `ContinuousDiscreteEKF` | CD-EKF: integrates the drift and linearised Riccati ODE between measurement steps using implicit-Euler sub-stepping. |
| **Optimal control** | `CDLinearizedMPCController` | Linearizes around the current operating point, discretizes via ZOH, and solves the resulting batch convex QP via OSQP/HiGHS. |

The house-heating application provides these classes in
`heatingassistant/engine/controller/`:

| Class | Role |
|-------|------|
| `HouseThermalSDE` | Concrete `ContinuousDiscreteModel` wrapping `HouseModel` and `HeatSource` objects.  Nonlinearity in the plant (e.g. heat-pump COP vs outdoor temperature) is handled by linearisation at the operating point. |
| `HeatingMPCController` / `HeatingLinearisedMPC` | Application facade.  Builds the SDE, CD-EKF, and linearised MPC; adds solar/outdoor forecasting; applies source set-points; exposes visualisation properties for the App runtime. |

At each control step the controller:

1. Reads room temperatures from HA sensors via the MQTT bridge (measurement vector **y**).
2. Builds an *N*-step disturbance forecast **D** (outdoor temperature + solar gains).
3. Runs the CD-EKF to obtain the state estimate **x̂**.
4. Solves the QP to find the optimal continuous input sequence **U***.
5. Applies only the **first step** u*[0] of the optimal sequence (receding horizon).

### 4.2 State estimation — Continuous-Discrete EKF

The state estimator is a **Continuous-Discrete Extended Kalman Filter (CD-EKF)** from the `mbc` package (`mbc.estimation.ContinuousDiscreteEKF`).  Between consecutive measurement times $t_{k-1}$ and $t_k$ the filter integrates the continuous-time mean and covariance using implicit-Euler sub-steps:

**Prediction (continuous-time integration over $[t_{k-1}, t_k]$):**

$$\frac{d\hat{\mathbf{x}}}{dt} = \mathbf{f}(\hat{\mathbf{x}}, \mathbf{u}, \mathbf{d}, \mathbf{p}, t)$$

$$\frac{d\mathbf{P}}{dt} = \mathbf{F}\,\mathbf{P} + \mathbf{P}\,\mathbf{F}^\top + \boldsymbol{\sigma}\,\boldsymbol{\sigma}^\top \qquad \text{(continuous Riccati ODE)}$$

where $\mathbf{F} = \partial\mathbf{f}/\partial\mathbf{x}$ is the analytic state Jacobian (constant for the linear drift, so this reduces to a Lyapunov ODE).

**Update (at measurement time $t_k$):**

$$\mathbf{S}[k] = \mathbf{H}\,\mathbf{P}^{-}[k]\,\mathbf{H}^\top + \mathbf{R}_m \qquad \text{(innovation covariance)}$$

$$\mathbf{K}[k] = \mathbf{P}^{-}[k]\,\mathbf{H}^\top\,\mathbf{S}[k]^{-1} \qquad \text{(Kalman gain)}$$

$$\hat{\mathbf{x}}[k] = \hat{\mathbf{x}}^{-}[k] + \mathbf{K}[k]\bigl(\mathbf{y}[k] - \mathbf{h}_m(\hat{\mathbf{x}}^{-}[k])\bigr) \qquad \text{(corrected estimate)}$$

$$\mathbf{P}[k] = \bigl(\mathbf{I} - \mathbf{K}[k]\,\mathbf{H}\bigr)\,\mathbf{P}^{-}[k] \qquad \text{(posterior covariance)}$$

| Symbol | Meaning |
|--------|---------|
| $\boldsymbol{\sigma}$ | Diffusion matrix — $\sigma_w \mathbf{I}$ for isotropic process noise (default $\sigma_w = 0.1$ K/√s). |
| $\mathbf{R}_m$ | Measurement noise covariance — $\sigma_v^2 \mathbf{I}$ (default $\sigma_v = 0.5$ K). |
| $\mathbf{P}$ | State error covariance — propagated at every step; determines the Kalman gain. |
| $\mathbf{H} = \partial\mathbf{h}_m/\partial\mathbf{x}$ | Observation Jacobian — identity matrix for full-state observation. |

For the house thermal system with full-state observation ($\mathbf{h}_m = \mathbf{I}$, one temperature sensor per room), the Kalman gain converges quickly to a value that weights measurements heavily relative to the model prediction.  The filter provides robustness against temporary sensor noise and gradual model drift.

### 4.3 Optimal control problem — batch QP

The cost function over the prediction horizon *N* is:

$$J(\mathbf{U}) = \sum_{k=0}^{N-2} \left\lVert \mathbf{z}[k{+}1] - \mathbf{z}_{\text{ref}} \right\rVert_{\mathbf{Q}}^2 + \left\lVert \mathbf{z}[N] - \mathbf{z}_{\text{ref}} \right\rVert_{\mathbf{P}}^2 + \sum_{k=0}^{N-1} \left( \left\lVert \mathbf{u}[k] \right\rVert_{\mathbf{R}}^2 + \left\lVert \Delta\mathbf{u}[k] \right\rVert_{\mathbf{S}}^2 \right) + \rho_z \sum_{k=1}^{N} \left\lVert \max(0, \mathbf{z}[k] - \mathbf{z}_{\max}) \right\rVert^2 + \left\lVert \max(0, \mathbf{z}_{\min} - \mathbf{z}[k]) \right\rVert^2$$

where $\Delta\mathbf{u}[k] = \mathbf{u}[k] - \mathbf{u}[k{-}1]$ (with $\mathbf{u}[-1]$ equal to the previous step's applied input):

| Symbol | Value / meaning |
|--------|----------------|
| $\mathbf{z}[k] = \mathbf{g}(\mathbf{x}[k])$ | Controlled output — room temperatures — at step *k* |
| $\mathbf{z}_{\text{ref}}$ | Reference (room setpoints) |
| $\mathbf{Q}$ | Stage output tracking cost (default: $\mathbf{I}$) |
| $\mathbf{P}$ | Terminal output tracking cost (`terminal_weight` × $\mathbf{Q}$, default: $100 \cdot \mathbf{I}$).  A large value strongly encourages the predicted trajectory to reach the setpoint by the end of the horizon, significantly improving steady-state tracking. |
| $\mathbf{R}$ | Input cost (`energy_weight` × $\mathbf{I}$, default: $0.01 \cdot \mathbf{I}$) |
| $\mathbf{S}$ | Input rate-of-change cost (`smoothing_weight` × $\mathbf{I}$, default: $0.1 \cdot \mathbf{I}$).  Set `smoothing_weight` to `0.0` to disable. |
| $\mathbf{z}_{\min}, \mathbf{z}_{\max}$ | Soft output constraint bounds: $\mathbf{z}_{\text{ref}} \pm \delta$ where $\delta$ = `constraint_offset` (default 2.0 °C) |
| $\rho_z$ | Soft constraint penalty weight (default: $10^4$) |
| $\mathbf{u}[k]$ | Input vector (continuous fractions $\in [0, 1]$) |

The predicted state trajectory is propagated by linearizing the nonlinear SDE around the current operating point (x̂, u_prev, d_now) using the analytic Jacobians, then discretizing the local linear model via ZOH.  The resulting convex QP is solved via **OSQP/HiGHS** with box constraints $0 \le \mathbf{u}[k] \le 1$.

The **terminal cost** $\mathbf{P}$ is the key mechanism for achieving setpoint tracking.  Without a large terminal weight the optimizer has weak incentive to drive the state to the reference by the end of the horizon — it can minimise total cost by spreading the error across all stages without converging.  Setting $\mathbf{P} = \lambda \mathbf{Q}$ with $\lambda \gg 1$ (default $\lambda = 100$) is equivalent to approximating the infinite-horizon cost and forces the optimal trajectory to converge to the setpoint well within the horizon.

The input cost $\mathbf{R}$ softly discourages running heaters when the room is close to setpoint.  Increasing `energy_weight` makes the controller more energy-conservative at the expense of tighter temperature tracking.

The smoothing cost $\mathbf{S}$ penalises *changes* in the control input from one step to the next.  This prevents the controller from toggling heaters on and off aggressively, resulting in more stable actuator commands and less wear on compressor-based heat sources.  Increasing `smoothing_weight` makes the controller more reluctant to change its actions between time steps.

**On-off sources** (e.g. `switch.*` entities) are modelled with a duty-cycle relaxation: the MPC optimises the continuous fraction $u \in [0, 1]$, interpreted as the proportion of the sampling interval `update_interval` for which the source is active.  The App runtime maps this fraction to on/off commands on the bridged HA entity.

### 4.4 Disturbance forecasts

The controller builds a disturbance forecast matrix $\mathbf{D} \in \mathbb{R}^{N \times n_d}$ before solving the QP:

The disturbance vector has the layout $\mathbf{d} = [T_{\text{out}},\, q_{\text{solar},1..n},\, q_{\text{air},1..n}]$: the (unscaled) modelled solar gain per room, and a direct air-node heat channel carrying the identified internal gain plus the decaying online gain deviation.

| Disturbance | Forecast method |
|-------------|----------------|
| **Outdoor temperature** | If a `weather_entity` is configured (e.g. the Met.no integration), the controller uses the weather forecast temperatures interpolated to each horizon step.  Otherwise, it falls back to persistence: the current measured value is held constant for all horizon steps.  Configure `outdoor_temp_entity` for the current measurement and `weather_entity` for the forecast. |
| **Solar gains** | The solar position model is evaluated at times `now + (k+1)·update_interval` for each horizon step `k = 0, …, N−1`, matching the end of each prediction interval.  This uses the deterministic orbital equations and produces an accurate prediction of how solar irradiance through each window will evolve over the next `N·update_interval` seconds.  The same time convention is used for the outdoor temperature forecast so that both disturbance components are evaluated consistently. |
| **Wind speed** | When the weather forecast exposes `wind_speed`, the per-step values drive the Sherman–Grimsrud infiltration overlay in the nonlinear prediction rollout, and the QP linearisation uses the horizon-mean wind (the wind enters through a conductance, which the linearised model freezes).  Without a forecast the current wind is held over the horizon. |
| **Online gain deviation** | The EKF's estimated internal-gain deviation $\Delta \hat g$ is an Ornstein–Uhlenbeck state that mean-reverts with rate $\kappa$.  Over the horizon the air-heat channel carries the decaying value $\Delta \hat g \, e^{-\kappa k \Delta t}$ instead of freezing it — a transient unmodelled gain (sun through an unmodelled window, an oven) no longer biases the tail of a 12–24 h plan. |

### 4.5 Control cycle

Each call to `HeatingMPCController.compute()` follows this sequence:

```
compute(outdoor_temp, solar_gains=None, now=None, outdoor_forecast=None)
│
├─ if solar_gains is None: compute from solar model
├─ if outdoor_forecast provided: use weather forecast
│  else: _forecast_outdoor(outdoor_temp)  → list of N floats (persistence)
├─ _forecast_solar(now)                → list of N {room: W} dicts
├─ Build D ∈ ℝ^(N × nd) from forecasts
│
├─ ContinuousDiscreteEKF.step(y, u, d, p, t)  → x̂  (state estimate)
│   ├─ predict: integrate dx/dt = f(x,u,d,p,t) and dP/dt = FP+PFᵀ+σσᵀ
│   └─ update:  K = P⁻Hᵀ(HP⁻Hᵀ+Rm)⁻¹,  x̂ = x̂⁻ + K(y − hm(x̂⁻))
│
├─ CDLinearizedMPCController.solve(x̂, D)
│   ├─ linearize: ∂f/∂x, ∂f/∂u evaluated at (x̂, u_prev, d_now)
│   ├─ discretize: ZOH → local linear model (A_d, B_d)
│   ├─ QP: min Σ ‖z[k]-z_ref‖²_Q + ‖u[k]‖²_R + ‖Δu[k]‖²_S + ρ_z·corridor_violation
│   └─ solve via OSQP/HiGHS  s.t.  0 ≤ u ≤ 1
│
└─ Apply u*[0] to heat sources (receding horizon)
   Return {source_name: fraction}
```

**Open-window override (Phase 3 W1).** If a room has configured
`window_sensors`, the coordinator runs a per-room state machine
(`closed → pending_open → open → pending_closed → closed`). While the room is
in `open`, every heat source assigned to that room is clamped to `u = 0` at
dispatch, and the EKF process-noise covariance for that room is inflated by
`window_open_q_inflation` so the estimator tracks rapid cooling instead of
lagging.

---
## 18. References

1. ISO 13790:2008 — *Energy performance of buildings – Calculation of energy use for space heating and cooling.*
2. Duffie, J. A. & Beckman, W. A. (2013) — *Solar Engineering of Thermal Processes*, 4th edition, Wiley.
3. ASHRAE Fundamentals Handbook (2021), Chapter 14 — *Climatic Design Information.*
4. Kasten, F. & Young, A. T. (1989) — "Revised optical air mass tables and approximation formula", *Applied Optics*, 28(22), 4735–4738.
5. Spencer, J. W. (1971) — "Fourier series representation of the position of the sun", *Search*, 2(5), 172.
6. Cooper, P. I. (1969) — "The absorption of radiation in solar stills", *Solar Energy*, 12(3), 333–346.
7. Liu, B. Y. H. & Jordan, R. C. (1960) — "The interrelationship and characteristic distribution of direct, diffuse and total solar radiation", *Solar Energy*, 4(3), 1–19.
8. Mayne, D. Q. et al. (2000) — "Constrained model predictive control: Stability and optimality", *Automatica*, 36(6), 789–814.
9. Jazwinski, A. H. (1970) — *Stochastic Processes and Filtering Theory*, Academic Press.  (Continuous-Discrete Kalman filtering theory.)
10. Kristensen, N. R. et al. (2004) — "Parameter estimation in stochastic grey-box models", *Automatica*, 40(2), 225–237.  (CD-EKF prediction-error decomposition / ML identification.)
