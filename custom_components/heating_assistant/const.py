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
CONF_THERMAL_MASS = "thermal_mass"      # J/K
CONF_R_EXTERNAL = "r_external"          # K/W (typical-conditions total resistance to outdoors)
CONF_INFILTRATION_FRACTION = "infiltration_fraction"  # 0–1, fraction of 1/r_external from wind-driven infiltration at typical conditions
CONF_CONNECTIONS = "connections"        # list of {room, r_value}
CONF_WINDOWS = "windows"               # list of {area, orientation, tilt}
CONF_SETPOINT = "setpoint"             # °C
CONF_SETPOINT_ENTITY = "setpoint_entity"
CONF_TEMP_SENSOR = "temp_sensor"       # HA entity_id for measured room temp
CONF_TEMP_SENSORS = "temp_sensors"     # list of HA entity_ids for measured room temp

# Comfort schedule (time-of-day setpoint / setback) keys
CONF_SCHEDULE = "schedule"                       # list of schedule periods on a room
CONF_SCHEDULE_NAME = "name"                      # period label (e.g. "night", "workday_eco")
CONF_SCHEDULE_START = "start"                    # local time HH:MM (24h) inclusive
CONF_SCHEDULE_END = "end"                        # local time HH:MM (24h) exclusive; may wrap past midnight
CONF_SCHEDULE_DAYS = "days"                      # optional weekday list, defaults to all days
CONF_SCHEDULE_SETPOINT = "setpoint"              # °C, optional override for active period
CONF_SCHEDULE_MODE = "mode"                      # "comfort" (default) | "off"
CONF_SCHEDULE_FROST_PROTECTION = "frost_protection"  # °C floor enforced while mode == "off"

# Connection configuration keys
CONF_CONNECTED_ROOM = "room"
CONF_R_VALUE = "r_value"               # K/W

# Window configuration keys
CONF_WINDOW_AREA = "area"              # m²
CONF_WINDOW_ORIENTATION = "orientation"  # degrees from North (0=N, 90=E, 180=S, 270=W)
CONF_WINDOW_TILT = "tilt"             # degrees from horizontal (90 = vertical)

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
CONF_SOURCE_TURN_OFF_DEADBAND = "turn_off_deadband"  # °C above setpoint before heat pump turns off
CONF_SOURCE_COOLING_COP = "cooling_cop"  # cooling COP (EER) for heat pumps in cooling mode
CONF_SOURCE_COOLING_EFFICIENCY = "cooling_efficiency"  # fraction of max cooling capacity (0–1) used in dry/cool mode
CONF_SOURCE_HEATING_EFFICIENCY = "heating_efficiency"  # fraction of max heating capacity (0–1) used in heat mode

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
CONF_CONSTRAINT_OFFSET = "constraint_offset"      # °C offset for soft output constraints
CONF_ENERGY_WEIGHT = "energy_weight"              # scalar weight on ‖u‖² (energy saving vs. tracking)
CONF_SMOOTHING_WEIGHT = "smoothing_weight"        # scalar weight on ‖Δu‖² (dampens rapid input changes)
CONF_TERMINAL_WEIGHT = "terminal_weight"          # scalar multiplier on Q for terminal cost P = terminal_weight × Q
CONF_MPC_SOLVER = "mpc_solver"                    # NLP solver backend ("ipopt" | "SLSQP")
CONF_MPC_ANALYTIC_DERIVATIVES = "mpc_analytic_derivatives"  # enable analytical-derivative hooks when backend supports them
CONF_SIGMA_W = "sigma_w"                          # EKF/process model process-noise std dev [K/√s]
CONF_SIGMA_V = "sigma_v"                          # EKF measurement-noise std dev [K]
CONF_SIGMA_B = "sigma_b"                          # EKF offset-state process-noise std dev [K/√s]

# Defaults
DEFAULT_THERMAL_MASS = 5_000_000.0     # J/K (~typical room)
DEFAULT_R_EXTERNAL = 0.05              # K/W
DEFAULT_SETPOINT = 22.0                # °C
DEFAULT_HORIZON = 100                  # 100 steps ahead (~25 h at 15-min steps)
DEFAULT_UPDATE_INTERVAL = 900          # OCP step / ZOH duration = coordinator / EKF update period (seconds)
DEFAULT_EFFICIENCY = 1.0
DEFAULT_COP_RATED = 3.5
DEFAULT_COP_TEMP_REF = 7.0             # °C
DEFAULT_MIN_POWER = 0.0                # W (no minimum by default)
DEFAULT_MAX_TEMP_OFFSET = 5.0          # °C (heat pump offset at full power)
DEFAULT_TURN_OFF_DEADBAND = 0.0        # °C above setpoint before heat pump turns off (disabled by default)
DEFAULT_IDLE_OFFSET = 1.0              # °C below internal temp for idle setpoint
DEFAULT_COOLING_COP = 2.5              # rated cooling COP (EER) for heat pumps
DEFAULT_COOLING_EFFICIENCY = 1.0       # fraction of max cooling capacity used (0–1)
DEFAULT_HEATING_EFFICIENCY = 1.0       # fraction of max heating capacity used (0–1)
DEFAULT_CONSTRAINT_OFFSET = 2.0        # °C symmetric soft output constraint offset
DEFAULT_ENERGY_WEIGHT = 0.01           # weight on ‖u‖² (energy saving)
DEFAULT_SMOOTHING_WEIGHT = 0.1         # weight on ‖Δu‖² (input rate-of-change damping)
DEFAULT_TERMINAL_WEIGHT = 100.0        # terminal cost multiplier P = terminal_weight × Q
DEFAULT_MPC_SOLVER = "ipopt"
DEFAULT_MPC_ANALYTIC_DERIVATIVES = True
DEFAULT_SIGMA_W = 0.1
DEFAULT_SIGMA_V = 0.5
DEFAULT_SIGMA_B = 0.002
DEFAULT_WINDOW_TILT = 90.0             # vertical
DEFAULT_FROST_PROTECTION = 12.0        # °C minimum room temperature enforced while a schedule period has mode=off

# Comfort schedule modes
SCHEDULE_MODE_COMFORT = "comfort"      # apply the period's setpoint (default behaviour)
SCHEDULE_MODE_OFF = "off"              # disable heat sources for the room during this period

# Source types
SOURCE_TYPE_ELECTRIC = "electric_heater"
SOURCE_TYPE_HEAT_PUMP = "heat_pump"

# Update interval (seconds)
# This constant is kept for backward compatibility. The live value is read
# from the config entry (CONF_UPDATE_INTERVAL) at coordinator start-up.
UPDATE_INTERVAL = DEFAULT_UPDATE_INTERVAL

# Parameter estimation
#: Number of update steps to keep in the rolling history buffer.
#: At DEFAULT_UPDATE_INTERVAL=900 s (15 min) this is exactly 120 hours of data.
HISTORY_BUFFER_SIZE = 480
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
