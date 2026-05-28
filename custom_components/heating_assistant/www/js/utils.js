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

export function formatDuration(seconds) {
  const num = parseFloat(seconds);
  if (isNaN(num)) return '—';
  if (num < 1) return (num * 1000).toFixed(0) + ' ms';
  return num.toFixed(2) + ' s';
}

export function formatNumber(value, decimals = 2) {
  const num = parseFloat(value);
  if (isNaN(num)) return '—';
  return num.toFixed(decimals);
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
  const entity = state[entityId];
  if (!entity) return null;
  const val = parseFloat(entity.state);
  return isNaN(val) ? null : val;
}

export function entityAttr(state, entityId, attr) {
  const entity = state[entityId];
  if (!entity || !entity.attributes) return null;
  return entity.attributes[attr] ?? null;
}

export const ENTITY_PREFIX = 'sensor.heating_assistant_';

export function systemEntity(metric) {
  return ENTITY_PREFIX + metric;
}

export function roomEntity(roomSlug, metric) {
  return ENTITY_PREFIX + roomSlug + '_' + metric;
}
