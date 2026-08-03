/**
 * Client-side KPI calculations mirroring ``kpi.py`` and ``docs/KPI_SPEC.md``.
 * Prefer backend attributes when present; compute locally as fallback.
 */

import {
  entityValue,
  entityAttr,
  systemEntity,
  modelFitLabel,
  MAX_SOLVE_TIME_S,
} from './utils.js?v=115';

export const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

// Gauge scale constants (KPI_SPEC §4.3, §5.3, §5.5 — match kpi.py)
export const HEAT_LOSS_GAUGE_FLOOR_W = 3000;
export const HEAT_LOSS_GAUGE_SCALE_FACTOR = 1.2;
export const SOLAR_GAIN_GAUGE_DEFAULT_MAX_W = 1000;
export const HOUSE_POWER_GAUGE_FLOOR_W = 3000;
export const HOUSE_POWER_GAUGE_DEFAULT_MAX_W = 10000;
export const HOUSE_POWER_GAUGE_SCALE_FACTOR = 1.2;

export const COMFORT_DEVIATION_GAUGE_MAX_C = 2.0;
export const DAILY_ENERGY_GAUGE_MAX_KWH = 100;
export const MPC_LOAD_GAUGE_MAX_PCT = 100;

/** Severity thresholds per KPI (KPI_SPEC §4–5). Use with severityColor / severityColorInverse. */
export const KPI_SEVERITY = {
  comfortIndex: { good: 95, warning: 80 },
  timeInRange: { good: 95, warning: 80 },
  houseHeatingPower: { good: 0.30, warning: 0.70, inverse: true },
  effectiveCop: { good: 3.0, warning: 2.0 },
  dailyEnergy: { good: 20, warning: 50, inverse: true },
  meanTrackingError: { good: 0.3, warning: 0.5, inverse: true },
  modelFit: { good: 0.8, warning: 0.5 },
  mpcLoad: { good: 25, warning: 50, inverse: true },
  comfortDeviation: { good: 0, warning: 0.5, inverse: true },
  solarGain: { good: 300, warning: 50 },
  heatLoss: { good: 500, warning: 1500, inverse: true },
};

function isFiniteNum(value) {
  if (value === null || value === undefined) return false;
  const num = typeof value === 'number' ? value : parseFloat(value);
  return !isNaN(num) && Number.isFinite(num);
}

/** Whether a room is in the active set (KPI_SPEC §6.2). */
export function isRoomActive(state, slug) {
  const attrs = state[CONFIG_ENTITY]?.attributes || {};
  if (attrs.room_active && slug in attrs.room_active) {
    return attrs.room_active[slug] !== false;
  }
  if (attrs.room_enabled && slug in attrs.room_enabled) {
    return attrs.room_enabled[slug] !== false;
  }
  return true;
}

/** Prefer filtered temperature; fall back to measured (KPI_SPEC §6.3). */
export function roomTemperature(state, room) {
  const filtered = room.entities?.temperature_filtered;
  if (filtered) {
    const v = entityValue(state, filtered);
    if (v !== null) return v;
  }
  const measured = room.entities?.temperature_measured;
  if (measured) return entityValue(state, measured);
  return null;
}

/** Signed distance outside the comfort corridor; zero when in band (§5.2). */
export function comfortDeviationC(temperature, lower, upper) {
  if (temperature < lower) return lower - temperature;
  if (temperature > upper) return temperature - upper;
  return 0;
}

/**
 * Room comfort deviation [°C].
 * Reads ``comfort_deviation`` from ``temperature_filtered`` when present, else computes.
 */
export function roomComfortDeviation(state, room, roomActive) {
  if (!roomActive) return null;

  const filteredEntity = room.entities?.temperature_filtered;
  if (filteredEntity) {
    const attrDev = entityAttr(state, filteredEntity, 'comfort_deviation');
    if (attrDev !== null && attrDev !== undefined && isFiniteNum(attrDev)) {
      return parseFloat(attrDev);
    }
  }

  const temp = roomTemperature(state, room);
  const lower = entityValue(state, room.entities?.constraint_lower);
  const upper = entityValue(state, room.entities?.constraint_upper);
  if (!isFiniteNum(temp) || !isFiniteNum(lower) || !isFiniteNum(upper)) {
    return null;
  }
  return comfortDeviationC(temp, lower, upper);
}

