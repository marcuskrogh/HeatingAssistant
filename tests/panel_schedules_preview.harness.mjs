/**
 * Harness: period preview, NOW/NEXT across types, inactive bucketing (SWD-45).
 *
 * Run: node tests/panel_schedules_preview.harness.mjs
 */
import {
  findActivePeriod,
  findNextPeriod,
  formatPeriodPreview,
  formatPeriodPreviewHtml,
  formatPeriodTiming,
  isPeriodInactive,
  partitionPeriods,
  scheduleTypeLabel,
} from '../heatingassistant/app/static/js/schedule-utils.js';

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ── Preview strings / parts per type ────────────────────────────────────────

const weeklyWindow = {
  name: 'Evening',
  schedule_type: 'weekly_recurring',
  time_mode: 'window',
  start: '18:00',
  end: '22:00',
  days: [0, 1, 2, 3, 4],
  mode: 'comfort',
  enabled: true,
};
assertEqual(scheduleTypeLabel(weeklyWindow), 'Weekly', 'weekly type label');
assertEqual(formatPeriodTiming(weeklyWindow), 'Mon–Fri 18:00–22:00', 'weekly window timing');
{
  const preview = formatPeriodPreview(weeklyWindow);
  assertEqual(preview.type, 'Weekly', 'weekly preview type');
  assertEqual(preview.name, 'Evening', 'weekly preview name');
  assertEqual(preview.timing, 'Mon–Fri 18:00–22:00', 'weekly preview timing');
  assertEqual(preview.mode, 'COMFORT', 'weekly preview mode (no overrides)');
}

const weeklyAllDay = {
  name: 'Weekend',
  schedule_type: 'weekly_recurring',
  time_mode: 'all_day',
  days: [5, 6],
  mode: 'off',
  enabled: true,
};
assertEqual(formatPeriodTiming(weeklyAllDay), 'Sat–Sun · all day', 'weekly all-day timing');
assertEqual(formatPeriodPreview(weeklyAllDay).mode, 'OFF', 'weekly all-day mode');

const weeklyAllDayEveryDay = {
  name: 'Always',
  schedule_type: 'weekly_recurring',
  time_mode: 'all_day',
  days: [0, 1, 2, 3, 4, 5, 6],
  mode: 'comfort',
  enabled: true,
};
assertEqual(formatPeriodTiming(weeklyAllDayEveryDay), 'all day', 'weekly all-day all days');

const dateRangeWindow = {
  name: 'Holiday',
  schedule_type: 'date_range_daily',
  time_mode: 'window',
  start_date: '2026-07-20',
  end_date: '2026-07-27',
  start: '09:00',
  end: '17:00',
  mode: 'comfort',
  setpoint: 22,
  enabled: true,
};
assertEqual(scheduleTypeLabel(dateRangeWindow), 'Date range', 'date range type label');
{
  const timing = formatPeriodTiming(dateRangeWindow);
  assert(timing.includes('·'), 'date range timing separates dates and window');
  assert(timing.includes('09:00–17:00'), 'date range includes daily window');
  assert(!timing.toLowerCase().includes('all day'), 'date range window is not all day');
  const preview = formatPeriodPreview(dateRangeWindow);
  assertEqual(preview.mode, '22°C', 'setpoint shows as mode, not as override list');
  assert(!('setpoint' in preview), 'preview has no override fields');
}

const dateRangeAllDay = {
  name: 'Vacation',
  schedule_type: 'date_range_daily',
  time_mode: 'all_day',
  start_date: '2026-08-01',
  end_date: '2026-08-14',
  mode: 'off',
  enabled: true,
};
{
  const timing = formatPeriodTiming(dateRangeAllDay);
  assert(timing.includes('all day'), 'date range all-day shows all day');
  assert(!timing.includes(':'), 'date range all-day has no clock times');
}

const continuous = {
  name: 'Party',
  schedule_type: 'continuous_span',
  start_at: '2026-07-20T18:00:00',
  end_at: '2026-07-20T23:30:00',
  mode: 'comfort',
  enabled: true,
};
assertEqual(scheduleTypeLabel(continuous), 'Continuous', 'continuous type label');
{
  const timing = formatPeriodTiming(continuous);
  assert(timing.includes('→'), 'continuous timing has arrow');
  assert(!timing.toLowerCase().includes('all day'), 'continuous is not all day');
}

// ── Inactive bucketing ──────────────────────────────────────────────────────

const now = new Date('2026-07-17T12:00:00');

const pastContinuous = {
  name: 'Past party',
  schedule_type: 'continuous_span',
  start_at: '2026-07-10T10:00:00',
  end_at: '2026-07-10T14:00:00',
  mode: 'comfort',
  enabled: true,
};
assert(isPeriodInactive(pastContinuous, now), 'past continuous is inactive');

