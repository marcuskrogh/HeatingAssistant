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

const DAY_NAMES_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function resolveScheduleType(p) {
  return p.schedule_type
    || (p.recurring === false ? SCHEDULE_TYPE_DATE_RANGE : SCHEDULE_TYPE_WEEKLY);
}

function resolveTimeMode(p) {
  return p.time_mode || (p.all_day ? 'all_day' : 'window');
}

function startOfLocalDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function addLocalDays(d, n) {
  const out = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  out.setDate(out.getDate() + n);
  return out;
}

function atLocalDateAndTime(dateStr, hhmm) {
  const [y, m, day] = String(dateStr).split('-').map(Number);
  const [h, mi] = String(hhmm || '00:00').split(':').map(Number);
  return new Date(y, m - 1, day, h || 0, mi || 0, 0);
}

function parseLocalDateTime(value) {
  if (!value) return null;
  const text = String(value);
  const m = text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
}

function formatShortDate(dateStr) {
  if (!dateStr) return '—';
  const [y, m, d] = String(dateStr).split('-').map(Number);
  if (!y || !m || !d) return dateStr;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function formatShortDateTime(value) {
  const dt = parseLocalDateTime(value);
  if (!dt) return value || '—';
  const datePart = dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const timePart = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`;
  return `${datePart} ${timePart}`;
}

function formatDateRangeLabel(startDate, endDate) {
  if (!startDate || !endDate) return '—';
  if (startDate === endDate) return formatShortDate(startDate);
  return `${formatShortDate(startDate)} → ${formatShortDate(endDate)}`;
}

/** Compact weekday label list (Mon–Sun, Mon=0). */
export function formatWeekdayLabels(days) {
  const sorted = [...(days && days.length ? days : DEFAULT_DAYS)]
    .map(Number)
    .filter((d) => d >= 0 && d <= 6)
    .sort((a, b) => a - b);
  if (sorted.length === 0) return '';
  if (sorted.length === 7) return 'Mon–Sun';

  const ranges = [];
  let start = sorted[0];
  let prev = sorted[0];
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] === prev + 1) {
      prev = sorted[i];
      continue;
    }
    ranges.push(start === prev
      ? DAY_NAMES_SHORT[start]
      : `${DAY_NAMES_SHORT[start]}–${DAY_NAMES_SHORT[prev]}`);
    start = sorted[i];
    prev = sorted[i];
  }
  ranges.push(start === prev
    ? DAY_NAMES_SHORT[start]
    : `${DAY_NAMES_SHORT[start]}–${DAY_NAMES_SHORT[prev]}`);
  return ranges.join(', ');
}

/** Short type label for previews: Weekly / Date range / Continuous. */
export function scheduleTypeLabel(p) {
  const scheduleType = resolveScheduleType(p);
  if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) return 'Date range';
  if (scheduleType === SCHEDULE_TYPE_CONTINUOUS) return 'Continuous';
  return 'Weekly';
}

/**
 * Timing-only preview string per schedule type.
 * All-day weekly/date-range uses lowercase "all day" (no clock times).
 */
export function formatPeriodTiming(p) {
  const scheduleType = resolveScheduleType(p);
  const timeMode = resolveTimeMode(p);

  if (scheduleType === SCHEDULE_TYPE_CONTINUOUS) {
    if (p.start_at && p.end_at) {
      return `${formatShortDateTime(p.start_at)} → ${formatShortDateTime(p.end_at)}`;
    }
    return 'Continuous span';
  }

  if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {
    const dates = formatDateRangeLabel(p.start_date, p.end_date);
    if (timeMode === 'all_day') return `${dates} · all day`;
    const window = `${p.start || '—'}–${p.end || '—'}`;
    return `${dates} · ${window}`;
  }

  // weekly_recurring
  if (timeMode === 'all_day') {
    const days = p.days || DEFAULT_DAYS;
    if (!days || days.length === 7) return 'all day';
    return `${formatWeekdayLabels(days)} · all day`;
  }
  const daysLabel = formatWeekdayLabels(p.days || DEFAULT_DAYS);
  const window = `${p.start || '—'}–${p.end || '—'}`;
  return daysLabel ? `${daysLabel} ${window}` : window;
}

/**
 * Structured preview parts: type + name + timing + mode (no overrides).
 * @returns {{ type: string, name: string, timing: string, mode: string, modeCls: string }}
 */
export function formatPeriodPreview(p) {
  const mode = periodModeDisplay(p);
  return {
    type: scheduleTypeLabel(p),
    name: p.name || 'Period',
    timing: formatPeriodTiming(p),
    mode: mode.text,
    modeCls: mode.cls,
  };
}

/** Escape text for HTML element bodies / attributes. */
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** HTML snippets for preview parts (user-controlled fields escaped). */
export function formatPeriodPreviewHtml(p) {
  const parts = formatPeriodPreview(p);
  return {
    typeHtml: `<span class="sched-row__type">${escapeHtml(parts.type)}</span>`,
    nameHtml: `<span class="sched-row__name">${escapeHtml(parts.name)}</span>`,
    timingHtml: `<span class="sched-row__time">${escapeHtml(parts.timing)}</span>`,
    modeHtml: `<span class="sched-row__mode ${escapeHtml(parts.modeCls)}">${escapeHtml(parts.mode)}</span>`,
    parts,
  };
}

/**
 * Inactive = disabled OR fully past.
 * Continuous: end_at <= now (or missing end_at); date_range: end_date < today; weekly: never past by date.
 */
export function isPeriodInactive(p, now = new Date()) {
  if (p.enabled === false) return true;
  const scheduleType = resolveScheduleType(p);
  if (scheduleType === SCHEDULE_TYPE_CONTINUOUS) {
    if (!p.end_at) return true;
    const end = parseLocalDateTime(p.end_at);
    if (!end) return true;
    return now.getTime() >= end.getTime();
  }
  if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {
    if (!p.end_date) return true;
    return localDateString(now) > p.end_date;
  }
  return false;
}

/** Split periods into active vs inactive, preserving relative order in each bucket. */
export function partitionPeriods(periods, now = new Date()) {
  const active = [];
  const inactive = [];
  if (!periods) return { active, inactive };
  for (const p of periods) {
    if (isPeriodInactive(p, now)) inactive.push(p);
    else active.push(p);
  }
  return { active, inactive };
}

/** Next future start datetime for an active, non-matching period; or null. */
function nextStartForPeriod(p, now) {
  const scheduleType = resolveScheduleType(p);
  const timeMode = resolveTimeMode(p);

  if (scheduleType === SCHEDULE_TYPE_CONTINUOUS) {
    const start = parseLocalDateTime(p.start_at);
    if (start && start.getTime() > now.getTime()) return start;
    return null;
  }

  if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {
    if (!p.start_date || !p.end_date) return null;
    const todayStr = localDateString(now);

    if (timeMode === 'all_day') {
      // Next calendar day in range at 00:00 when not currently matching.
      let cursor = todayStr < p.start_date ? p.start_date : todayStr;
      if (cursor === todayStr && todayStr >= p.start_date && todayStr <= p.end_date) {
        // Today is in range but not matching — should not happen for all_day;
        // advance to tomorrow.
        cursor = localDateString(addLocalDays(now, 1));
      }
      if (cursor < p.start_date) cursor = p.start_date;
      if (cursor > p.end_date) return null;
      return atLocalDateAndTime(cursor, '00:00');
    }

    // Daily window within remaining date range.
    let cursor = todayStr < p.start_date ? p.start_date : todayStr;
    while (cursor <= p.end_date) {
      const startDt = atLocalDateAndTime(cursor, p.start || '00:00');
      if (startDt.getTime() > now.getTime()) return startDt;
      cursor = localDateString(addLocalDays(atLocalDateAndTime(cursor, '00:00'), 1));
    }
    return null;
  }

  // weekly_recurring
  const days = p.days || DEFAULT_DAYS;
  if (timeMode === 'all_day') {
    for (let offset = 0; offset < 7; offset++) {
      const candidate = addLocalDays(startOfLocalDay(now), offset);
      const day = (candidate.getDay() + 6) % 7;
      if (!days.includes(day)) continue;
      if (offset === 0) continue; // today would already be NOW if matching
      return candidate;
    }
    return null;
  }

  for (let offset = 0; offset < 8; offset++) {
    const candidateDay = addLocalDays(startOfLocalDay(now), offset);
    const day = (candidateDay.getDay() + 6) % 7;
    if (!days.includes(day)) continue;
    const startDt = atLocalDateAndTime(localDateString(candidateDay), p.start || '00:00');
    if (startDt.getTime() > now.getTime()) return startDt;
  }
  return null;
}

/** Returns the period active right now among non-inactive periods, or null. */
export function findActivePeriod(periods, now = new Date()) {
  if (!periods || !periods.length) return null;
  for (const p of periods) {
    if (isPeriodInactive(p, now)) continue;
    if (periodMatchesNow(p, now)) return p;
  }
  return null;
}

/**
 * Soonest upcoming start among active (non-inactive) periods across all types.
 * Skips periods that currently match (those are NOW, not NEXT).
 */
export function findNextPeriod(periods, now = new Date()) {
  if (!periods || !periods.length) return null;
  let best = null;
  let bestTime = null;
  for (const p of periods) {
    if (isPeriodInactive(p, now)) continue;
    if (periodMatchesNow(p, now)) continue;
    const start = nextStartForPeriod(p, now);
    if (!start) continue;
    if (!bestTime || start.getTime() < bestTime.getTime()) {
      bestTime = start;
      best = p;
    }
  }
  return best;
}

/** Human-readable timing for a period row (delegates to formatPeriodTiming). */
export function formatPeriodTime(p) {
  return formatPeriodTiming(p);
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
  const preview = formatPeriodPreviewHtml(p);
  return `<div class="sched-row${isActive ? ' sched-row--active' : ''}${isNext ? ' sched-row--next' : ''}${disabled ? ' sched-row--disabled' : ''}">
    ${isActive ? '<span class="sched-row__now-badge">NOW</span>' : ''}
    ${isNext ? '<span class="sched-index-card__next-label">NEXT</span>' : ''}
    ${toggle}
    ${preview.typeHtml}
    ${preview.nameHtml}
    ${preview.timingHtml}
    ${preview.modeHtml}
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