/**
 * Per-room 24 h time-in-range [%].
 * Reads ``time_in_range_pct_24h`` from ``temperature_filtered`` when present.
 */
export function roomTimeInRangePct(state, room, roomActive) {
  if (!roomActive) return null;

  const filteredEntity = room.entities?.temperature_filtered;
  if (filteredEntity) {
    const attr = entityAttr(state, filteredEntity, 'time_in_range_pct_24h');
    if (attr !== null && attr !== undefined && isFiniteNum(attr)) {
      return parseFloat(attr);
    }
  }

  const temp = roomTemperature(state, room);
  const lower = entityValue(state, room.entities?.constraint_lower);
  const upper = entityValue(state, room.entities?.constraint_upper);
  if (!isFiniteNum(temp) || !isFiniteNum(lower) || !isFiniteNum(upper)) {
    return null;
  }
  return null;
}

/** Percentage of active rooms within the comfort corridor (§4.2). */
export function comfortIndexPct(state, rooms) {
  const active = rooms.filter((r) => isRoomActive(state, r.slug));
  if (active.length === 0) return null;

  let eligible = 0;
  let inBand = 0;
  for (const room of active) {
    const temp = roomTemperature(state, room);
    const lower = entityValue(state, room.entities?.constraint_lower);
    const upper = entityValue(state, room.entities?.constraint_upper);
    if (!isFiniteNum(temp) || !isFiniteNum(lower) || !isFiniteNum(upper)) {
      continue;
    }
    eligible += 1;
    if (lower <= temp && temp <= upper) inBand += 1;
  }

  if (eligible === 0) return null;
  return (100 * inBand) / eligible;
}

/**
 * House comfort index [%].
 * Prefers ``system_summary.comfort_index_pct``; falls back to client computation.
 */
export function houseComfortIndex(state, rooms) {
  const summaryEntity = systemEntity('system_summary');
  const attr = entityAttr(state, summaryEntity, 'comfort_index_pct');
  if (attr !== null && attr !== undefined) {
    const num = parseFloat(attr);
    if (!isNaN(num)) return num;
  }
  if (rooms) return comfortIndexPct(state, rooms);
  return null;
}

/** Mean |T − setpoint| across active rooms with valid data (§4.6). */
export function computeMeanTrackingError(state, rooms) {
  const active = rooms.filter((r) => isRoomActive(state, r.slug));
  if (active.length === 0) return null;

  const errors = [];
  for (const room of active) {
    const temp = roomTemperature(state, room);
    const setpoint = entityValue(state, room.entities?.setpoint);
    if (!isFiniteNum(temp) || !isFiniteNum(setpoint)) continue;
    errors.push(Math.abs(temp - setpoint));
  }

  if (errors.length === 0) return null;
  return errors.reduce((sum, e) => sum + e, 0) / errors.length;
}

/**
 * House mean tracking error [°C].
 * Prefers ``mpc_performance.mean_tracking_error``; falls back to client computation.
 */
export function houseMeanTrackingError(state, rooms) {
  const mpcEntity = systemEntity('mpc_performance');
  const attr = entityAttr(state, mpcEntity, 'mean_tracking_error');
  if (attr !== null && attr !== undefined) {
    const num = parseFloat(attr);
    if (!isNaN(num)) return num;
  }
  if (rooms) return computeMeanTrackingError(state, rooms);
  return null;
}

/** Mean R² across rooms with valid data plus aggregate label (§4.7). */
export function aggregateModelFit(r2Values) {
  const valid = r2Values.filter((v) => v !== null && isFiniteNum(v));
  if (valid.length === 0) {
    return { value: 0, label: '—' };
  }
  const mean = valid.reduce((sum, v) => sum + v, 0) / valid.length;
  const { label } = modelFitLabel(mean);
  return { value: mean, label };
}

/** House model fit — mean R² across rooms plus GOOD / ACCEPTABLE / POOR label (§4.7). */
export function houseModelFit(state, rooms) {
  const r2Values = [];
  for (const room of rooms) {
    const entity = room.entities?.model_fit_quality;
    if (entity) {
      const v = entityValue(state, entity);
      if (v !== null) r2Values.push(v);
    }
  }
  return aggregateModelFit(r2Values);
}