const pastDateRange = {
  name: 'Old vacation',
  schedule_type: 'date_range_daily',
  time_mode: 'all_day',
  start_date: '2026-06-01',
  end_date: '2026-06-15',
  mode: 'off',
  enabled: true,
};
assert(isPeriodInactive(pastDateRange, now), 'past date range is inactive');

const disabledWeekly = {
  name: 'Disabled evening',
  schedule_type: 'weekly_recurring',
  time_mode: 'window',
  start: '18:00',
  end: '22:00',
  days: [0, 1, 2, 3, 4],
  mode: 'comfort',
  enabled: false,
};
assert(isPeriodInactive(disabledWeekly, now), 'disabled period is inactive');

const upcomingContinuous = {
  name: 'Future party',
  schedule_type: 'continuous_span',
  start_at: '2026-07-20T18:00:00',
  end_at: '2026-07-20T23:00:00',
  mode: 'comfort',
  enabled: true,
};
assert(!isPeriodInactive(upcomingContinuous, now), 'upcoming continuous stays active');

const todayDateRange = {
  name: 'Today range',
  schedule_type: 'date_range_daily',
  time_mode: 'window',
  start_date: '2026-07-17',
  end_date: '2026-07-17',
  start: '08:00',
  end: '20:00',
  mode: 'comfort',
  enabled: true,
};
assert(!isPeriodInactive(todayDateRange, now), 'date range ending today is still active');

{
  const periods = [
    weeklyWindow,
    pastContinuous,
    upcomingContinuous,
    pastDateRange,
    disabledWeekly,
  ];
  const { active, inactive } = partitionPeriods(periods, now);
  assertEqual(active.length, 2, 'two active periods');
  assertEqual(active[0].name, 'Evening', 'active order preserved');
  assertEqual(active[1].name, 'Future party', 'upcoming continuous in active');
  assertEqual(inactive.length, 3, 'three inactive periods');
  assertEqual(inactive[0].name, 'Past party', 'inactive order preserved');
  assertEqual(inactive[1].name, 'Old vacation', 'past date range inactive');
  assertEqual(inactive[2].name, 'Disabled evening', 'disabled inactive');
}

// ── NOW / NEXT across types ─────────────────────────────────────────────────

// Friday 2026-07-17 is weekday index 4 (Mon=0).
const fridayNoon = new Date('2026-07-17T12:00:00');

const eveningWeekly = {
  name: 'Evening',
  schedule_type: 'weekly_recurring',
  time_mode: 'window',
  start: '18:00',
  end: '22:00',
  days: [0, 1, 2, 3, 4],
  mode: 'comfort',
  enabled: true,
};

const continuousLater = {
  name: 'Later continuous',
  schedule_type: 'continuous_span',
  start_at: '2026-07-17T20:00:00',
  end_at: '2026-07-17T22:00:00',
  mode: 'comfort',
  enabled: true,
};

const dateRangeTomorrow = {
  name: 'Tomorrow range',
  schedule_type: 'date_range_daily',
  time_mode: 'window',
  start_date: '2026-07-18',
  end_date: '2026-07-19',
  start: '09:00',
  end: '17:00',
  mode: 'comfort',
  enabled: true,
};

{
  const periods = [eveningWeekly, continuousLater, dateRangeTomorrow, pastContinuous];
  assertEqual(findActivePeriod(periods, fridayNoon), null, 'nothing matching at noon');
  const next = findNextPeriod(periods, fridayNoon);
  assert(next, 'should find a next period');
  assertEqual(next.name, 'Evening', 'NEXT is soonest start (weekly 18:00 before continuous 20:00)');
}

{
  const duringEvening = new Date('2026-07-17T19:00:00');
  const periods = [eveningWeekly, continuousLater];
  assertEqual(findActivePeriod(periods, duringEvening)?.name, 'Evening', 'NOW is weekly evening');
  assertEqual(findNextPeriod(periods, duringEvening)?.name, 'Later continuous', 'NEXT is continuous after NOW');
}

{
  const duringContinuous = new Date('2026-07-17T20:30:00');
  const periods = [eveningWeekly, continuousLater];
  // Evening ends at 22:00 so still matches; first matching wins.
  assertEqual(findActivePeriod(periods, duringContinuous)?.name, 'Evening', 'NOW prefers first match');
}

{
  const mondayMorning = new Date('2026-07-20T08:00:00'); // Mon
  const weekendAllDay = {
    name: 'Weekend',
    schedule_type: 'weekly_recurring',
    time_mode: 'all_day',
    days: [5, 6],
    mode: 'off',
    enabled: true,
  };
  const next = findNextPeriod([weekendAllDay], mondayMorning);
  assertEqual(next?.name, 'Weekend', 'NEXT finds weekly all-day on upcoming Sat');
}

