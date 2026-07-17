/* schedule-utils.js — shared schedule helpers.
 *
 * A single source of truth for resolving the *currently active* (and next)
 * schedule period, mirroring the Python ``SchedulePeriod.matches()`` logic.
 * Used by the schedules page and the climate cards so the "NOW" indicator is
 * always consistent across the dashboard.
 */

function localDateString(now) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function localHhmm(now) {
  return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
}

function localDateTimeString(now) {
  return `${localDateString(now)}T${localHhmm(now)}:00`;
}

export const SCHEDULE_TYPE_WEEKLY = 'weekly_recurring';
export const SCHEDULE_TYPE_DATE_RANGE = 'date_range_daily';
export const SCHEDULE_TYPE_CONTINUOUS = 'continuous_span';
export const SCHEDULE_TYPES = [
  SCHEDULE_TYPE_WEEKLY,
  SCHEDULE_TYPE_DATE_RANGE,
  SCHEDULE_TYPE_CONTINUOUS,
];

export const COMFORT_OVERRIDE_FIELDS = [
  'setpoint',
  'comfort_offset',
  'tracking_weight',
  'energy_weight',
];
export const OFF_OVERRIDE_FIELDS = ['frost_protection'];
export const OVERRIDE_FIELDS = [...COMFORT_OVERRIDE_FIELDS, ...OFF_OVERRIDE_FIELDS];

const DEFAULT_DAYS = [0, 1, 2, 3, 4, 5, 6];

export const OVERRIDE_META = {
  setpoint: {
    label: 'Setpoint',
    unit: 'degC',
    step: '0.5',
    min: '5',
    max: '35',
    hint: 'Room base setpoint override',
  },
  comfort_offset: {
    label: 'Comfort Offset',
    unit: '+/- degC',
    step: '0.1',
    min: '0.1',
    max: '5',
    hint: 'Comfort band half-width override',
  },
  tracking_weight: {
    label: 'Tracking Weight',
    unit: '',
    step: '0.1',
    min: '0',
    max: '10',
    hint: 'Inherited baseline is 1.0',
  },
  energy_weight: {
    label: 'Energy Weight',
    unit: '',
    step: '0.01',
    min: '0',
    max: '10',
    hint: 'Inherited baseline is 1.0',
  },
  frost_protection: {
    label: 'Frost Protection',
    unit: 'degC',
    step: '0.5',
    min: '0',
    max: '15',
    hint: 'Inherited frost floor is 12 degC',
  },
};

export function overrideBaseline(field, defaults = {}) {
  if (field === 'setpoint') return defaults.setpoint ?? 21;
  if (field === 'comfort_offset') return defaults.comfort_offset ?? 2.0;
  if (field === 'tracking_weight') return 1.0;
  if (field === 'energy_weight') return 1.0;
  if (field === 'frost_protection') return 12;
  return undefined;
}

export function activeOverrideFields(mode) {
  return mode === 'off' ? OFF_OVERRIDE_FIELDS : COMFORT_OVERRIDE_FIELDS;
}

export function hasOverride(period, field) {
  return period && period[field] !== undefined && period[field] !== null;
}

function parseNumberOrNull(value) {
  if (value === '' || value === undefined || value === null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeScheduleType(p) {
  if (SCHEDULE_TYPES.includes(p.schedule_type)) return p.schedule_type;
  return p.recurring === false ? SCHEDULE_TYPE_DATE_RANGE : SCHEDULE_TYPE_WEEKLY;
}

function normalizeTimeMode(p, fallback = 'window') {
  if (p.time_mode === 'all_day' || p.time_mode === 'window') return p.time_mode;
  if (p.all_day === true) return 'all_day';
  return fallback;
}

function todayString() {
  return localDateString(new Date());
}

function normalizeDateTimeInput(value, fallback) {
  if (!value) return fallback;
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(text)) {
    return text.slice(0, 16);
  }
  return text;
}

function serializeDateTimeInput(value) {
  if (!value) return value;
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) return `${text}:00`;
  return text;
}

function defaultWhenByType(p = {}) {
  const today = todayString();
  return {
    [SCHEDULE_TYPE_WEEKLY]: {
      time_mode: 'window',
      start: p.start || '08:00',
      end: p.end || '22:00',
      days: p.days ? [...p.days] : [...DEFAULT_DAYS],
    },
    [SCHEDULE_TYPE_DATE_RANGE]: {
      time_mode: 'window',
      start: p.start || '08:00',
      end: p.end || '22:00',
      start_date: p.start_date || today,
      end_date: p.end_date || today,
    },
    [SCHEDULE_TYPE_CONTINUOUS]: {
      start_at: normalizeDateTimeInput(p.start_at, `${today}T08:00`),
      end_at: normalizeDateTimeInput(p.end_at, `${today}T22:00`),
    },
  };
}

