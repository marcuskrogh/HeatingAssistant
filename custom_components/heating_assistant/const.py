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
CONF_R_EXTERNAL = "r_external"          # K/W (thermal resistance to outdoors)
CONF_CONNECTIONS = "connections"        # list of {room, r_value}
CONF_WINDOWS = "windows"               # list of {area, orientation, tilt}
CONF_SETPOINT = "setpoint"             # °C
CONF_SETPOINT_ENTITY = "setpoint_entity"
CONF_TEMP_SENSOR = "temp_sensor"       # HA entity_id for measured room temp
CONF_TEMP_SENSORS = "temp_sensors"     # list of HA entity_ids for measured room temp

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

# Controller configuration keys
CONF_HORIZON = "horizon"               # MPC prediction horizon (steps)
CONF_DT = "dt"                         # time step in seconds
CONF_OUTDOOR_TEMP_ENTITY = "outdoor_temp_entity"  # HA sensor entity_id
CONF_WEATHER_ENTITY = "weather_entity"             # HA weather entity_id for forecast
CONF_CONSTRAINT_OFFSET = "constraint_offset"      # °C offset for soft output constraints

# Defaults
DEFAULT_THERMAL_MASS = 5_000_000.0     # J/K (~typical room)
DEFAULT_R_EXTERNAL = 0.05              # K/W
DEFAULT_SETPOINT = 21.0                # °C
DEFAULT_HORIZON = 6                    # 6 steps ahead
DEFAULT_DT = 900                       # 15-minute steps (seconds)
DEFAULT_EFFICIENCY = 1.0
DEFAULT_COP_RATED = 3.5
DEFAULT_COP_TEMP_REF = 7.0             # °C
DEFAULT_MIN_POWER = 0.0                # W (no minimum by default)
DEFAULT_MAX_TEMP_OFFSET = 5.0          # °C (heat pump offset at full power)
DEFAULT_TURN_OFF_DEADBAND = 1.0        # °C above setpoint before heat pump turns off
DEFAULT_IDLE_OFFSET = 1.0              # °C below internal temp for idle setpoint
DEFAULT_CONSTRAINT_OFFSET = 2.0        # °C symmetric soft output constraint offset
DEFAULT_WINDOW_TILT = 90.0             # vertical

# Source types
SOURCE_TYPE_ELECTRIC = "electric_heater"
SOURCE_TYPE_HEAT_PUMP = "heat_pump"

# Entity suffixes
SUFFIX_CLIMATE = "climate"
SUFFIX_PREDICTED_TEMP = "predicted_temperature"
SUFFIX_HEATING_POWER = "heating_power"

# Update interval (seconds)
UPDATE_INTERVAL = 60
