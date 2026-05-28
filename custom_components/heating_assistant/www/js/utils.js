export function formatTemperature(value) {
  const num = parseFloat(value);
  if (isNaN(num)) return '—';
  return num.toFixed(1) + '°C';
}

export function formatPower(value) {
  const num = parseFloat(value);
  if (isNaN(num)) return '—';
  if (Math.abs(num) >= 1000) return (num / 1000).toFixed(1) + ' kW';
  return num.toFixed(0) + ' W';
}

export function formatEnergy(value) {
  const num = parseFloat(value);
  if (isNaN(num)) return '—';
  if (Math.abs(num) >= 1000) return (num / 1000).toFixed(1) + ' MWh';
  return num.toFixed(1) + ' kWh';
}

export function formatDuration(seconds) {
  const num = parseFloat(seconds);
  if (isNaN(num)) return '—';
  if (num < 1) return (num * 1000).toFixed(0) + ' ms';
  return num.toFixed(2) + ' s';
}

export function formatPercent(value) {
  const num = parseFloat(value);
  if (isNaN(num)) return '—';
  return num.toFixed(0) + '%';
}

export function formatNumber(value, decimals = 2) {
  const num = parseFloat(value);
  if (isNaN(num)) return '—';
  return num.toFixed(decimals);
}

export function formatCountdown(seconds) {
  const num = Math.max(0, Math.round(seconds));
  const m = Math.floor(num / 60);
  const s = num % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function modelFitLabel(r2) {
  const num = parseFloat(r2);
  if (isNaN(num)) return { label: '—', class: '' };
  if (num > 0.8) return { label: 'GOOD', class: 'fit--good' };
  if (num > 0.5) return { label: 'ACCEPTABLE', class: 'fit--acceptable' };
  return { label: 'POOR', class: 'fit--poor' };
}

export function severityColor(value, thresholds) {
  if (value >= thresholds.good) return 'var(--good)';
  if (value >= thresholds.warning) return 'var(--warning)';
  return 'var(--alarm)';
}

export function severityColorInverse(value, thresholds) {
  if (value <= thresholds.good) return 'var(--good)';
  if (value <= thresholds.warning) return 'var(--warning)';
  return 'var(--alarm)';
}

export function entityValue(state, entityId) {
  if (!entityId) return null;
  const entity = state[entityId];
  if (!entity) return null;
  const val = parseFloat(entity.state);
  return isNaN(val) ? null : val;
}

export function entityAttr(state, entityId, attr) {
  if (!entityId) return null;
  const entity = state[entityId];
  if (!entity || !entity.attributes) return null;
  return entity.attributes[attr] ?? null;
}

export function entityLastUpdated(state, entityId) {
  if (!entityId) return null;
  const entity = state[entityId];
  if (!entity) return null;
  return entity.last_updated ? new Date(entity.last_updated) : null;
}

export const ENTITY_PREFIX = 'sensor.heating_assistant_';

export function systemEntity(metric) {
  return ENTITY_PREFIX + metric;
}

export function roomEntity(roomSlug, metric) {
  return ENTITY_PREFIX + roomSlug + '_' + metric;
}

export const MAX_SOLVE_TIME_S = 2.0;
