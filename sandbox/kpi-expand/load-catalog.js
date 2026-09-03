/**
 * Sandbox candidate: split load KPIs and grouped detail rows.
 * Production catalog stays until promote.
 */

function dash(value) {
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
}

function yesNo(flag) {
  if (flag === true) return 'yes';
  if (flag === false) return 'no';
  return '—';
}

function formatNumber(value, digits) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  return num.toFixed(digits);
}

function formatSeconds(value) {
  const num = parseFloat(value);
  if (!Number.isFinite(num)) return '—';
  return `${formatNumber(num, 2)} s`;
}

function formatPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  return `${formatNumber(num, 0)}%`;
}

function formatUnix(ts) {
  const num = parseFloat(ts);
  if (!Number.isFinite(num) || num <= 0) return '—';
  const date = new Date(num * 1000);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
}

function mpcEntity(state) {
  return state['sensor.heating_assistant_mpc_performance'] || { state: null, attributes: {} };
}

export const REGULATOR_BUDGET_S = 2;
export const NMPC_LOAD_FRACTION = 0.1;

export function nmpcLoadBudgetS(periodS) {
  const period = Number(periodS);
  if (!Number.isFinite(period) || period <= 0) return null;
  return NMPC_LOAD_FRACTION * period;
}

export function nmpcLoadPercent(state) {
  const entity = mpcEntity(state);
  const duration = parseFloat(entity.attributes?.last_nmpc_duration_s);
  const budget = nmpcLoadBudgetS(entity.attributes?.nmpc_period_s);
  if (!Number.isFinite(duration) || budget == null || budget <= 0) return null;
  return Math.min(100, (duration / budget) * 100);
}

export function regulatorLoadPercent(state) {
  const duration = parseFloat(mpcEntity(state).state);
  if (!Number.isFinite(duration)) return null;
  return Math.min(100, (duration / REGULATOR_BUDGET_S) * 100);
}

function nmpcRows(state) {
  const entity = mpcEntity(state);
  const load = nmpcLoadPercent(state);
  const budget = nmpcLoadBudgetS(entity.attributes?.nmpc_period_s);
  return [
    { label: 'Load', value: load == null ? '—' : formatPercent(load) },
    { label: 'Last NMPC solve', value: formatSeconds(entity.attributes?.last_nmpc_duration_s) },
    { label: 'Load budget', value: budget == null ? '—' : `${formatNumber(budget, 0)} s (10% of period)` },
    { label: 'NMPC computing', value: yesNo(entity.attributes?.nmpc_computing) },
    { label: 'NMPC period', value: formatSeconds(entity.attributes?.nmpc_period_s) },
    { label: 'Last NMPC result', value: formatUnix(entity.attributes?.nmpc_result_ts) },
  ];
}

function regulatorRows(state) {
  const entity = mpcEntity(state);
  const load = regulatorLoadPercent(state);
  return [
    { label: 'Load', value: load == null ? '—' : formatPercent(load) },
    { label: 'Last P cycle', value: formatSeconds(entity.state) },
    { label: 'Load budget', value: `${formatNumber(REGULATOR_BUDGET_S, 0)} s` },
    { label: 'P computing', value: yesNo(entity.attributes?.control_computing) },
    { label: 'Control interval', value: formatSeconds(entity.attributes?.dt_s) },
    { label: 'Last ran', value: formatUnix(entity.attributes?.last_control_ran_ts) },
  ];
}

export function nmpcLoadDetail(state) {
  return {
    description:
      'Share of the NMPC load budget used by the last NMPC solve. '
      + 'The budget is 10% of the NMPC period. This card is NMPC only.',
    sections: [{ title: 'NMPC', rows: nmpcRows(state) }],
  };
}

export function regulatorLoadDetail(state) {
  return {
    description:
      `Share of the ${formatNumber(REGULATOR_BUDGET_S, 0)} s load budget used by the last room-level P-cycle. `
      + 'NMPC wall-clock time is listed below and does not change this percent.',
    sections: [
      { title: 'Regulator', rows: regulatorRows(state) },
      { title: 'NMPC', rows: nmpcRows(state) },
    ],
  };
}

export function overallHealthDetail(state) {
  const summary = state['sensor.heating_assistant_system_summary'] || { attributes: {} };
  const quality = summary.attributes.system_quality;
  let qualityLabel = 'HEALTHY';
  if (quality === 'warning') qualityLabel = 'WARNING';
  if (quality === 'error') qualityLabel = 'ERROR';
  return {
    description: 'Combined house quality from MQTT, tags, and identification history.',
    sections: [{
      title: 'System',
      rows: [
        { label: 'Quality', value: qualityLabel },
        { label: 'Issue', value: summary.attributes.issue_summary || 'No active issues' },
        { label: 'MQTT', value: summary.attributes.mqtt_connected === false ? 'disconnected' : 'ok' },
      ],
    }],
  };
}

export { dash };
