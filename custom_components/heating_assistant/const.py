"""Constants for the Heating Assistant integration."""

DOMAIN = "heating_assistant"
NAME = "Heating Assistant"

# Configuration keys
CONF_ROOMS = "rooms"
CONF_HEAT_SOURCES = "heat_sources"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_CONTROLLER = "controller"

# Room configuration keys
CONF_ROOM_NAME = "name"
CONF_THERMAL_MASS = "thermal_mass"      # J/K (bundled total = c_air + c_wall)
CONF_R_EXTERNAL = "r_external"          # K/W (typical-conditions total resistance to outdoors)
CONF_INFILTRATION_FRACTION = "infiltration_fraction"  # 0–1, fraction of 1/r_external from wind-driven infiltration at typical conditions
CONF_C_AIR_FRACTION = "c_air_fraction"  # 0–1, share of thermal_mass attributed to the (fast) air node in the 2R2C model
CONF_R_AW_FRACTION = "r_aw_fraction"    # 0–1, share of the conductive path (r_external / (1 - infiltration_fraction)) attributed to the internal air↔wall film resistance R_aw; the remainder is R_we (wall↔outdoor)
CONF_FLOOR_TYPE = "floor_type"          # "none" | "slab_on_grade" | "concrete" | "ufh"
CONF_C_SLAB_FRACTION = "c_slab_fraction"  # 0–1, share of thermal_mass attributed to the slab node (Phase 1 A2)
CONF_R_SA = "r_sa"                       # K/W, air↔slab film resistance (Phase 1 A2)
CONF_R_SG = "r_sg"                       # K/W, slab↔ground conduction resistance (Phase 1 A2)
# Phase 1 C3 / C4 / C5 — finishing-pass envelope terms (all default 0 / "off"
# so existing installs see no behaviour change; opt in per room as desired).
CONF_SKY_RADIATIVE_UA = "sky_radiative_ua"      # W/K, linearised long-wave coupling between wall and sky
CONF_FACADE_COLOUR = "facade_colour"            # "light" | "medium" | "dark" | "custom"
CONF_FACADE_ABSORPTANCE = "facade_absorptance"  # 0–1, solar absorptance of the opaque facade
CONF_FACADE_SOLAR_SHARE = "facade_solar_share"  # 0–1, share of the room's window-derived solar gain attributed to the opaque facade (sol-air heat input on the wall node)
CONF_THERMAL_BRIDGE_PSI_L = "thermal_bridge_psi_l"  # W/K, linear thermal-bridge correction added to wall↔outdoor conductance
CONF_CONNECTIONS = "connections"        # list of {room, r_value}
CONF_WINDOWS = "windows"               # list of {area, orientation, tilt}
CONF_SETPOINT = "setpoint"             # °C
CONF_COMFORT_OFFSET = "comfort_offset"  # °C symmetric offset from setpoint for comfort region
CONF_SETPOINT_ENTITY = "setpoint_entity"
CONF_TEMP_SENSOR = "temp_sensor"       # HA entity_id for measured room temp
CONF_TEMP_SENSORS = "temp_sensors"     # list of HA entity_ids for measured room temp
CONF_WINDOW_SENSORS = "window_sensors"  # list of HA binary_sensor entity_ids for open-window override (Phase 3 W1)

# Comfort schedule (time-of-day setpoint / setback) keys
CONF_SCHEDULE = "schedule"                       # list of schedule periods on a room
CONF_SCHEDULE_NAME = "name"                      # period label (e.g. "night", "workday_eco")
CONF_SCHEDULE_START = "start"                    # local time HH:MM (24h) inclusive
CONF_SCHEDULE_END = "end"                        # local time HH:MM (24h) exclusive; may wrap past midnight
CONF_SCHEDULE_DAYS = "days"                      # optional weekday list, defaults to all days
CONF_SCHEDULE_SETPOINT = "setpoint"              # °C, optional override for active period
CONF_SCHEDULE_MODE = "mode"                      # "comfort" (default) | "off"
CONF_SCHEDULE_FROST_PROTECTION = "frost_protection"  # °C floor enforced while mode == "off"
CONF_SCHEDULE_COMFORT_OFFSET = "comfort_offset"  # °C half-width of soft constraint corridor; None = use room default
CONF_SCHEDULE_TRACKING_WEIGHT = "tracking_weight"  # multiplier on global Q (setpoint tracking aggressiveness); None = 1.0
CONF_SCHEDULE_ENERGY_WEIGHT = "energy_weight"    # multiplier on global R (energy use penalty); None = 1.0

# Connection configuration keys
CONF_CONNECTED_ROOM = "room"
CONF_R_VALUE = "r_value"               # K/W

# Window configuration keys
CONF_WINDOW_AREA = "area"              # m²
CONF_WINDOW_ORIENTATION = "orientation"  # degrees from North (0=N, 90=E, 180=S, 270=W)
CONF_WINDOW_TILT = "tilt"             # degrees from horizontal (90 = vertical)

# Per-room solar-exposure preset — the lightweight, no-geometry alternative to
# enumerating individual windows.  When a room has no ``windows`` configured but
# a non-"none" exposure level, its solar gain is computed from a single
# effective aperture facing one direction (see SOLAR_EXPOSURE_TO_APERTURE).
# Detailed per-window geometry remains the primary, higher-fidelity input and
# takes precedence whenever any window is configured.
CONF_SOLAR_EXPOSURE = "solar_exposure"  # "none" | "low" | "medium" | "high"
CONF_SOLAR_FACING = "solar_facing"      # degrees clockwise from North (default South=180)
CONF_SOLAR_SCALE = "solar_scale"        # dimensionless multiplier on the room's modelled solar gain (identified from data)

