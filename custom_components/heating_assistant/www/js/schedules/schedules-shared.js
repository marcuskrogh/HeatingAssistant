import { getRoomScheduleData, periodRowHtml } from '../schedule-utils.js?v=100';
import { experimentStatusInfo } from '../experiment-utils.js?v=100';

export const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

export const getScheduleDataForRoom = getRoomScheduleData;

/** Compare two period arrays for structural equality (JSON snapshot). */
export function periodsMatch(a, b) {
  const left = a ?? [];
  const right = b ?? [];
  if (left.length !== right.length) return false;
  return JSON.stringify(left) === JSON.stringify(right);
}

/**
 * Resolve schedule data for one room: prefer non-empty WebSocket payload,
 * then patched config-entity state. Optional ``savedSnapshot`` holds the
 * last successful save until the coordinator confirms the same payload.
 */
export function resolveRoomScheduleData(room, roomSchedules, state, savedSnapshot = null) {
  const fromWs = getScheduleDataForRoom(roomSchedules, room);
  const wsPeriods = fromWs?.periods ?? [];

  if (savedSnapshot !== null) {
    const snapLen = savedSnapshot.length;
    const wsLen = wsPeriods.length;
    if (wsLen < snapLen || wsLen > snapLen) {
      const fromState = getScheduleDataForRoom(
        state[CONFIG_ENTITY]?.attributes?.room_schedules,
        room,
      );
      const enabled = fromState?.enabled ?? fromWs?.enabled ?? true;
      return { enabled, periods: [...savedSnapshot] };
    }
    if (periodsMatch(wsPeriods, savedSnapshot)) {
      // WS caught up — caller may clear snapshot after this returns.
    }
  }

  if (wsPeriods.length > 0) {
    return fromWs;
  }

  const fromState = getScheduleDataForRoom(
    state[CONFIG_ENTITY]?.attributes?.room_schedules,
    room,
  );
  if ((fromState?.periods?.length ?? 0) > 0) {
    return fromState;
  }

  return fromWs ?? fromState ?? null;
}

/**
 * Merge WebSocket schedule payloads with config-entity fallbacks so list
 * views stay consistent when getSchedules() is stale or empty after a save.
 */
export function mergeRoomSchedulesWithState(roomSchedules, state) {
  const ws = roomSchedules || {};
  const fromState = state?.[CONFIG_ENTITY]?.attributes?.room_schedules || {};
  if (!fromState || Object.keys(fromState).length === 0) {
    return ws;
  }

  const merged = { ...fromState, ...ws };
  for (const [key, sched] of Object.entries(fromState)) {
    const wsSched = ws[key];
    const wsPeriods = wsSched?.periods ?? [];
    const statePeriods = sched?.periods ?? [];
    if (wsPeriods.length === 0 && statePeriods.length > 0) {
      merged[key] = sched;
    }
  }
  return merged;
}
/** Renders a single period summary row element. */
export function makePeriodRow(p, isActive, isNext, periodIndex = null) {
  const row = document.createElement('div');
  row.innerHTML = periodRowHtml(p, isActive, isNext, periodIndex);
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
