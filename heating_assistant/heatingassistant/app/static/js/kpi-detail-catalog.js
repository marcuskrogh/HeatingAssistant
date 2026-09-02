/**
 * Description and absolute-value rows for expandable KPI cards.
 */

import {
  houseComfortIndex,
  houseHeatingPowerKw,
  houseHeatingPowerGaugeFill,
  houseHeatingPowerGaugeMax,
  houseHeatingPowerW,
  houseEffectiveCop,
  houseMeanTrackingError,
  houseModelFit,
  mpcLoadPercent,
  roomTimeInRangePct,
  roomHeatLoss,
  heatLossGaugeMax,
  solarGainGaugeMax,
  roomModelFit,
  houseComfortBreakdown,
} from './kpi-engine.js?v=124';
import {
  formatEnergy,
  formatPercent,
  formatPower,
  formatPowerKw,
  formatNumber,
  formatCountdown,
  formatPrice,
  entityValue,
  entityAttr,
  systemEntity,
  MAX_SOLVE_TIME_S,
} from './utils.js?v=127';
import { COUNTDOWN_CONTROL, COUNTDOWN_NMPC, countdownRemaining } from './components/countdown.js?v=146';

function dash(value) {
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
}

function yesNo(flag) {
  if (flag === true) return 'yes';
  if (flag === false) return 'no';
  return '—';
}

function formatSeconds(value) {
  if (value === null || value === undefined || value === '') return '—';
  const num = parseFloat(value);
  if (!Number.isFinite(num)) return '—';
  return `${formatNumber(num, 2)} s`;
}

function formatUnix(ts) {
  const num = parseFloat(ts);
  if (!Number.isFinite(num) || num <= 0) return '—';
  const date = new Date(num * 1000);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
}

export function overallHealthDetail(state) {
  const quality = entityAttr(state, systemEntity('system_summary'), 'system_quality');
  const issue = entityAttr(state, systemEntity('system_summary'), 'issue_summary');
  const mqtt = entityAttr(state, systemEntity('system_summary'), 'mqtt_connected');
  let qualityLabel = 'HEALTHY';
  if (quality === 'warning') qualityLabel = 'WARNING';
  if (quality === 'error') qualityLabel = 'ERROR';
  return {
    description: 'Combined house quality from MQTT, tags, and identification history.',
    rows: [
      { label: 'Quality', value: qualityLabel },
      { label: 'Issue', value: issue || 'No active issues' },
      { label: 'MQTT', value: mqtt === false ? 'disconnected' : 'ok' },
    ],
  };
}

export function mpcLoadDetail(state) {
  const entity = systemEntity('mpc_performance');
  const pDuration = entityValue(state, entity);
  const nmpcDuration = entityAttr(state, entity, 'last_nmpc_duration_s');
  const load = mpcLoadPercent(state);
  return {
    description:
      `Share of the ${formatNumber(MAX_SOLVE_TIME_S, 0)} s load budget used by the last P-cycle duration. `
      + 'The percent is not NMPC wall-clock time.',
    rows: [
      { label: 'Load', value: load == null ? '—' : formatPercent(load) },
      { label: 'Last P cycle', value: formatSeconds(pDuration) },
      { label: 'Last NMPC solve', value: formatSeconds(nmpcDuration) },
      { label: 'NMPC computing', value: yesNo(entityAttr(state, entity, 'nmpc_computing')) },
      { label: 'P computing', value: yesNo(entityAttr(state, entity, 'control_computing')) },
      { label: 'Control interval', value: formatSeconds(entityAttr(state, entity, 'dt_s')) },
      { label: 'NMPC period', value: formatSeconds(entityAttr(state, entity, 'nmpc_period_s')) },
      { label: 'Last NMPC result', value: formatUnix(entityAttr(state, entity, 'nmpc_result_ts')) },
    ],
  };
}

export function comfortDetail(state, rooms) {
  const idx = houseComfortIndex(state, rooms);
  const parts = houseComfortBreakdown(state, rooms);
  const out = parts.outNames.length ? parts.outNames.join(', ') : 'none';
  return {
    description: 'Share of active rooms whose temperature is inside the comfort band.',
    rows: [
      { label: 'In band', value: idx == null ? '—' : formatPercent(idx) },
      { label: 'Rooms', value: parts.eligible ? `${parts.inBand} / ${parts.eligible}` : '—' },
      { label: 'Out of band', value: parts.eligible ? out : '—' },
    ],
  };
}

export function heatingPowerDetail(state) {
  const liveW = houseHeatingPowerW(state);
  const liveKw = houseHeatingPowerKw(state);
  const fill = houseHeatingPowerGaugeFill(state);
  const maxW = liveW == null ? null : houseHeatingPowerGaugeMax(liveW);
  return {
    description: 'Sum of measured heater power across the house.',
    rows: [
      { label: 'Live', value: liveKw == null ? '—' : formatPowerKw(liveKw) },
      { label: 'Fill', value: fill == null ? '—' : formatPercent(fill * 100) },
      { label: 'Gauge max', value: maxW == null ? '—' : formatPower(maxW) },
    ],
  };
}

export function systemCopDetail(state) {
  const cop = houseEffectiveCop(state);
  return {
    description: 'Effective system COP when a heat pump is in the house.',
    rows: [
      { label: 'COP', value: cop == null ? '—' : formatNumber(cop, 2) },
    ],
  };
}