#: Default per-room solar-gain scale.  The configured windows / exposure
#: preset give the *prior* solar aperture; the parameter estimator refines a
#: multiplicative scale on top of it (shading, curtains, dirt, frame
#: fraction, preset error all land here).  1.0 = trust the configured
#: geometry as-is.
DEFAULT_SOLAR_SCALE = 1.0

#: Site-level ground reflectance (albedo) used for the ground-reflected
#: irradiance on tilted/vertical surfaces.  Grass/soil ≈ 0.2; fresh snow
#: ≈ 0.8.  A site-level scalar is sufficient — per-window albedo is far
#: below the noise floor of the rest of the solar pipeline.
CONF_GROUND_ALBEDO = "ground_albedo"
DEFAULT_GROUND_ALBEDO = 0.2

#: Fraction of a room's window solar gain deposited on the wall/mass node
#: (the remainder heats the air node directly).  Transmitted shortwave
#: radiation is mostly absorbed by floor and wall surfaces, not the air;
#: an even split is a robust engineering default and is deliberately NOT
#: identified from data (it is nearly collinear with the envelope split
#: parameters).
SOLAR_WALL_FRACTION = 0.5

# Heat source configuration keys
CONF_SOURCE_NAME = "name"
CONF_SOURCE_TYPE = "type"              # "electric_heater" | "heat_pump"
CONF_SOURCE_ROOM = "room"
CONF_SOURCE_MAX_POWER = "max_power"    # W (thermal)
CONF_SOURCE_EFFICIENCY = "efficiency"  # 0‑1 for electric heaters
CONF_SOURCE_COP_RATED = "cop_rated"   # rated COP for heat pumps
CONF_SOURCE_COP_TEMP_REF = "cop_temp_ref"  # outdoor temp (°C) at which rated COP applies
CONF_SOURCE_MIN_POWER = "min_power"    # W (minimum thermal output for heat pumps)
CONF_SOURCE_HEATER_ENTITY = "heater_entity"  # HA entity_id for the heater
CONF_SOURCE_MAX_TEMP_OFFSET = "max_temp_offset"  # °C max temperature offset for heat pump power control
CONF_SOURCE_HVAC_MODE = "hvac_mode"          # operating mode: "heat" | "cool" | "heat_cool"
CONF_SOURCE_TURN_OFF_DEADBAND = "turn_off_deadband"  # deprecated – ignored, kept for config compat
CONF_SOURCE_COOLING_COP = "cooling_cop"  # cooling COP (EER) for heat pumps in cooling mode
CONF_SOURCE_COOLING_EFFICIENCY = "cooling_efficiency"  # fraction of max cooling capacity (0–1) used in dry/cool mode
CONF_SOURCE_HEATING_EFFICIENCY = "heating_efficiency"  # fraction of max heating capacity (0–1) used in heat mode
CONF_SOURCE_EMITTER_TIME_CONSTANT = "emitter_time_constant"  # s, first-order lag between commanded fraction and delivered power (Phase 1 B2)

# Envelope tightness (Sherman–Grimsrud infiltration model — Phase 1 C1).
#
# The total external loss bundled into ``r_external`` is split at runtime
# into a fixed conductive part and a wind-driven infiltration part:
#
#     UA_total(v, ΔT) = (1 − f) / r_external                    # conductive
#                     + ρ c_p · L · √(C_s |ΔT| + C_w v²)        # infiltration
#
# where ``f`` is the per-room ``infiltration_fraction`` (0 = no wind-driven
# component, 1 = entire envelope loss is infiltration), and ``L`` is the
# per-room effective leakage area implicitly derived so that the runtime
# UA equals exactly ``1/r_external`` at the typical reference conditions
# (v = ``SHERMAN_GRIMSRUD_V_TYPICAL``, ΔT = ``SHERMAN_GRIMSRUD_DT_TYPICAL``).
#
# Result: when no wind data is available, the model reduces exactly to
# today's behaviour, so adding C1 introduces no regression for installs
# that don't configure a wind source.

#: User-facing tightness preset key (also accepted as a config-flow / YAML field).
CONF_ENVELOPE_TIGHTNESS = "envelope_tightness"

#: Tightness presets → default per-room infiltration_fraction.
#:
#: ``leaky``        ~7+ ACH50 (pre-1980 unsealed) — large wind-driven swing.
#: ``typical``      ~3-5 ACH50 (1980s–2000s) — default.
#: ``tight``        ~1-2 ACH50 (modern construction) — small swing.
#: ``passive_house`` <0.6 ACH50 (PassivHaus) — almost no swing.
ENVELOPE_TIGHTNESS_LEAKY = "leaky"
ENVELOPE_TIGHTNESS_TYPICAL = "typical"
ENVELOPE_TIGHTNESS_TIGHT = "tight"
ENVELOPE_TIGHTNESS_PASSIVE = "passive_house"
ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION = {
    ENVELOPE_TIGHTNESS_LEAKY: 0.50,
    ENVELOPE_TIGHTNESS_TYPICAL: 0.30,
    ENVELOPE_TIGHTNESS_TIGHT: 0.15,
    ENVELOPE_TIGHTNESS_PASSIVE: 0.05,
}
DEFAULT_ENVELOPE_TIGHTNESS = ENVELOPE_TIGHTNESS_TYPICAL
DEFAULT_INFILTRATION_FRACTION = (
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[DEFAULT_ENVELOPE_TIGHTNESS]
)

