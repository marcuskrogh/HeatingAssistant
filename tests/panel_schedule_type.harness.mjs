/**
 * Harness: schedule-utils parity with Python schedule_type model.
 *
 * Run: node tests/panel_schedule_type.harness.mjs
 */
import {
  periodMatchesNow,
  serializeSchedulePeriod,
  normalizePeriodForEditor,
} from '../custom_components/heating_assistant/www/js/schedule-utils.js';

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const defaults = { setpoint: 21.0 };

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

const editor = normalizePeriodForEditor({
  schedule_type: 'date_range_daily',
  time_mode: 'all_day',
  start_date: '2026-07-20',
  end_date: '2026-07-27',
  mode: 'off',
});
assert(editor.recurring === false, 'date_range_daily maps to non-recurring editor state');
assert(editor.all_day === true, 'all_day time_mode maps to editor all_day');

console.log('panel_schedule_type.harness.mjs: all assertions passed');
