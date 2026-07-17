/**
 * Harness: schedule-utils parity with Python schedule_type model.
 *
 * Run: node tests/panel_schedule_type.harness.mjs
 */
import {
  activeOverrideFields,
  hasOverride,
  periodMatchesNow,
  serializeSchedulePeriod,
  normalizePeriodForEditor,
  overrideBaseline,
} from '../custom_components/heating_assistant/www/js/schedule-utils.js';

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const defaults = {
  setpoint: 21.0,
  comfort_offset: 2.0,
  tracking_weight: 1.0,
  energy_weight: 1.0,
  frost_protection: 12.0,
};

// weekly_recurring window
assert(
  periodMatchesNow({
    schedule_type: 'weekly_recurring',
    time_mode: 'window',
    start: '18:00',
    end: '22:00',
    enabled: true,
  }, new Date('2026-01-05T20:00:00')) === true,
  'weekly window should match inside period',
);
assert(
  periodMatchesNow({
    schedule_type: 'weekly_recurring',
    time_mode: 'window',
    start: '18:00',
    end: '22:00',
    enabled: true,
  }, new Date('2026-01-05T22:00:00')) === false,
  'weekly window should be half-open at end',
);

// continuous span
assert(
  periodMatchesNow({
    schedule_type: 'continuous_span',
    start_at: '2026-07-20T10:00:00',
    end_at: '2026-07-20T14:00:00',
    enabled: true,
  }, new Date('2026-07-20T13:59:00')) === true,
  'continuous span should match before end',
);
assert(
  periodMatchesNow({
    schedule_type: 'continuous_span',
    start_at: '2026-07-20T10:00:00',
    end_at: '2026-07-20T14:00:00',
    enabled: true,
  }, new Date('2026-07-20T14:00:00')) === false,
  'continuous span should exclude end',
);

const serialized = serializeSchedulePeriod({
  name: 'Morning',
  recurring: true,
  all_day: false,
  start: '06:00',
  end: '09:00',
  days: [0, 1, 2, 3, 4],
  mode: 'comfort',
  setpoint: 21.0,
}, defaults);
assert(serialized.schedule_type === 'weekly_recurring', 'serialize should emit schedule_type');
assert(serialized.time_mode === 'window', 'serialize should emit time_mode');
assert(!('recurring' in serialized), 'serialize should not emit recurring');
assert(!('all_day' in serialized), 'serialize should not emit all_day');
assert(!('setpoint' in serialized), 'serialize should omit inherited setpoint');

const editor = normalizePeriodForEditor({
  schedule_type: 'date_range_daily',
  time_mode: 'all_day',
  start_date: '2026-07-20',
  end_date: '2026-07-27',
  mode: 'off',
});
assert(editor.recurring === false, 'date_range_daily maps to non-recurring editor state');
assert(editor.all_day === true, 'all_day time_mode maps to editor all_day');
assert(editor._whenByType.date_range_daily.start_date === '2026-07-20', 'editor stores date-range dates');

const periodWithAllTypes = normalizePeriodForEditor({
  name: 'Switchable',
  schedule_type: 'weekly_recurring',
  time_mode: 'window',
  start: '08:00',
  end: '22:00',
  days: [0, 1, 2],
  mode: 'comfort',
});
periodWithAllTypes._whenByType.date_range_daily = {
  time_mode: 'window',
  start: '10:00',
  end: '12:00',
  start_date: '2026-07-20',
  end_date: '2026-07-21',
};
periodWithAllTypes._whenByType.continuous_span = {
  start_at: '2026-07-22T09:30',
  end_at: '2026-07-22T11:00',
};
periodWithAllTypes.schedule_type = 'date_range_daily';
const dateSerialized = serializeSchedulePeriod(periodWithAllTypes, defaults);
assert(dateSerialized.schedule_type === 'date_range_daily', 'type switch should serialize active date range');
assert(dateSerialized.start === '10:00', 'date range should keep its own start time');
assert(dateSerialized.start_date === '2026-07-20', 'date range should keep its own start date');
assert(dateSerialized.start_at === '2026-07-22T09:30:00', 'inactive continuous start should be retained');
periodWithAllTypes.schedule_type = 'weekly_recurring';
const weeklyAgain = serializeSchedulePeriod(periodWithAllTypes, defaults);
assert(weeklyAgain.start === '08:00', 'switching back should restore weekly start');
assert(JSON.stringify(weeklyAgain.days) === JSON.stringify([0, 1, 2]), 'switching back should restore weekly days');

const continuousSerialized = serializeSchedulePeriod({
  name: 'Maintenance',
  schedule_type: 'continuous_span',
  mode: 'off',
  enabled: true,
  start_at: '2026-08-01T00:00',
  end_at: '2026-08-03T12:30',
}, defaults);
assert(continuousSerialized.start_at === '2026-08-01T00:00:00', 'continuous span serializes seconds');
assert(continuousSerialized.end_at === '2026-08-03T12:30:00', 'continuous span end serializes seconds');
assert(!('frost_protection' in continuousSerialized), 'off inherit frost should be omitted');

const overrideSerialized = serializeSchedulePeriod({
  name: 'Off with remembered comfort',
  schedule_type: 'weekly_recurring',
  time_mode: 'window',
  start: '00:00',
  end: '06:00',
  days: [0, 1, 2, 3, 4, 5, 6],
  mode: 'off',
  frost_protection: 12.0,
  setpoint: 20.5,
  tracking_weight: 1.0,
  energy_weight: 0.8,
}, defaults);
assert(!('frost_protection' in overrideSerialized), 'frost baseline should be omitted');
assert(overrideSerialized.setpoint === 20.5, 'inactive comfort override should be retained');
assert(!('tracking_weight' in overrideSerialized), 'tracking baseline 1.0 should be omitted');
assert(overrideSerialized.energy_weight === 0.8, 'inactive differing energy override should be retained');
assert(overrideBaseline('tracking_weight', { tracking_weight: 0 }) === 1.0, 'tracking baseline ignores global config');
assert(activeOverrideFields('off').join(',') === 'frost_protection', 'off picker should only expose frost');
assert(!hasOverride({}, 'setpoint'), 'missing override means inherit');

console.log('panel_schedule_type.harness.mjs: all assertions passed');