# 2R2C envelope split (Phase 1 A1).
#
# A typical residential room has a small fast-responding "air" thermal
# mass (room air + light furniture + carpets) and a much larger
# slow-responding "envelope" thermal mass (walls + floor slab + ceiling
# + heavy furniture).  The fast/slow time-scale separation is what gives
# real rooms their characteristic 5-min initial response and hour-to-day
# settling tail — a single 1R1C node cannot reproduce both.
#
# The defaults below are typology-neutral values that work well for
# typical European residential construction; the parameter estimator
# refines them from data over time, and advanced users can override
# them per-room via ``c_air_fraction`` and ``r_aw_fraction``.

#: Share of total ``thermal_mass`` attributed to the air node.
#:
#: Air + light furniture is small relative to wall/slab mass, so the
#: air node typically holds 3–10 % of the room's total heat capacity.
#: 0.05 (5 %) is a reasonable typology-neutral default.  Higher values
#: (~0.10) suit modern lightweight construction with no exposed mass;
#: lower values (~0.03) suit massive constructions with heavy walls
#: or slab floors.
DEFAULT_C_AIR_FRACTION = 0.05

#: Share of the *conductive* envelope path attributed to the internal
#: air↔wall film resistance R_aw.
#:
#: The conductive path between room air and outdoor air can be split
#: into an internal film, a wall conduction, and an external film.
#: For typical room geometries (40–80 m² of internal wall surface, an
#: internal film coefficient ~7–10 W/(m²·K)) R_aw is small relative to
#: the wall conduction + external film, so the air↔wall film accounts
#: for roughly 5 % of the conductive path resistance.
DEFAULT_R_AW_FRACTION = 0.05

#: Hard clamp on ``infiltration_fraction`` to keep the conductive path
#: well-conditioned.  At ``infiltration_fraction = 1`` the conductive
#: path resistance diverges (the wall has no path to outdoor); we
#: never let users land in that degenerate region.
MAX_INFILTRATION_FRACTION = 0.95

# Slab thermal model (Phase 1 A2 — slab node + B1 — UFH routing).
#
# A typical residential room with a concrete slab floor or underfloor
# heating has a third major thermal node: the **slab** itself.  Slabs
# have very large thermal capacitance (concrete is ~2.4 MJ/(m³·K), so a
# 25 m² × 10 cm slab is ~60 MJ/K — comparable to or larger than the
# whole rest of the envelope) and a separate heat-loss path to the
# ground at a temperature that's largely decoupled from outdoor air.
#
# The slab node decouples cleanly from the wall block (different mass,
# different boundary condition), so Phase 1 A2 introduces it as a
# distinct third state per room.  Phase 1 B1 routes UFH heat sources
# directly into the slab node (not the air), capturing the
# characteristic 4–8 h "slab-then-air" lag of underfloor heating.

#: Floor-type tokens.  Together with the floor-typology defaults
#: below they drive the per-room slab parameter derivation in
#: ``HouseModel``.
FLOOR_TYPE_NONE = "none"                # no significant slab (e.g. suspended timber)
FLOOR_TYPE_SLAB_ON_GRADE = "slab_on_grade"  # concrete slab in direct contact with ground
FLOOR_TYPE_CONCRETE = "concrete"        # concrete floor over a conditioned/semi-conditioned space
FLOOR_TYPE_UFH = "ufh"                  # underfloor heating — heat sources route into the slab
DEFAULT_FLOOR_TYPE = FLOOR_TYPE_NONE

#: Floor-type → default (c_slab_fraction, r_sa, r_sg) per room.
#:
#: c_slab_fraction is the share of the user's bundled ``thermal_mass``
#: attributed to the slab.  For floors with no significant slab the
#: share is a tiny non-zero value (the slab state stays effectively
#: passive); for slab/UFH floors the share is large (50 % is typical
#: for a concrete-floor room).  c_wall_fraction is derived as
#: ``1 − c_air_fraction − c_slab_fraction``.
#:
#: r_sa is the air↔slab film resistance, dominated by the internal
#: convective film coefficient (~7 W/(m²·K)) over the slab area.  For
#: a 25 m² slab this is ~0.006 K/W; for "no slab" rooms a large value
#: makes the slab effectively isolated from the air.
#:
#: r_sg is the slab↔ground conduction resistance through any insulation
#: under the slab.  Default 0.05 K/W reflects a moderately-insulated
#: slab-on-grade; uninsulated slabs sit closer to 0.10 K/W and well-
#: insulated slabs are closer to 0.02 K/W.  Large value for non-slab
#: rooms isolates the slab state from the ground.
FLOOR_TYPE_DEFAULTS: dict = {
    FLOOR_TYPE_NONE: {
        "c_slab_fraction": 0.01,   # passive, decoupled
        "r_sa": 1.0e6,             # effectively infinite — slab isolated from air
        "r_sg": 1.0e6,             # effectively infinite — slab isolated from ground
    },
    FLOOR_TYPE_SLAB_ON_GRADE: {
        "c_slab_fraction": 0.50,   # slab carries half the room's mass
        "r_sa": 6.0e-3,            # large slab surface → low film resistance
        "r_sg": 0.05,              # moderate slab insulation
    },
    FLOOR_TYPE_CONCRETE: {
        "c_slab_fraction": 0.40,   # slab over a conditioned space — less ground coupling
        "r_sa": 6.0e-3,
        "r_sg": 0.20,              # weaker coupling to ground (above conditioned space)
    },
    FLOOR_TYPE_UFH: {
        "c_slab_fraction": 0.50,   # UFH always implies a substantial slab
        "r_sa": 6.0e-3,
        "r_sg": 0.05,              # typical insulated UFH slab
    },
}

