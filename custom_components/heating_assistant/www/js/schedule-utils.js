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
    || (p.recurring === false ? 'date_range_daily' : 'weekly_recurring');

  if (scheduleType === 'continuous_span') {
    return matchesContinuousSpan(p, now);
  }
  if (scheduleType === 'date_range_daily') {
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
      || (p.recurring === false ? 'date_range_daily' : 'weekly_recurring');
    if (scheduleType !== 'weekly_recurring') continue;
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
    || (p.recurring === false ? 'date_range_daily' : 'weekly_recurring');
  const timeMode = p.time_mode || (p.all_day ? 'all_day' : 'window');

  if (scheduleType === 'continuous_span') {
    if (p.start_at && p.end_at) return `${p.start_at} → ${p.end_at}`;
    return 'Continuous span';
  }
  if (timeMode === 'all_day') return 'All day';
  if (scheduleType === 'date_range_daily' && p.start_date && p.end_date) {
    const timePart = `${p.start || '—'}–${p.end || '—'}`;
    if (p.start_date === p.end_date) return `${p.start_date} ${timePart}`;
    return `${p.start_date} → ${p.end_date} ${timePart}`;
  }
  return `${p.start || '—'}–${p.end || '—'}`;
}

/** Map persisted schedule_type payloads to legacy editor toggles until SWD-23. */
export function normalizePeriodForEditor(p) {
  const out = {
    ...p,
    mode: p.mode || 'comfort',
    enabled: p.enabled !== false,
    days: p.days ? [...p.days] : [0, 1, 2, 3, 4, 5, 6],
    start: p.start || '08:00',
    end: p.end || '22:00',
  };

  if (p.schedule_type === 'continuous_span') {
    out.recurring = false;
    out.all_day = false;
    return out;
  }

  if (p.schedule_type === 'date_range_daily') {
    out.recurring = false;
    out.all_day = p.time_mode === 'all_day';
    return out;
  }

  if (p.schedule_type === 'weekly_recurring') {
    out.recurring = true;
    out.all_day = p.time_mode === 'all_day';
    return out;
  }

  out.recurring = p.recurring !== false;
  out.all_day = !!p.all_day;
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

/** Serialize a local period object for the update_room_schedule service. */
export function serializeSchedulePeriod(p, defaults) {
  if (p.schedule_type === 'continuous_span') {
    const out = {
      name: p.name,
      schedule_type: 'continuous_span',
      mode: p.mode,
      enabled: p.enabled !== false,
      start_at: p.start_at,
      end_at: p.end_at,
    };
    if (p.mode === 'off') {
      out.frost_protection = p.frost_protection ?? 12;
    } else {
      out.setpoint = p.setpoint ?? defaults.setpoint;
      if (p.comfort_offset != null) out.comfort_offset = p.comfort_offset;
      if (p.tracking_weight != null) out.tracking_weight = p.tracking_weight;
      if (p.energy_weight != null) out.energy_weight = p.energy_weight;
    }
    return out;
  }

  const recurring = p.recurring !== false;
  const allDay = !!p.all_day;
  const scheduleType = recurring ? 'weekly_recurring' : 'date_range_daily';
  const timeMode = allDay ? 'all_day' : 'window';

  const out = {
    name: p.name,
    schedule_type: scheduleType,
    time_mode: timeMode,
    mode: p.mode,
    enabled: p.enabled !== false,
  };

  if (timeMode === 'window') {
    out.start = p.start;
    out.end = p.end;
  }

  if (scheduleType === 'date_range_daily') {
    out.start_date = p.start_date;
    out.end_date = p.end_date;
  } else if (p.days) {
    out.days = p.days;
  }

  if (p.mode === 'off') {
    out.frost_protection = p.frost_protection ?? 12;
  } else {
    out.setpoint = p.setpoint ?? defaults.setpoint;
    if (p.comfort_offset != null) out.comfort_offset = p.comfort_offset;
    if (p.tracking_weight != null) out.tracking_weight = p.tracking_weight;
    if (p.energy_weight != null) out.energy_weight = p.energy_weight;
  }
  return out;
}