function normalizeWhenByType(p) {
  const when = defaultWhenByType(p);
  const existing = p._whenByType || {};
  const hasEditorWhen = Boolean(p._whenByType);

  if (existing[SCHEDULE_TYPE_WEEKLY]) {
    when[SCHEDULE_TYPE_WEEKLY] = {
      ...when[SCHEDULE_TYPE_WEEKLY],
      ...existing[SCHEDULE_TYPE_WEEKLY],
      days: existing[SCHEDULE_TYPE_WEEKLY].days
        ? [...existing[SCHEDULE_TYPE_WEEKLY].days]
        : [...when[SCHEDULE_TYPE_WEEKLY].days],
    };
  }
  if (existing[SCHEDULE_TYPE_DATE_RANGE]) {
    when[SCHEDULE_TYPE_DATE_RANGE] = {
      ...when[SCHEDULE_TYPE_DATE_RANGE],
      ...existing[SCHEDULE_TYPE_DATE_RANGE],
    };
  }
  if (existing[SCHEDULE_TYPE_CONTINUOUS]) {
    when[SCHEDULE_TYPE_CONTINUOUS] = {
      ...when[SCHEDULE_TYPE_CONTINUOUS],
      ...existing[SCHEDULE_TYPE_CONTINUOUS],
    };
  }

  const scheduleType = normalizeScheduleType(p);
  if (scheduleType === SCHEDULE_TYPE_WEEKLY) {
    when[SCHEDULE_TYPE_WEEKLY] = {
      time_mode: hasEditorWhen
        ? when[SCHEDULE_TYPE_WEEKLY].time_mode
        : normalizeTimeMode(p, when[SCHEDULE_TYPE_WEEKLY].time_mode),
      start: hasEditorWhen ? when[SCHEDULE_TYPE_WEEKLY].start : p.start || when[SCHEDULE_TYPE_WEEKLY].start,
      end: hasEditorWhen ? when[SCHEDULE_TYPE_WEEKLY].end : p.end || when[SCHEDULE_TYPE_WEEKLY].end,
      days: hasEditorWhen
        ? [...when[SCHEDULE_TYPE_WEEKLY].days]
        : p.days ? [...p.days] : [...when[SCHEDULE_TYPE_WEEKLY].days],
    };
  } else if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {
    when[SCHEDULE_TYPE_DATE_RANGE] = {
      time_mode: hasEditorWhen
        ? when[SCHEDULE_TYPE_DATE_RANGE].time_mode
        : normalizeTimeMode(p, when[SCHEDULE_TYPE_DATE_RANGE].time_mode),
      start: hasEditorWhen ? when[SCHEDULE_TYPE_DATE_RANGE].start : p.start || when[SCHEDULE_TYPE_DATE_RANGE].start,
      end: hasEditorWhen ? when[SCHEDULE_TYPE_DATE_RANGE].end : p.end || when[SCHEDULE_TYPE_DATE_RANGE].end,
      start_date: hasEditorWhen ? when[SCHEDULE_TYPE_DATE_RANGE].start_date : p.start_date || when[SCHEDULE_TYPE_DATE_RANGE].start_date,
      end_date: hasEditorWhen ? when[SCHEDULE_TYPE_DATE_RANGE].end_date : p.end_date || when[SCHEDULE_TYPE_DATE_RANGE].end_date,
    };
  } else {
    when[SCHEDULE_TYPE_CONTINUOUS] = {
      start_at: hasEditorWhen
        ? normalizeDateTimeInput(when[SCHEDULE_TYPE_CONTINUOUS].start_at, when[SCHEDULE_TYPE_CONTINUOUS].start_at)
        : normalizeDateTimeInput(p.start_at, when[SCHEDULE_TYPE_CONTINUOUS].start_at),
      end_at: hasEditorWhen
        ? normalizeDateTimeInput(when[SCHEDULE_TYPE_CONTINUOUS].end_at, when[SCHEDULE_TYPE_CONTINUOUS].end_at)
        : normalizeDateTimeInput(p.end_at, when[SCHEDULE_TYPE_CONTINUOUS].end_at),
    };
  }

  return when;
}