DEFAULT_C_SLAB_FRACTION = FLOOR_TYPE_DEFAULTS[DEFAULT_FLOOR_TYPE]["c_slab_fraction"]
DEFAULT_R_SA = FLOOR_TYPE_DEFAULTS[DEFAULT_FLOOR_TYPE]["r_sa"]
DEFAULT_R_SG = FLOOR_TYPE_DEFAULTS[DEFAULT_FLOOR_TYPE]["r_sg"]

# Ground-temperature model (Phase 1 A2).
#
# The slab node conducts to the ground at a temperature that's
# decoupled from outdoor air on the timescale of weeks to months.  We
# model T_g(t) as a sinusoidal annual cycle:
#
#     T_g(t) = T_g_mean + T_g_amp · cos(2π · (day_of_year − phase) / 365)
#
# with parameters defaulted for a typical temperate residential climate
# (e.g. Northern Europe).  Users can override per-deployment via the
# config (future config-flow follow-up); for v1 these defaults are
# constants and the controller pushes T_g(now) into the model once per
# coordinator cycle.

#: Annual-mean ground temperature [°C] for the slab-depth band
#: (~30 cm below grade).  Typical Northern European value; warmer
#: climates have a higher mean.
DEFAULT_GROUND_TEMP_MEAN = 10.0

#: Annual amplitude of the ground-temperature cycle [°C].  Shallow
#: slabs follow the outdoor swing damped to about 4–6 K; deeper soil
#: converges to a near-constant value.  4 K is a sensible default for
#: a 30 cm slab.
DEFAULT_GROUND_TEMP_AMPLITUDE = 4.0

#: Day of year at which ground temperature peaks.  Outdoor temperature
#: typically peaks around day 200 (mid-July northern hemisphere); the
#: slab lags by ~20 days due to thermal diffusion through the soil.
DEFAULT_GROUND_TEMP_PEAK_DAY = 220

# Phase 1 C3 — long-wave radiation to sky.
#
# External walls and roof radiate to an effective "sky temperature"
# that's cooler than outdoor air, especially on clear nights.  We
# linearise the Stefan–Boltzmann ``ε σ A (T_w⁴ − T_sky⁴)`` term around
# the operating range and treat it as a constant per-room conductance
# ``sky_radiative_ua`` (W/K) coupling the wall node to a virtual
# temperature ``T_sky = T_outdoor − ΔT_sky``.  Effect on the wall
# heat balance:
#
#     (T_sky − T_w) · sky_radiative_ua
#       = (T_outdoor − T_w) · sky_radiative_ua  − ΔT_sky · sky_radiative_ua
#
# The first term folds into the wall→outdoor conductance; the second
# becomes a constant negative drift on the wall node (the
# "clear-night cooling" effect).  Defaults to ``sky_radiative_ua = 0``
# per room — opt-in via the YAML field.

#: Effective sky-temperature depression below outdoor air [K].  Phase
#: 1 v1 uses a constant fallback; Phase 5 will promote it to a
#: cloud-cover-driven term (clear nights → ΔT_sky ≈ 12–20 K;
#: overcast → 0 K).
DEFAULT_DELTA_T_SKY = 6.0

#: Per-room default ``sky_radiative_ua`` [W/K].  Conservative
#: default-off so existing installs see no behaviour change.
DEFAULT_SKY_RADIATIVE_UA = 0.0

# Phase 1 C4 — sol-air on opaque surfaces.
#
# External walls and roof absorb a fraction ``α`` of incident solar
# irradiance, effectively raising the surface temperature above the
# outdoor air.  Standard engineering treatment is the "sol-air
# temperature" ``T_sol-air = T_outdoor + α · G_inc / h_e``.  For Phase
# 1 v1 we capture the dominant effect with two per-room knobs:
#
# * ``facade_absorptance`` — α in [0, 1], typology-defaulted by the
#   ``facade_colour`` preset (light / medium / dark / custom).
# * ``facade_solar_share`` — fraction of the room's window-derived
#   solar gain attributed to the opaque facade's sol-air effect.
#   Defaults to 0 (off); typical residential values are 0.2–0.6
#   depending on the ratio of opaque-wall to window area.
#
# The full per-surface geometry pipeline (independent tilt / azimuth /
# area per opaque facade element) is deferred to Phase 5 / 6 — the
# v1 model is a single scalar per room.

#: Facade-colour preset → solar absorptance ``α``.
FACADE_COLOUR_LIGHT = "light"
FACADE_COLOUR_MEDIUM = "medium"
FACADE_COLOUR_DARK = "dark"
FACADE_COLOUR_CUSTOM = "custom"
FACADE_COLOUR_TO_ABSORPTANCE: dict = {
    FACADE_COLOUR_LIGHT: 0.30,   # whitewashed render, pale cladding
    FACADE_COLOUR_MEDIUM: 0.55,  # mid-tone brick, painted timber
    FACADE_COLOUR_DARK: 0.85,    # dark brick, dark metal, weathered timber
    FACADE_COLOUR_CUSTOM: 0.55,  # placeholder; user must set ``facade_absorptance``
}
DEFAULT_FACADE_COLOUR = FACADE_COLOUR_MEDIUM
DEFAULT_FACADE_ABSORPTANCE = FACADE_COLOUR_TO_ABSORPTANCE[DEFAULT_FACADE_COLOUR]