/** Room model fit — R² state plus label (§5.6). */
export function roomModelFit(state, room) {
  const entity = room.entities?.model_fit_quality;
  if (!entity) {
    return { value: null, label: '—' };
  }
  const v = entityValue(state, entity);
  if (v === null) {
    return { value: null, label: '—' };
  }
  const { label } = modelFitLabel(v);
  return { value: v, label };
}

/** Total instantaneous heating power [W] from system_summary. */
export function houseHeatingPowerW(state) {
  const summaryEntity = systemEntity('system_summary');
  const stateW = entityValue(state, summaryEntity);
  if (stateW !== null) return stateW;
  const attr = entityAttr(state, summaryEntity, 'total_heating_power');
  if (attr !== null && attr !== undefined && isFiniteNum(attr)) {
    return parseFloat(attr);
  }
  return null;
}

/** Total instantaneous heating power [kW], one decimal (§4.3). */
export function houseHeatingPowerKw(state) {
  const totalW = houseHeatingPowerW(state);
  if (totalW === null) return null;
  return Math.round((totalW / 1000) * 10) / 10;
}

/**
 * Effective system COP for display, or ``null`` when the gauge should be hidden (§4.4).
 * Hides for resistive-only systems and when COP is undefined or zero.
 */
export function houseEffectiveCop(state) {
  const summaryEntity = systemEntity('system_summary');
  const hasHeatPump = entityAttr(state, summaryEntity, 'has_heat_pump');
  if (hasHeatPump === false) return null;

  const cop = entityAttr(state, summaryEntity, 'effective_system_cop');
  if (cop === null || cop === undefined || !isFiniteNum(cop) || parseFloat(cop) === 0) {
    return null;
  }
  return parseFloat(cop);
}

/** Room heat loss [W] from ``{slug}_heat_loss`` state (§5.5). */
export function roomHeatLoss(state, room) {
  const entity = room.entities?.heat_loss;
  if (!entity) return null;
  return entityValue(state, entity);
}

/** Dynamic upper bound for the room heat-loss gauge (§5.5). */
export function heatLossGaugeMax(heatLossW) {
  if (!isFiniteNum(heatLossW)) return HEAT_LOSS_GAUGE_FLOOR_W;
  return Math.max(HEAT_LOSS_GAUGE_FLOOR_W, HEAT_LOSS_GAUGE_SCALE_FACTOR * heatLossW);
}

/** Dynamic upper bound for the room solar-gain gauge (§5.4). */
export function solarGainGaugeMax(solarGainW) {
  if (!isFiniteNum(solarGainW)) return SOLAR_GAIN_GAUGE_DEFAULT_MAX_W;
  return Math.max(SOLAR_GAIN_GAUGE_DEFAULT_MAX_W, solarGainW);
}

/**
 * Upper bound for house total-heating-power gauge normalisation (§4.3).
 * @param {number} liveTotalW - Current total heating power [W]
 * @param {number|null} [ratedCapacityW] - Coordinator-rated capacity when exposed
 */
export function houseHeatingPowerGaugeMax(liveTotalW, ratedCapacityW = null) {
  if (ratedCapacityW !== null && ratedCapacityW !== undefined && isFiniteNum(ratedCapacityW)) {
    return Math.max(ratedCapacityW, HOUSE_POWER_GAUGE_FLOOR_W);
  }
  if (!isFiniteNum(liveTotalW)) return HOUSE_POWER_GAUGE_DEFAULT_MAX_W;
  return Math.max(
    HOUSE_POWER_GAUGE_DEFAULT_MAX_W,
    HOUSE_POWER_GAUGE_SCALE_FACTOR * liveTotalW,
    HOUSE_POWER_GAUGE_FLOOR_W,
  );
}

/** MPC solve load as percent of MAX_SOLVE_TIME_S (§4.8). */
export function mpcLoadPercent(state) {
  const mpcEntity = systemEntity('mpc_performance');
  const solveS = entityValue(state, mpcEntity);
  if (solveS === null) return 0;
  return Math.min(100, (solveS / MAX_SOLVE_TIME_S) * 100);
}

/** Normalised house heating power for gauge fill: live / max, 0–1 (§4.3). */
export function houseHeatingPowerGaugeFill(state, ratedCapacityW = null) {
  const liveW = houseHeatingPowerW(state);
  if (liveW === null) return null;
  const maxW = houseHeatingPowerGaugeMax(liveW, ratedCapacityW);
  return liveW / maxW;
}
