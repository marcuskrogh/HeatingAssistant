"""Construct thermal model and heat-source objects from integration config."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..const import (
    CONF_COMFORT_OFFSET,
    CONF_C_AIR_FRACTION,
    CONF_C_SLAB_FRACTION,
    CONF_CONNECTED_ROOM,
    CONF_CONNECTIONS,
    CONF_FACADE_ABSORPTANCE,
    CONF_FACADE_COLOUR,
    CONF_FACADE_SOLAR_SHARE,
    CONF_FLOOR_TYPE,
    CONF_INFILTRATION_FRACTION,
    CONF_R_AW_FRACTION,
    CONF_R_EXTERNAL,
    CONF_R_SA,
    CONF_R_SG,
    CONF_R_VALUE,
    CONF_ROOM_NAME,
    CONF_SETPOINT,
    CONF_SKY_RADIATIVE_UA,
    CONF_SOLAR_EXPOSURE,
    CONF_SOLAR_FACING,
    CONF_SOLAR_SCALE,
    CONF_SOURCE_CHARGE_POWER,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_COOLING_COP,
    CONF_SOURCE_COOLING_EFFICIENCY,
    CONF_SOURCE_DELTA_SAT,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_HEATING_EFFICIENCY,
    CONF_SOURCE_HVAC_MODE,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_MAX_TEMP_OFFSET,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_MIN_POWER_FRACTION,
    CONF_SOURCE_NAME,
    CONF_SOURCE_PASSIVE_DISCHARGE_RATE,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_STORAGE_CAPACITY_KWH,
    CONF_SOURCE_TYPE,
    CONF_THERMAL_BRIDGE_PSI_L,
    CONF_THERMAL_MASS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
    CONF_WINDOWS,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_C_AIR_FRACTION,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_COOLING_COP,
    DEFAULT_COOLING_EFFICIENCY,
    DEFAULT_DELTA_SAT,
    DEFAULT_EFFICIENCY,
    DEFAULT_FACADE_ABSORPTANCE,
    DEFAULT_FACADE_COLOUR,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_FLOOR_TYPE,
    DEFAULT_GAS_EFFICIENCY,
    DEFAULT_GROUND_SOURCE_COP,
    DEFAULT_HEATING_EFFICIENCY,
    DEFAULT_INFILTRATION_FRACTION,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_MIN_POWER,
    DEFAULT_OIL_BOILER_EFFICIENCY,
    DEFAULT_PELLET_EFFICIENCY,
    DEFAULT_PELLET_MIN_POWER_FRACTION,
    DEFAULT_R_AW_FRACTION,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SETPOINT,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_SOLAR_EXPOSURE,
    DEFAULT_SOLAR_FACING,
    DEFAULT_SOLAR_SCALE,
    DEFAULT_SOURCE_HVAC_MODE,
    DEFAULT_STORAGE_CAPACITY_KWH,
    DEFAULT_STORAGE_CHARGE_POWER,
    DEFAULT_STORAGE_DISCHARGE_RATE,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    DEFAULT_THERMAL_MASS,
    DEFAULT_WINDOW_TILT,
    FACADE_COLOUR_TO_ABSORPTANCE,
    SOLAR_EXPOSURE_TO_APERTURE,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_ELECTRIC_FLOOR,
    SOURCE_TYPE_ELECTRIC_STORAGE,
    SOURCE_TYPE_GAS_HEATER,
    SOURCE_TYPE_GENERIC_THERMOSTAT,
    SOURCE_TYPE_GROUND_SOURCE_HP,
    SOURCE_TYPE_HEAT_PUMP,
    SOURCE_TYPE_HYDRONIC_FLOOR,
    SOURCE_TYPE_HYDRONIC_RADIATOR,
    SOURCE_TYPE_OIL_BOILER,
    SOURCE_TYPE_OIL_RADIATOR,
    SOURCE_TYPE_PELLET_STOVE,
    SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU,
)
from ..heat_sources import (
    ElectricHeater,
    ElectricStorageHeater,
    GasHeater,
    GenericThermostat,
    GroundSourceHeatPump,
    HeatPump,
    HeatSource,
    HydronicRadiator,
    PelletStove,
)
from ..thermal_model import HouseModel, Room, RoomConnection, Window

_LOGGER = logging.getLogger(__name__)


def build_house_model(rooms_cfg: List[Dict[str, Any]]) -> HouseModel:
    """
    Construct a :class:`HouseModel` from the YAML / config-entry rooms list.

    Inter-room connections reference the adjacent room by name.  Deleting or
    renaming a room can leave other rooms with a connection pointing at a name
    that no longer exists; such a dangling connection (or one with a
    non-positive R-value) is dropped here with a warning rather than allowed to
    raise — a stale link must never take down the whole integration on setup.
    """
    known_rooms = {
        rc.get(CONF_ROOM_NAME)
        for rc in rooms_cfg
        if isinstance(rc, dict) and rc.get(CONF_ROOM_NAME)
    }
    rooms = []
    for rc in rooms_cfg:
        connections = []
        for c in rc.get(CONF_CONNECTIONS, []) or []:
            target = c.get(CONF_CONNECTED_ROOM)
            try:
                r_value = float(c.get(CONF_R_VALUE))
            except (TypeError, ValueError):
                r_value = None
            if target not in known_rooms or r_value is None or r_value <= 0:
                _LOGGER.warning(
                    "Heating Assistant: dropping invalid connection %r on room "
                    "%r (target unknown or R-value non-positive)",
                    c,
                    rc.get(CONF_ROOM_NAME),
                )
                continue
            connections.append(
                RoomConnection(connected_room=target, r_value=r_value)
            )
        windows = []
        for w in rc.get(CONF_WINDOWS, []) or []:
            try:
                area = float(w[CONF_WINDOW_AREA])
                orientation = float(w[CONF_WINDOW_ORIENTATION])
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning(
                    "Heating Assistant: dropping invalid window %r on room %r "
                    "(missing/invalid area or orientation)",
                    w,
                    rc.get(CONF_ROOM_NAME),
                )
                continue
            windows.append(
                Window(
                    area=area,
                    orientation=orientation,
                    tilt=w.get(CONF_WINDOW_TILT, DEFAULT_WINDOW_TILT),
                )
            )
        rooms.append(
            Room(
                name=rc[CONF_ROOM_NAME],
                thermal_mass=rc.get(CONF_THERMAL_MASS, DEFAULT_THERMAL_MASS),
                r_external=rc.get(CONF_R_EXTERNAL, DEFAULT_R_EXTERNAL),
                connections=connections,
                windows=windows,
                setpoint=rc.get(CONF_SETPOINT, DEFAULT_SETPOINT),
                comfort_offset=rc.get(CONF_COMFORT_OFFSET, DEFAULT_COMFORT_OFFSET),
                infiltration_fraction=rc.get(
                    CONF_INFILTRATION_FRACTION, DEFAULT_INFILTRATION_FRACTION,
                ),
                # Phase 1 A2: optional floor / slab parameters.
                # ``floor_type`` drives the typology defaults applied
                # inside ``Room.__init__`` for the three slab numerics
                # (``c_slab_fraction``, ``r_sa``, ``r_sg``); per-field
                # overrides take precedence over the typology defaults
                # when both are present.
                floor_type=rc.get(CONF_FLOOR_TYPE, DEFAULT_FLOOR_TYPE),
                c_slab_fraction=rc.get(CONF_C_SLAB_FRACTION),
                r_sa=rc.get(CONF_R_SA),
                r_sg=rc.get(CONF_R_SG),
                # Phase 1 C3 / C4 / C5 — finishing-pass envelope
                # corrections.  ``facade_colour`` resolves into
                # ``facade_absorptance`` via the colour preset map;
                # an explicit ``facade_absorptance`` always wins.
                # All three default off (zero) so existing installs
                # see no behaviour change.
                sky_radiative_ua=rc.get(
                    CONF_SKY_RADIATIVE_UA, DEFAULT_SKY_RADIATIVE_UA,
                ),
                facade_absorptance=rc.get(
                    CONF_FACADE_ABSORPTANCE,
                    FACADE_COLOUR_TO_ABSORPTANCE.get(
                        rc.get(CONF_FACADE_COLOUR, DEFAULT_FACADE_COLOUR),
                        DEFAULT_FACADE_ABSORPTANCE,
                    ),
                ),
                facade_solar_share=rc.get(
                    CONF_FACADE_SOLAR_SHARE, DEFAULT_FACADE_SOLAR_SHARE,
                ),
                thermal_bridge_psi_l=rc.get(
                    CONF_THERMAL_BRIDGE_PSI_L, DEFAULT_THERMAL_BRIDGE_PSI_L,
                ),
                # Optional per-room solar-exposure preset — the no-geometry
                # fallback used when the room has no enumerated windows.
                solar_exposure_aperture=SOLAR_EXPOSURE_TO_APERTURE.get(
                    rc.get(CONF_SOLAR_EXPOSURE, DEFAULT_SOLAR_EXPOSURE),
                    SOLAR_EXPOSURE_TO_APERTURE[DEFAULT_SOLAR_EXPOSURE],
                ),
                solar_facing=rc.get(CONF_SOLAR_FACING, DEFAULT_SOLAR_FACING),
                # Identified multiplicative correction on the modelled
                # solar gain (refined by the parameter estimator).
                solar_scale=rc.get(CONF_SOLAR_SCALE, DEFAULT_SOLAR_SCALE),
                # 2R2C envelope split fractions (typology defaults; the
                # parameter estimator refines them when identifiable).
                c_air_fraction=rc.get(
                    CONF_C_AIR_FRACTION, DEFAULT_C_AIR_FRACTION,
                ),
                r_aw_fraction=rc.get(
                    CONF_R_AW_FRACTION, DEFAULT_R_AW_FRACTION,
                ),
            )
        )
    return HouseModel(rooms)


def build_heat_sources(
    sources_cfg: List[Dict[str, Any]],
) -> List[HeatSource]:
    """
    Construct heat-source objects from the configuration list.
    """
    sources: List[HeatSource] = []
    for sc in sources_cfg:
        src_type = sc[CONF_SOURCE_TYPE]
        name = sc[CONF_SOURCE_NAME]
        room = sc[CONF_SOURCE_ROOM]
        max_power = sc[CONF_SOURCE_MAX_POWER]
        entity = sc.get(CONF_SOURCE_HEATER_ENTITY)
        # Phase 1 B2 emitter-filter time constant.  Per-source override
        # via ``emitter_time_constant``; otherwise the typology default
        # from ``SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU`` (electric → 0;
        # heat-pump → 60 s).  Users on hydronic radiators can override
        # with τ ≈ 600 s.
        tau_em = float(sc.get(
            CONF_SOURCE_EMITTER_TIME_CONSTANT,
            SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU.get(src_type, 0.0),
        ))

        if src_type == SOURCE_TYPE_ELECTRIC:
            sources.append(
                ElectricHeater(
                    name=name,
                    room=room,
                    max_power=max_power,
                    efficiency=sc.get(CONF_SOURCE_EFFICIENCY, DEFAULT_EFFICIENCY),
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        elif src_type == SOURCE_TYPE_HEAT_PUMP:
            sources.append(
                HeatPump(
                    name=name,
                    room=room,
                    max_power=max_power,
                    cop_rated=sc.get(CONF_SOURCE_COP_RATED, DEFAULT_COP_RATED),
                    cop_temp_ref=sc.get(CONF_SOURCE_COP_TEMP_REF, DEFAULT_COP_TEMP_REF),
                    min_power=sc.get(CONF_SOURCE_MIN_POWER, DEFAULT_MIN_POWER),
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    hvac_mode=sc.get(CONF_SOURCE_HVAC_MODE, DEFAULT_SOURCE_HVAC_MODE),
                    cooling_cop=sc.get(CONF_SOURCE_COOLING_COP, DEFAULT_COOLING_COP),
                    cooling_efficiency=sc.get(CONF_SOURCE_COOLING_EFFICIENCY, DEFAULT_COOLING_EFFICIENCY),
                    heating_efficiency=sc.get(CONF_SOURCE_HEATING_EFFICIENCY, DEFAULT_HEATING_EFFICIENCY),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        elif src_type == SOURCE_TYPE_GENERIC_THERMOSTAT:
            sources.append(
                GenericThermostat(
                    name=name,
                    room=room,
                    max_power=max_power,
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        elif src_type in (SOURCE_TYPE_OIL_RADIATOR, SOURCE_TYPE_ELECTRIC_FLOOR):
            # These are electric heaters with typology-specific emitter time constants
            # defined in SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU; no separate class needed.
            sources.append(
                ElectricHeater(
                    name=name,
                    room=room,
                    max_power=max_power,
                    efficiency=sc.get(CONF_SOURCE_EFFICIENCY, DEFAULT_EFFICIENCY),
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        elif src_type == SOURCE_TYPE_GAS_HEATER:
            sources.append(
                GasHeater(
                    name=name,
                    room=room,
                    max_power=max_power,
                    efficiency=sc.get(CONF_SOURCE_EFFICIENCY, DEFAULT_GAS_EFFICIENCY),
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        elif src_type in (SOURCE_TYPE_HYDRONIC_RADIATOR, SOURCE_TYPE_HYDRONIC_FLOOR):
            # hydronic_floor_heating is an alias with a longer default τ_em (7200 s vs 600 s),
            # applied via SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU above.
            sources.append(
                HydronicRadiator(
                    name=name,
                    room=room,
                    max_power=max_power,
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        elif src_type == SOURCE_TYPE_OIL_BOILER:
            sources.append(
                GasHeater(
                    name=name,
                    room=room,
                    max_power=max_power,
                    efficiency=sc.get(CONF_SOURCE_EFFICIENCY, DEFAULT_OIL_BOILER_EFFICIENCY),
                    max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                    heater_entity=entity,
                    emitter_time_constant=tau_em,
                )
            )
        elif src_type == SOURCE_TYPE_GROUND_SOURCE_HP:
            sources.append(GroundSourceHeatPump(
                name=name, room=room, max_power=max_power,
                cop_rated=sc.get(CONF_SOURCE_COP_RATED, DEFAULT_GROUND_SOURCE_COP),
                max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                delta_sat=sc.get(CONF_SOURCE_DELTA_SAT, DEFAULT_DELTA_SAT),
                hvac_mode=sc.get(CONF_SOURCE_HVAC_MODE, DEFAULT_SOURCE_HVAC_MODE),
                heater_entity=entity,
                cooling_cop=sc.get(CONF_SOURCE_COOLING_COP, DEFAULT_COOLING_COP),
                cooling_efficiency=sc.get(CONF_SOURCE_COOLING_EFFICIENCY, DEFAULT_COOLING_EFFICIENCY),
                heating_efficiency=sc.get(CONF_SOURCE_HEATING_EFFICIENCY, DEFAULT_HEATING_EFFICIENCY),
                emitter_time_constant=tau_em,
            ))
        elif src_type == SOURCE_TYPE_PELLET_STOVE:
            sources.append(PelletStove(
                name=name, room=room, max_power=max_power,
                efficiency=sc.get(CONF_SOURCE_EFFICIENCY, DEFAULT_PELLET_EFFICIENCY),
                min_power_fraction=sc.get(CONF_SOURCE_MIN_POWER_FRACTION, DEFAULT_PELLET_MIN_POWER_FRACTION),
                max_temp_offset=sc.get(CONF_SOURCE_MAX_TEMP_OFFSET, DEFAULT_MAX_TEMP_OFFSET),
                heater_entity=entity,
                emitter_time_constant=tau_em,
            ))
        elif src_type == SOURCE_TYPE_ELECTRIC_STORAGE:
            sources.append(ElectricStorageHeater(
                name=name, room=room, max_power=max_power,
                charge_power=sc.get(CONF_SOURCE_CHARGE_POWER, DEFAULT_STORAGE_CHARGE_POWER),
                storage_capacity_kwh=sc.get(CONF_SOURCE_STORAGE_CAPACITY_KWH, DEFAULT_STORAGE_CAPACITY_KWH),
                passive_discharge_rate=sc.get(CONF_SOURCE_PASSIVE_DISCHARGE_RATE, DEFAULT_STORAGE_DISCHARGE_RATE),
                heater_entity=entity,
                emitter_time_constant=tau_em,
            ))
        else:
            _LOGGER.warning("Unknown heat source type %r – skipping %r", src_type, name)
    return sources