#: Per-room default ``facade_solar_share``.  Conservative default-off
#: so the sol-air heat input is zero unless the user opts in.  A
#: typical value once enabled is ~0.3 (the opaque facade contributes a
#: small fraction of the window-derived solar gain to the wall node).
DEFAULT_FACADE_SOLAR_SHARE = 0.0

#: Per-room solar-exposure preset → effective aperture ``A_eff`` [m²·SHGC].
#: The single scalar that maps incident clear-sky POA irradiance [W/m²] to a
#: room's solar heat gain [W] when individual windows are not enumerated.
#: Levels are deliberately coarse; defaults assume clear double glazing
#: (SHGC≈0.6) over roughly 1.5 / 5 / 10 m² of glazing.
SOLAR_EXPOSURE_NONE = "none"
SOLAR_EXPOSURE_LOW = "low"
SOLAR_EXPOSURE_MEDIUM = "medium"
SOLAR_EXPOSURE_HIGH = "high"
SOLAR_EXPOSURE_TO_APERTURE: dict = {
    SOLAR_EXPOSURE_NONE: 0.0,
    SOLAR_EXPOSURE_LOW: 1.0,     # ~1.5 m² glazing × SHGC 0.6
    SOLAR_EXPOSURE_MEDIUM: 3.0,  # ~5 m² glazing × SHGC 0.6
    SOLAR_EXPOSURE_HIGH: 6.0,    # ~10 m² glazing × SHGC 0.6
}
DEFAULT_SOLAR_EXPOSURE = SOLAR_EXPOSURE_NONE
DEFAULT_SOLAR_FACING = 180.0  # South

# Phase 1 C5 — linear thermal-bridge correction.
#
# Linear thermal bridges (window frames, balcony slabs, corner
# junctions, …) add ``Ψ · L`` [W/K] to the wall→outdoor conductance.
# Identified from data with default 0 and a strong prior centred on
# zero; surfaces worst-bridge rooms in the parameter-confidence
# diagnostics.

#: Per-room default ``thermal_bridge_psi_l`` [W/K].
DEFAULT_THERMAL_BRIDGE_PSI_L = 0.0

#: Sherman–Grimsrud (LBL) infiltration coefficients.
#:
#: SHERMAN_GRIMSRUD_STACK_COEF — coefficient on |ΔT| inside the square root,
#: units (m/s)²/K.  Default 0.000145 corresponds to a single-storey building
#: with no significant shielding (LBL/RHB-79 typical residential value).
#:
#: SHERMAN_GRIMSRUD_WIND_COEF — coefficient on v² inside the square root,
#: dimensionless (it scales (m/s)² → (m/s)²).  Default 0.000319 corresponds
#: to a single-storey building with average shielding.
SHERMAN_GRIMSRUD_STACK_COEF = 1.45e-4
SHERMAN_GRIMSRUD_WIND_COEF = 3.19e-4

#: Reference conditions at which the runtime UA equals exactly ``1/r_external``.
#: Picked to be representative of a heating-season operating point so that
#: existing-installation parameters stay calibrated.
SHERMAN_GRIMSRUD_V_TYPICAL = 3.0    # m/s
SHERMAN_GRIMSRUD_DT_TYPICAL = 20.0  # K

#: Volumetric heat capacity of dry air at room temperature, ρ × c_p ≈
#: 1.2 kg/m³ × 1005 J/(kg·K).  Used to convert the Sherman–Grimsrud
#: volumetric flow rate to a thermal conductance.
AIR_RHO_CP = 1200.0  # J / (m³ · K)

# Controller configuration keys
CONF_HORIZON = "horizon"               # MPC prediction horizon (steps)
CONF_UPDATE_INTERVAL = "update_interval"  # wall-clock period between coordinator updates = OCP step = EKF step (seconds)
CONF_OUTDOOR_TEMP_ENTITY = "outdoor_temp_entity"  # HA sensor entity_id
CONF_WEATHER_ENTITY = "weather_entity"             # HA weather entity_id for forecast
# Solar-radiation (irradiance) forecast.  A sensor reporting the sun's Global
# Horizontal Irradiance (GHI) in W/m² — e.g. the Open-Meteo
# ``shortwave_radiation`` variable, a pyranometer, or any irradiance sensor,
# ideally with an hourly forecast in its attributes.  When configured, the
# forecast supplies the solar model's *intensity* — decomposed into beam/diffuse
# (Erbs) and transposed onto each window by geometry — replacing the modelled
# clear-sky irradiance; the analytical clear-sky model remains the automatic
# fallback.  This is the sun's radiation, NOT solar-panel / PV production.
CONF_SOLAR_RADIATION_ENTITY = "solar_radiation_entity"  # HA sensor entity_id, W/m² GHI
CONF_TRACKING_WEIGHT = "tracking_weight"          # scalar weight on ‖z − z_ref‖² (setpoint tracking cost Q diagonal)
CONF_ENERGY_WEIGHT = "energy_weight"              # scalar weight on ‖u‖² (input regularisation cost R diagonal)
CONF_SMOOTHING_WEIGHT = "smoothing_weight"        # scalar weight on ‖Δu‖² (input rate-of-movement cost S diagonal)
CONF_SOFT_CONSTRAINT_WEIGHT = "soft_constraint_weight"          # quadratic penalty ρ on soft output bound violations (ρ·ε²)
CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT = "soft_constraint_linear_weight"  # linear penalty ρ_lin on soft output bound violations (ρ_lin·ε)
CONF_TERMINAL_WEIGHT = "terminal_weight"          # scalar multiplier on Q for terminal cost P = terminal_weight × Q
CONF_MPC_SOLVER = "mpc_solver"                    # kept for backwards compat; QP backend always used
CONF_MPC_ANALYTIC_DERIVATIVES = "mpc_analytic_derivatives"  # kept for backwards compat; always True
CONF_SIGMA_W = "sigma_w"                          # EKF/process model process-noise std dev [K/√s]
CONF_SIGMA_V = "sigma_v"                          # EKF measurement-noise std dev [K]
CONF_SIGMA_B = "sigma_b"                          # EKF offset-state process-noise std dev [K/√s]
CONF_WINDOW_OPEN_DEBOUNCE = "window_open_debounce"            # seconds sensor must stay on before entering open-state
CONF_WINDOW_OPEN_CLOSE_SETTLE = "window_open_close_settle"    # seconds sensors must stay off before leaving open-state
CONF_WINDOW_OPEN_Q_INFLATION = "window_open_q_inflation"      # covariance multiplier for EKF process noise while room is open