{
  const beforeRange = new Date('2026-07-17T12:00:00');
  const next = findNextPeriod([dateRangeTomorrow], beforeRange);
  assertEqual(next?.name, 'Tomorrow range', 'NEXT finds date_range window start');
}

{
  const beforeAllDayRange = new Date('2026-07-31T12:00:00');
  const next = findNextPeriod([dateRangeAllDay], beforeAllDayRange);
  assertEqual(next?.name, 'Vacation', 'NEXT finds date_range all-day at range start');
}

{
  // Inactive periods must not win NOW/NEXT
  const periods = [pastContinuous, disabledWeekly, upcomingContinuous];
  assertEqual(findActivePeriod(periods, now), null, 'no NOW among inactive+upcoming');
  assertEqual(findNextPeriod(periods, now)?.name, 'Future party', 'NEXT ignores inactive');
}

// ── HTML escaping ───────────────────────────────────────────────────────────

{
  const evil = {
    name: '<img src=x onerror=alert(1)>',
    schedule_type: 'weekly_recurring',
    time_mode: 'window',
    start: '08:00',
    end: '09:00',
    days: [0],
    mode: 'comfort',
    enabled: true,
  };
  const html = formatPeriodPreviewHtml(evil);
  assert(!html.nameHtml.includes('<img'), 'preview HTML must escape name tags');
  assert(html.nameHtml.includes('&lt;img'), 'preview HTML escapes < as entity');
}

// ── Overnight / wrap NEXT ───────────────────────────────────────────────────

{
  const overnight = {
    name: 'Overnight',
    schedule_type: 'weekly_recurring',
    time_mode: 'window',
    start: '22:00',
    end: '06:00',
    days: [4], // Friday
    mode: 'comfort',
    enabled: true,
  };
  const fri2300 = new Date('2026-07-17T23:00:00');
  assertEqual(findActivePeriod([overnight], fri2300)?.name, 'Overnight', 'overnight NOW on Friday night');
  assertEqual(findNextPeriod([overnight], fri2300), null, 'overnight has no NEXT while matching');

  const sat0300 = new Date('2026-07-18T03:00:00');
  // Saturday is day 5 — overnight only lists Friday, so second half may not match depending on matcher.
  // After end Saturday morning: next Friday 22:00.
  const sat0700 = new Date('2026-07-18T07:00:00');
  const morning = {
    name: 'Morning',
    schedule_type: 'weekly_recurring',
    time_mode: 'window',
    start: '07:00',
    end: '09:00',
    days: [5], // Saturday
    mode: 'comfort',
    enabled: true,
  };
  assertEqual(
    findNextPeriod([overnight, morning], fri2300)?.name,
    'Morning',
    'while overnight NOW, NEXT is Saturday morning',
  );
  assertEqual(findActivePeriod([overnight, morning], sat0700)?.name, 'Morning', 'Morning is NOW at 07:00');
  assertEqual(
    findNextPeriod([overnight, morning], sat0700)?.name,
    'Overnight',
    'after Morning starts, NEXT is next Friday overnight start',
  );
}

{
  const wrapRange = {
    name: 'Wrap range',
    schedule_type: 'date_range_daily',
    time_mode: 'window',
    start_date: '2026-07-17',
    end_date: '2026-07-18',
    start: '22:00',
    end: '06:00',
    mode: 'comfort',
    enabled: true,
  };
  const fri2300 = new Date('2026-07-17T23:00:00');
  assertEqual(findActivePeriod([wrapRange], fri2300)?.name, 'Wrap range', 'date-range overnight NOW');
  const sat0700 = new Date('2026-07-18T07:00:00');
  assertEqual(
    findNextPeriod([wrapRange], sat0700)?.name,
    'Wrap range',
    'after overnight half, NEXT is same-day 22:00 window start',
  );
  const sat2300 = new Date('2026-07-18T23:00:00');
  assertEqual(findActivePeriod([wrapRange], sat2300)?.name, 'Wrap range', 'wrap NOW on last evening');
  const sun0100 = new Date('2026-07-19T01:00:00');
  assertEqual(isPeriodInactive(wrapRange, sun0100), true, 'date-range past after end_date');
  assertEqual(findNextPeriod([wrapRange], sun0100), null, 'no NEXT once date-range is inactive');
}

{
  // Fractional seconds on end_at still mark continuous inactive at/after end
  const msEnd = {
    name: 'Ms end',
    schedule_type: 'continuous_span',
    start_at: '2026-07-01T10:00:00.000',
    end_at: '2026-07-10T12:00:00.000',
    mode: 'comfort',
    enabled: true,
  };
  assertEqual(isPeriodInactive(msEnd, new Date('2026-07-10T12:00:00')), true, 'ms end_at inactive at exact end');
  assertEqual(isPeriodInactive({ schedule_type: 'continuous_span', enabled: true }, now), true, 'missing end_at is inactive');
}

console.log('panel_schedules_preview.harness.mjs: all assertions passed');