function applyActiveWhenFields(out, scheduleType, whenByType) {
  const when = whenByType[scheduleType];
  if (scheduleType === SCHEDULE_TYPE_WEEKLY) {
    out.time_mode = when.time_mode || 'window';
    if (out.time_mode === 'window') {
      out.start = when.start || '08:00';
      out.end = when.end || '22:00';
    }
    out.days = when.days ? [...when.days] : [...DEFAULT_DAYS];
    return;
  }

  if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {
    out.time_mode = when.time_mode || 'window';
    if (out.time_mode === 'window') {
      out.start = when.start || '08:00';
      out.end = when.end || '22:00';
    }
    out.start_date = when.start_date || todayString();
    out.end_date = when.end_date || out.start_date;
    return;
  }

  out.start_at = serializeDateTimeInput(when.start_at);
  out.end_at = serializeDateTimeInput(when.end_at);
}

function preserveInactiveWhenFields(out, scheduleType, whenByType) {
  if (scheduleType !== SCHEDULE_TYPE_WEEKLY) {
    const weekly = whenByType[SCHEDULE_TYPE_WEEKLY];
    out.days = weekly.days ? [...weekly.days] : [...DEFAULT_DAYS];
  }
  if (scheduleType !== SCHEDULE_TYPE_DATE_RANGE) {
    const dateRange = whenByType[SCHEDULE_TYPE_DATE_RANGE];
    out.start_date = dateRange.start_date;
    out.end_date = dateRange.end_date;
  }
  if (scheduleType !== SCHEDULE_TYPE_CONTINUOUS) {
    const continuous = whenByType[SCHEDULE_TYPE_CONTINUOUS];
    out.start_at = serializeDateTimeInput(continuous.start_at);
    out.end_at = serializeDateTimeInput(continuous.end_at);
  }
}

function applyOverrideFields(out, period, defaults) {
  for (const field of OVERRIDE_FIELDS) {
    const value = parseNumberOrNull(period[field]);
    if (value === null) continue;
    if (value !== overrideBaseline(field, defaults)) {
      out[field] = value;
    }
  }
}

/** Mirrors Python ``SchedulePeriod._matches_time_of_day``. */
function periodMatchesTimeOfDay(p, now, applyWeekdayFilter) {
  const day = (now.getDay() + 6) % 7;
  const hhmm = localHhmm(now);
  const days = p.days || [0, 1, 2, 3, 4, 5, 6];
  const wraps = p.end <= p.start;

  if (wraps) {
    if (!applyWeekdayFilter) {
      return hhmm >= p.start || hhmm < p.end;
    }
    const prevDay = (day + 6) % 7;
    const inFirstHalf = hhmm >= p.start && days.includes(day);
    const inSecondHalf = hhmm < p.end && days.includes(prevDay);
    return inFirstHalf || inSecondHalf;
  }

  if (applyWeekdayFilter && !days.includes(day)) return false;
  return hhmm >= p.start && hhmm < p.end;
}

function matchesWeeklyRecurring(p, now) {
  const day = (now.getDay() + 6) % 7;
  const days = p.days || [0, 1, 2, 3, 4, 5, 6];
  if (p.time_mode === 'all_day') {
    return days.includes(day);
  }
  return periodMatchesTimeOfDay(p, now, true);
}

function matchesDateRangeDaily(p, now) {
  const dateStr = localDateString(now);
  if (!p.start_date || !p.end_date) return false;
  if (dateStr < p.start_date || dateStr > p.end_date) return false;
  if (p.time_mode === 'all_day') return true;
  return periodMatchesTimeOfDay(p, now, false);
}

function matchesContinuousSpan(p, now) {
  if (!p.start_at || !p.end_at) return false;
  const current = localDateTimeString(now);
  return current >= p.start_at && current < p.end_at;
}

/** Mirrors Python ``SchedulePeriod.matches``. */
export function periodMatchesNow(p, now = new Date()) {
  if (p.enabled === false) return false;

  const scheduleType = p.schedule_type
    || (p.recurring === false ? SCHEDULE_TYPE_DATE_RANGE : SCHEDULE_TYPE_WEEKLY);

  if (scheduleType === SCHEDULE_TYPE_CONTINUOUS) {
    return matchesContinuousSpan(p, now);
  }
  if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {
    const normalized = { ...p };
    if (!normalized.time_mode) {
      normalized.time_mode = p.all_day ? 'all_day' : 'window';
    }
    return matchesDateRangeDaily(normalized, now);
  }

  const normalized = { ...p };
  if (!normalized.time_mode) {
    normalized.time_mode = p.all_day ? 'all_day' : 'window';
  }
  return matchesWeeklyRecurring(normalized, now);
}

/** Returns the period active right now, or null. */
export function findActivePeriod(periods) {
  if (!periods || !periods.length) return null;
  const now = new Date();
  for (const p of periods) {
    if (periodMatchesNow(p, now)) return p;
  }
  return null;
}