# ---------------------------------------------------------------------------
# UI / dashboard display settings (industrial panel "Configuration" page)
# ---------------------------------------------------------------------------
# These control how the custom industrial dashboard renders plots; they have
# no effect on the controller itself.  The plot prediction horizon is
# deliberately decoupled from the MPC ``CONF_HORIZON``: when the plot horizon
# extends beyond the controller horizon, the final actuation is held flat and
# the temperature trajectory is simulated forward (see
# ``coordinator.build_forecast_payload``).
CONF_PLOT_HISTORY_HOURS = "plot_history_hours"    # hours of measured history shown on room plots
CONF_PLOT_FORECAST_HOURS = "plot_forecast_hours"  # hours of forecast shown on room plots (0 = match controller horizon)
DEFAULT_PLOT_HISTORY_HOURS = 12.0
DEFAULT_PLOT_FORECAST_HOURS = 0.0                 # 0 = auto: use the full controller horizon

# Electricity price (Nord Pool / Tibber / any hourly price sensor)
CONF_PRICE_ENTITY = "price_entity"                # HA sensor entity_id exposing Nord Pool / market prices
CONF_ENERGY_PRICE_WEIGHT = "energy_price_weight"  # α: dimensionless scale on the linear price term
CONF_PRICE_NET_TARIFF = "price_net_tariff"        # fixed net/grid tariff added to raw spot price (same unit/kWh)
CONF_PRICE_SPOT_SURCHARGE = "price_spot_surcharge"  # fixed spot price surcharge/tax added on top (same unit/kWh)

# Defaults
DEFAULT_THERMAL_MASS = 5_000_000.0     # J/K (~typical room)
DEFAULT_R_EXTERNAL = 0.05              # K/W
DEFAULT_SETPOINT = 22.0                # °C
DEFAULT_SETPOINT_PULL_WEIGHT = 0.0     # kept for internal back-compat; use DEFAULT_TRACKING_WEIGHT
DEFAULT_TRACKING_WEIGHT = 0.0          # weight on ‖z − z_ref‖² (Q diagonal); 0 = zone control (comfort-corridor only)
DEFAULT_HORIZON = 100                  # 100 steps ahead (~25 h at 15-min steps)
DEFAULT_UPDATE_INTERVAL = 900          # OCP step / ZOH duration = coordinator / EKF update period (seconds)
DEFAULT_EFFICIENCY = 1.0
DEFAULT_COP_RATED = 3.5
DEFAULT_COP_TEMP_REF = 7.0             # °C
DEFAULT_MIN_POWER = 0.0                # W (no minimum by default)
DEFAULT_MAX_TEMP_OFFSET = 5.0          # °C (heat pump offset at full power)
DEFAULT_SOURCE_HVAC_MODE = "heat_cool" # operating mode for new heat-pump sources
SOURCE_HVAC_MODE_HEAT = "heat"
SOURCE_HVAC_MODE_COOL = "cool"
SOURCE_HVAC_MODE_HEAT_COOL = "heat_cool"
DEFAULT_TURN_OFF_DEADBAND = 0.0        # deprecated – kept for config compat only
DEFAULT_IDLE_OFFSET = 1.0              # °C below internal temp for idle setpoint (non-HP climate)
DEFAULT_COOLING_COP = 2.5              # rated cooling COP (EER) for heat pumps
DEFAULT_COOLING_EFFICIENCY = 1.0       # fraction of max cooling capacity used (0–1)
DEFAULT_HEATING_EFFICIENCY = 1.0       # fraction of max heating capacity used (0–1)
DEFAULT_COMFORT_OFFSET = 2.0           # °C symmetric comfort region offset from setpoint (per-room)
DEFAULT_ENERGY_WEIGHT = 0.01           # weight on ‖u‖² (input regularisation)
DEFAULT_SMOOTHING_WEIGHT = 0.1         # weight on ‖Δu‖² (input rate-of-movement damping)
DEFAULT_SOFT_CONSTRAINT_WEIGHT = 1000.0       # quadratic soft output-bound violation penalty ρ (ρ·ε²)
DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT = 0.0   # linear soft output-bound violation penalty ρ_lin (ρ_lin·ε); 0 = disabled
DEFAULT_TERMINAL_WEIGHT = 100.0        # terminal cost multiplier P = terminal_weight × Q
DEFAULT_MPC_SOLVER = "qp"              # QP backend; legacy "ipopt"/"slsqp" values accepted but ignored
DEFAULT_MPC_ANALYTIC_DERIVATIVES = True  # always True; kept for backwards compat
DEFAULT_SIGMA_W = 0.1
DEFAULT_SIGMA_V = 0.5
DEFAULT_SIGMA_B = 0.002
DEFAULT_WINDOW_OPEN_DEBOUNCE = 60
DEFAULT_WINDOW_OPEN_CLOSE_SETTLE = 30
DEFAULT_WINDOW_OPEN_Q_INFLATION = 10.0
CONF_IDENTIFICATION_HORIZON_HOURS = "identification_horizon_hours"
DEFAULT_IDENTIFICATION_HORIZON_HOURS = 6.0
DEFAULT_WINDOW_TILT = 90.0             # vertical
DEFAULT_ENERGY_PRICE_WEIGHT = 1.0      # active out of the box when a price entity is configured
DEFAULT_PRICE_NET_TARIFF = 0.0         # no tariff adder by default
DEFAULT_PRICE_SPOT_SURCHARGE = 0.0     # no surcharge adder by default
DEFAULT_FROST_PROTECTION = 12.0        # °C minimum room temperature enforced while a schedule period has mode=off