export function dailyEnergyDetail(kwh, ready) {
  return {
    description: 'Heat delivered since local midnight.',
    rows: [
      { label: 'Today', value: ready ? formatEnergy(kwh) : '—' },
    ],
  };
}

export function trackingErrorDetail(state, rooms) {
  const err = houseMeanTrackingError(state, rooms);
  return {
    description: 'Mean absolute temperature error versus setpoint across active rooms.',
    rows: [
      { label: 'Mean error', value: err == null ? '—' : `${formatNumber(err, 2)}°C` },
    ],
  };
}

export function houseModelFitDetail(state, rooms) {
  const fit = houseModelFit(state, rooms);
  return {
    description: 'Aggregate model fit from room R² scores.',
    rows: [
      { label: 'Label', value: dash(fit.label) },
      { label: 'Score', value: fit.value == null ? '—' : formatNumber(fit.value, 2) },
    ],
  };
}

export function timeInRangeDetail(state, room, roomActive) {
  const pct = roomTimeInRangePct(state, room, roomActive);
  const lower = entityValue(state, room.entities?.constraint_lower);
  const upper = entityValue(state, room.entities?.constraint_upper);
  const temp = entityValue(
    state,
    room.entities?.temperature_filtered || room.entities?.temperature_measured,
  );
  return {
    description: 'Share of the last 24 hours this room stayed inside its comfort band.',
    rows: [
      { label: 'Time in range', value: pct == null ? '—' : `${Math.round(pct)}%` },
      { label: 'Band', value: lower == null || upper == null ? '—' : `${formatNumber(lower, 1)}–${formatNumber(upper, 1)}°C` },
      { label: 'Temperature', value: temp == null ? '—' : `${formatNumber(temp, 1)}°C` },
    ],
  };
}

export function roomPowerDetail(state, room, bounds) {
  const watts = entityValue(state, room.entities?.heating_power_measured);
  return {
    description: 'Measured heater power for this room.',
    rows: [
      { label: 'Power', value: watts == null ? '—' : formatPower(watts) },
      { label: 'Gauge min', value: formatPower(bounds.min) },
      { label: 'Gauge max', value: formatPower(bounds.max) },
    ],
  };
}

export function energyPriceDetail(state, priceEntity, bounds) {
  const value = entityValue(state, priceEntity);
  const unit = entityAttr(state, priceEntity, 'unit_of_measurement');
  const text = formatPrice(value);
  const withUnit = text === '—' ? '—' : (unit ? `${text} ${unit}` : text);
  return {
    description: 'Current electricity price, with the bar spanning the upcoming forecast.',
    rows: [
      { label: 'Price', value: withUnit },
      { label: 'Forecast min', value: formatPrice(bounds.min) },
      { label: 'Forecast max', value: formatPrice(bounds.max) },
    ],
  };
}

export function solarGainDetail(state, room) {
  const entity = room.entities?.solar_gain_measured;
  const watts = entityValue(state, entity);
  return {
    description: 'Applied solar gain for this room.',
    rows: [
      { label: 'Gain', value: watts == null ? '—' : formatPower(watts) },
      { label: 'Gauge max', value: formatPower(solarGainGaugeMax(watts ?? 0)) },
    ],
  };
}

export function heatLossDetail(state, room) {
  const watts = roomHeatLoss(state, room);
  return {
    description: 'Instantaneous heat loss for this room.',
    rows: [
      { label: 'Loss', value: watts == null ? '—' : formatPower(watts) },
      { label: 'Gauge max', value: formatPower(heatLossGaugeMax(watts ?? 0)) },
    ],
  };
}

export function roomModelFitDetail(state, room) {
  const fit = roomModelFit(state, room);
  return {
    description: 'Open-loop model fit for this room.',
    rows: [
      { label: 'Label', value: dash(fit.label) },
      { label: 'Score', value: fit.value == null ? '—' : formatNumber(fit.value, 2) },
    ],
  };
}

export function nextControlDetail(state) {
  const entity = systemEntity('mpc_performance');
  const remaining = countdownRemaining(state, COUNTDOWN_CONTROL);
  return {
    description: 'Time until the next P tick on the shared Start epoch.',
    rows: [
      { label: 'Remaining', value: remaining == null ? '—' : formatCountdown(remaining) },
      { label: 'Interval', value: formatSeconds(entityAttr(state, entity, 'dt_s')) },
      { label: 'Computing', value: yesNo(entityAttr(state, entity, 'control_computing')) },
      { label: 'Last ran', value: formatUnix(entityAttr(state, entity, 'last_control_ran_ts')) },
    ],
  };
}

export function nextNmpcDetail(state) {
  const entity = systemEntity('mpc_performance');
  const remaining = countdownRemaining(state, COUNTDOWN_NMPC);
  return {
    description: 'Time until the next NMPC slot on the shared Start epoch.',
    rows: [
      { label: 'Remaining', value: remaining == null ? '—' : formatCountdown(remaining) },
      { label: 'Period', value: formatSeconds(entityAttr(state, entity, 'nmpc_period_s')) },
      { label: 'Computing', value: yesNo(entityAttr(state, entity, 'nmpc_computing')) },
      { label: 'Last NMPC', value: formatUnix(entityAttr(state, entity, 'last_nmpc_ts')) },
    ],
  };
}