/** Returns the next upcoming period today, or null (weekly recurring window mode). */
export function findNextPeriod(periods) {
  if (!periods || !periods.length) return null;
  const now = new Date();
  const day = (now.getDay() + 6) % 7;
  const hhmm = localHhmm(now);
  let best = null;
  for (const p of periods) {
    if (p.enabled === false) continue;
    const scheduleType = p.schedule_type
      || (p.recurring === false ? SCHEDULE_TYPE_DATE_RANGE : SCHEDULE_TYPE_WEEKLY);
    if (scheduleType !== SCHEDULE_TYPE_WEEKLY) continue;
    const timeMode = p.time_mode || (p.all_day ? 'all_day' : 'window');
    if (timeMode === 'all_day') continue;
    const days = p.days || [0, 1, 2, 3, 4, 5, 6];
    if (!days.includes(day)) continue;
    if (p.start > hhmm) {
      if (!best || p.start < best.start) best = p;
    }
  }
  return best;
}

/** Human-readable time window for a period row. */
export function formatPeriodTime(p) {
  const scheduleType = p.schedule_type
    || (p.recurring === false ? SCHEDULE_TYPE_DATE_RANGE : SCHEDULE_TYPE_WEEKLY);
  const timeMode = p.time_mode || (p.all_day ? 'all_day' : 'window');

  if (scheduleType === SCHEDULE_TYPE_CONTINUOUS) {
    if (p.start_at && p.end_at) return `${p.start_at} → ${p.end_at}`;
    return 'Continuous span';
  }
  if (scheduleType === SCHEDULE_TYPE_DATE_RANGE && p.start_date && p.end_date) {
    if (timeMode === 'all_day') {
      if (p.start_date === p.end_date) return `${p.start_date} All day`;
      return `${p.start_date} → ${p.end_date} All day`;
    }
    const timePart = `${p.start || '—'}–${p.end || '—'}`;
    if (p.start_date === p.end_date) return `${p.start_date} ${timePart}`;
    return `${p.start_date} → ${p.end_date} ${timePart}`;
  }
  if (timeMode === 'all_day') return 'All day';
  return `${p.start || '—'}–${p.end || '—'}`;
}

/** Map persisted schedule_type payloads to the period editor state. */
export function normalizePeriodForEditor(p) {
  const scheduleType = normalizeScheduleType(p);
  const whenByType = normalizeWhenByType({ ...p, schedule_type: scheduleType });
  const activeWhen = whenByType[scheduleType];
  const out = {
    ...p,
    schedule_type: scheduleType,
    mode: p.mode || 'comfort',
    enabled: p.enabled !== false,
    _whenByType: whenByType,
  };

  if (scheduleType === SCHEDULE_TYPE_WEEKLY) {
    out.recurring = true;
    out.all_day = activeWhen.time_mode === 'all_day';
    out.time_mode = activeWhen.time_mode;
    out.start = activeWhen.start;
    out.end = activeWhen.end;
    out.days = [...(activeWhen.days || DEFAULT_DAYS)];
  } else if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {
    out.recurring = false;
    out.all_day = activeWhen.time_mode === 'all_day';
    out.time_mode = activeWhen.time_mode;
    out.start = activeWhen.start;
    out.end = activeWhen.end;
    out.start_date = activeWhen.start_date;
    out.end_date = activeWhen.end_date;
    out.days = [...(whenByType[SCHEDULE_TYPE_WEEKLY].days || DEFAULT_DAYS)];
  } else {
    out.recurring = false;
    out.all_day = false;
    out.start_at = activeWhen.start_at;
    out.end_at = activeWhen.end_at;
    out.days = [...(whenByType[SCHEDULE_TYPE_WEEKLY].days || DEFAULT_DAYS)];
  }

  return out;
}

/** Robust per-room schedule lookup: tries slug, name, and a case-insensitive
 *  normalised match.  Shared by the schedules page and the room-detail schedule
 *  overview so both resolve the same data from the coordinator payload. */
export function getRoomScheduleData(roomSchedules, room) {
  if (!roomSchedules) return null;
  if (roomSchedules[room.slug]) return roomSchedules[room.slug];
  if (roomSchedules[room.name]) return roomSchedules[room.name];
  const slug = room.slug.toLowerCase();
  for (const key of Object.keys(roomSchedules)) {
    if (key.toLowerCase().replace(/\s+/g, '_') === slug) return roomSchedules[key];
  }
  return null;
}