# Comfort schedule modes
SCHEDULE_MODE_COMFORT = "comfort"      # apply the period's setpoint (default behaviour)
SCHEDULE_MODE_OFF = "off"              # disable heat sources for the room during this period

# Source types
SOURCE_TYPE_ELECTRIC = "electric_heater"
SOURCE_TYPE_HEAT_PUMP = "heat_pump"
SOURCE_TYPE_GENERIC_THERMOSTAT = "generic_thermostat"
SOURCE_TYPE_OIL_RADIATOR = "oil_radiator"
SOURCE_TYPE_ELECTRIC_FLOOR = "electric_floor_heating"
SOURCE_TYPE_GAS_HEATER = "gas_heater"
SOURCE_TYPE_HYDRONIC_RADIATOR = "hydronic_radiator"
SOURCE_TYPE_HYDRONIC_FLOOR = "hydronic_floor_heating"

# Default combustion efficiency for gas heaters (condensing ≈ 0.95; conventional ≈ 0.80–0.85).
DEFAULT_GAS_EFFICIENCY = 0.90

# Phase 1 B2 — typology defaults for the per-source emitter time
# constant ``τ_em`` [s].  Applied in ``coordinator.build_heat_sources``
# when the user hasn't explicitly set ``emitter_time_constant``:
#
# * electric_heater / generic_thermostat / gas_heater → 0 s.  The
#   heat source itself delivers power almost instantly; thermal mass
#   in the room is captured by the room model, not the source.
# * heat_pump              →   60 s.  Indoor unit + compressor +
#   refrigerant loop have ~1 min of lag between commanded fraction
#   and delivered air-side power.
# * hydronic_radiator      →  600 s.  Water mass + steel/cast-iron
#   radiator body; ~10 min between TRV command and room-air effect.
# * oil_radiator           → 1800 s.  Large oil reservoir acts as a
#   slow thermal buffer (~30 min time constant).
# * electric_floor_heating → 3600 s.  Concrete/screed slab stores
#   energy over hours; the commanded fraction changes floor surface
#   temperature very slowly.
# * hydronic_floor_heating → 3600 s.  Water-pipe-heated screed slab;
#   similar inertia to electric UFH but draws no electricity.
SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU: dict = {
    SOURCE_TYPE_ELECTRIC: 0.0,
    SOURCE_TYPE_HEAT_PUMP: 60.0,
    SOURCE_TYPE_GENERIC_THERMOSTAT: 0.0,
    SOURCE_TYPE_HYDRONIC_RADIATOR: 600.0,
    SOURCE_TYPE_OIL_RADIATOR: 1800.0,
    SOURCE_TYPE_ELECTRIC_FLOOR: 3600.0,
    SOURCE_TYPE_HYDRONIC_FLOOR: 3600.0,
    SOURCE_TYPE_GAS_HEATER: 0.0,
}

# Update interval (seconds)
# This constant is kept for backward compatibility. The live value is read
# from the config entry (CONF_UPDATE_INTERVAL) at coordinator start-up.
UPDATE_INTERVAL = DEFAULT_UPDATE_INTERVAL

# Cadence [s] of the fast UI refresh that re-reads measurements / setpoints and
# pushes them to the dashboard between scheduled MPC ticks.  The MPC itself runs
# only at CONF_UPDATE_INTERVAL; this refresh never runs the controller.  Capped
# at the update interval so it never fires more often than the MPC on very short
# intervals.
UI_REFRESH_INTERVAL = 60

