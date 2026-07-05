import { getRoomScheduleData, periodRowHtml } from '../schedule-utils.js?v=95';
import { experimentStatusInfo } from '../experiment-utils.js?v=95';

export const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

export const getScheduleDataForRoom = getRoomScheduleData;

/** Robust lookup: tries slug, name, and case-insensitive normalised match. */


/** Renders a single period summary row element. */
export function makePeriodRow(p, isActive, isNext) {
  const row = document.createElement('div');
  row.innerHTML = periodRowHtml(p, isActive, isNext);
  return row.firstElementChild;
}

export const EXCITATION_OPTIONS = [

  { value: 'step', label: 'Step (recommended)' },
  { value: 'prbs', label: 'PRBS' },
  { value: 'pulse', label: 'Pulse' },
];

export function fmtExpTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function fmtExpDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function fmtExpWindow(exp) {
  if (!exp.start_ts) return '—';
  const sd = new Date(exp.start_ts * 1000);
  const ed = new Date((exp.end_ts || 0) * 1000);
  const sameDay = sd.toDateString() === ed.toDateString();
  const startStr = `${fmtExpDate(exp.start_ts)} ${fmtExpTime(exp.start_ts)}`;
  const endStr = sameDay ? fmtExpTime(exp.end_ts) : `${fmtExpDate(exp.end_ts)} ${fmtExpTime(exp.end_ts)}`;
  return `${startStr} → ${endStr}`;
}

export function tsToLocalInput(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function expStatusInfo(status) {
  return experimentStatusInfo(status);
}

export function expCardModifier(status) {
  if (status === 'running') return ' schedule-form__period--exp-running';
  if (status === 'scheduled') return ' schedule-form__period--exp-scheduled';
  return '';
}

/** Optimistically patch controller-config room_schedules in panel state. */
export function patchStateSchedule(state, slug, periods, enabled) {
  const existingEntity = state[CONFIG_ENTITY] || {
    entity_id: CONFIG_ENTITY,
    state: 'ok',
    attributes: {},
  };
  const existingAttrs = existingEntity.attributes || {};
  const existingSchedules = existingAttrs.room_schedules || {};
  const resolvedEnabled = enabled !== undefined
    ? enabled
    : (existingSchedules[slug]?.enabled ?? true);

  state[CONFIG_ENTITY] = {
    ...existingEntity,
    attributes: {
      ...existingAttrs,
      room_schedules: {
        ...existingSchedules,
        [slug]: { enabled: resolvedEnabled, periods },
      },
    },
  };
}