/** Returns { text, cls } describing a period's mode for display. */
export function periodModeDisplay(p) {
  if (p.mode === 'off') return { text: 'OFF', cls: 'sched-row__mode--off' };
  if (p.setpoint != null) return { text: `${p.setpoint}°C`, cls: 'sched-row__mode--comfort' };
  return { text: 'COMFORT', cls: 'sched-row__mode--comfort' };
}

/** ENABLED/DISABLED badge for schedule sections. */
export function scheduleEnabledBadgeHtml(enabled) {
  const on = enabled ?? true;
  return `<span class="sched-index-card__badge ${on ? 'sched-index-card__badge--on' : 'sched-index-card__badge--off'}">${on ? 'ENABLED' : 'DISABLED'}</span>`;
}

/** Per-period enabled toggle button HTML. */
export function periodEnabledToggleHtml(enabled, periodIndex) {
  const on = enabled !== false;
  return `<button type="button" class="sched-period-toggle ${on ? 'sched-period-toggle--on' : 'sched-period-toggle--off'}" data-period-enable="${periodIndex}" title="${on ? 'Disable period' : 'Enable period'}">${on ? 'ON' : 'OFF'}</button>`;
}

/** HTML for a single comfort-period summary row (shared across schedule surfaces). */
export function periodRowHtml(p, isActive, isNext, periodIndex = null) {
  const disabled = p.enabled === false;
  const toggle = periodIndex != null ? periodEnabledToggleHtml(p.enabled, periodIndex) : '';
  return `<div class="sched-row${isActive ? ' sched-row--active' : ''}${isNext ? ' sched-row--next' : ''}${disabled ? ' sched-row--disabled' : ''}">
    ${isActive ? '<span class="sched-row__now-badge">NOW</span>' : ''}
    ${isNext ? '<span class="sched-index-card__next-label">NEXT</span>' : ''}
    ${toggle}
    <span class="sched-row__name">${p.name || 'Period'}</span>
    <span class="sched-row__time">${formatPeriodTime(p)}</span>
    ${disabled ? '<span class="sched-row__disabled-label">DISABLED</span>' : ''}
  </div>`;
}

/** Section header used for comfort/experiment blocks inside schedule cards. */
export function scheduleSectionHeaderHtml(title, badgeHtml = '') {
  return `<div class="sched-section__header">
    <span class="sched-section__title">${title}</span>
    ${badgeHtml}
  </div>`;
}

/**
 * Move an entry in an array to a new index and return a new array.
 *
 * Used by the drag-to-reorder schedule priority UI on the room schedule detail
 * page (SWD-24). Kept as a pure helper so the reorder mechanics can be covered
 * by unit tests without spinning up the DOM harness.
 *
 * If either index is out of range or equals the other, the original array is
 * returned unchanged (as a shallow copy).
 */
export function movePeriodInList(list, fromIndex, toIndex) {
  if (!Array.isArray(list)) return list;
  const n = list.length;
  if (fromIndex < 0 || fromIndex >= n) return list.slice();
  if (toIndex < 0) toIndex = 0;
  if (toIndex > n - 1) toIndex = n - 1;
  if (fromIndex === toIndex) return list.slice();
  const next = list.slice();
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
}

/**
 * Remap a Set of indices after a single-item move within a list.
 *
 * Given the set of currently-expanded period indices, returns a new Set with
 * each index shifted to reflect the same period's new position after moving
 * the entry at `fromIndex` to `toIndex`. Companion to `movePeriodInList`.
 */
export function remapExpandedIndices(set, fromIndex, toIndex) {
  const out = new Set();
  if (!(set instanceof Set)) return out;
  if (fromIndex === toIndex) {
    for (const idx of set) out.add(idx);
    return out;
  }
  for (const idx of set) {
    let next = idx;
    if (idx === fromIndex) {
      next = toIndex;
    } else if (fromIndex < toIndex) {
      if (idx > fromIndex && idx <= toIndex) next = idx - 1;
    } else {
      if (idx >= toIndex && idx < fromIndex) next = idx + 1;
    }
    out.add(next);
  }
  return out;
}

/** Serialize a local period object for the update_room_schedule service. */
export function serializeSchedulePeriod(p, defaults) {
  const normalized = normalizePeriodForEditor(p);
  const scheduleType = normalized.schedule_type;
  const out = {
    name: p.name,
    schedule_type: scheduleType,
    mode: p.mode || 'comfort',
    enabled: p.enabled !== false,
  };

  applyActiveWhenFields(out, scheduleType, normalized._whenByType);
  preserveInactiveWhenFields(out, scheduleType, normalized._whenByType);
  applyOverrideFields(out, normalized, defaults || {});
  return out;
}