# Parameter estimation
#: Number of update steps to keep in the rolling history buffer.
#: At DEFAULT_UPDATE_INTERVAL=900 s (15 min) this is exactly 120 hours of data.
HISTORY_BUFFER_SIZE = 480
#: Config-entry key for the JSONL identification-history retention period.
CONF_IDENTIFICATION_HISTORY_DAYS = "identification_history_days"
#: Default number of days the integration-managed JSONL identification history
#: store retains data.  Independent of HA Recorder's ``purge_keep_days``.
DEFAULT_IDENTIFICATION_HISTORY_DAYS = 90
#: Exponential-moving-average time constant [s] for smoothing the live cloud
#: cover used by the solar model.  Clouds change gradually, so the instantaneous
#: weather-entity reading is low-pass filtered to keep the solar attenuation
#: continuous instead of jumping between cycles.
CLOUD_SMOOTHING_TAU_S = 1800.0
#: Throttle [s] for persisting smoothed runtime weather state (cloud cover) so
#: it survives a restart and the first post-restart cycle does not spike.
RUNTIME_STATE_SAVE_DELAY_S = 60.0
#: Number of MPC solve-time samples to retain for rolling statistics.
MPC_STATS_BUFFER_SIZE = 100
#: Number of past parameter-estimation runs retained for the dashboard.
ESTIMATION_HISTORY_SIZE = 20
#: Service name for ML parameter estimation
SERVICE_ESTIMATE_PARAMETERS_ML = "estimate_parameters_ml"
#: Service name for runtime comfort-schedule suspend/resume
SERVICE_SET_SCHEDULE_ENABLED = "set_schedule_enabled"
#: entry.data key that stores the latest persisted estimation snapshot so that
#: estimated parameters survive a full Home Assistant restart.
CONF_ESTIMATED_PARAMS = "estimated_params"
#: entry.data key that stores user-modified room setpoints so they survive a
#: full Home Assistant restart and the end of a scheduled "off" period.
CONF_PERSISTED_SETPOINTS = "persisted_setpoints"

#: entry.data key that stores user-modified room schedules so they survive
#: reloads and restarts regardless of whether rooms come from YAML or options.
CONF_PERSISTED_SCHEDULES = "persisted_schedules"

#: entry.data key that stores user-modified per-room comfort offsets (the
#: symmetric comfort-band half-width set from the dashboard climate cards) so
#: they survive a full Home Assistant restart, mirroring CONF_PERSISTED_SETPOINTS.
CONF_PERSISTED_COMFORT_OFFSETS = "persisted_comfort_offsets"

#: entry.data key that stores the per-room on/off toggle state set by the user
#: so that rooms turned off (e.g. rooms without a heater) remain off after a
#: full Home Assistant restart, mirroring CONF_PERSISTED_SETPOINTS.
CONF_PERSISTED_ROOM_ENABLED = "persisted_room_enabled"

# ---------------------------------------------------------------------------
# System-identification experiments and stored datasets
# ---------------------------------------------------------------------------
#: Service names for the experiment / dataset features.
SERVICE_SCHEDULE_EXPERIMENT = "schedule_experiment"
SERVICE_CANCEL_EXPERIMENT = "cancel_experiment"
SERVICE_DELETE_EXPERIMENT = "delete_experiment"
SERVICE_CREATE_DATASET = "create_dataset"
SERVICE_DELETE_DATASET = "delete_dataset"

#: Excitation signal types a scheduled experiment can apply to a room's heaters
#: to gather informative system-identification data.
EXCITATION_STEP = "step"          # single on-step then off — simple, interpretable
EXCITATION_PRBS = "prbs"          # pseudo-random binary sequence (rich spectrum)
EXCITATION_PULSE = "pulse"        # square wave alternating high/low at a fixed period
EXCITATION_TYPES = (EXCITATION_STEP, EXCITATION_PRBS, EXCITATION_PULSE)

#: Default excitation parameters.  A *step* is the default: it drives the room
#: with a multi-phase step test (heat — settle — cool — settle for reversible
#: units, or heat — settle for heat-only units), the simplest informative test.
DEFAULT_EXCITATION_TYPE = EXCITATION_STEP
#: Step magnitude as a fraction (0–1) of the source's max heat / cool power; the
#: step drives ``+step_pct`` (heat) and, for reversible units, ``-step_pct`` (cool).
DEFAULT_EXCITATION_STEP_PCT = 1.0
#: Switching period [s] for PRBS / pulse signals.  One hour gives a good spread
#: of excitation energy across the thermal time constants of a typical room.
DEFAULT_EXCITATION_PERIOD_S = 3600.0
#: Settle / response buffer [s]: active excitation stops this long *before* the
#: window ends, leaving the heaters at their low level so the heater's influence
#: is absorbed by the room within the captured window.  Two hours covers a
#: typical room's dominant thermal response; the service caps it to never exceed
#: half the window so a short experiment still gets meaningful excitation.
DEFAULT_EXPERIMENT_SETTLE_S = 7200.0
#: Comfort-corridor half-width [°C] applied to a room over the horizon steps an
#: experiment governs.  The room's input is pinned to the excitation signal on
#: those steps, so the corridor is opened wide (effectively unbounded) to stop
#: the MPC penalising — and therefore *anticipating* — the forced excursion.  It
#: is kept out of the displayed forecast so the temperature plot does not zoom.
EXPERIMENT_RELAXED_COMFORT_OFFSET = 1000.0
#: Safety bounds enforced while an experiment runs, regardless of the signal:
#: heating is forced on below ``min`` (frost protection) and off above ``max``.
DEFAULT_EXPERIMENT_MIN_TEMP = 12.0
DEFAULT_EXPERIMENT_MAX_TEMP = 26.0
#: Maximum scheduled-experiment duration [s] accepted by the service (7 days).
MAX_EXPERIMENT_DURATION_S = 7 * 24 * 3600.0

#: Maximum number of stored datasets kept (oldest manual ones are pruned first).
MAX_STORED_DATASETS = 50
#: Maximum number of history records snapshotted into a single dataset.  Long
#: windows are trimmed to the most recent records to keep the store bounded.
MAX_DATASET_RECORDS = 20000

#: Origin labels for stored datasets.
DATASET_SOURCE_MANUAL = "manual"
DATASET_SOURCE_EXPERIMENT = "experiment"
