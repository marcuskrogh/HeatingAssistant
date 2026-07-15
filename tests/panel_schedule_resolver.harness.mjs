/**
 * Regression harness: shared schedule resolver must fall back to config-entity
 * state when WebSocket payloads are empty after a save.
 *
 * Run: node tests/panel_schedule_resolver.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SHARED = join(
  ROOT,
  'custom_components/heating_assistant/www/js/schedules/schedules-shared.js',
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const source = readFileSync(SHARED, 'utf8');
assert(
  /export function resolveRoomScheduleData/.test(source),
  'schedules-shared.js must export resolveRoomScheduleData',
);
assert(
  /export function mergeRoomSchedulesWithState/.test(source),
  'schedules-shared.js must export mergeRoomSchedulesWithState',
);

const indexSource = readFileSync(
  join(ROOT, 'custom_components/heating_assistant/www/js/schedules/schedules-index.js'),
  'utf8',
);
assert(
  /resolveRoomScheduleData/.test(indexSource),
  'schedules-index.js must use resolveRoomScheduleData',
);

const mod = await import(`${pathToFileURL(SHARED).href}?v=94`);
const room = { slug: 'living_room', name: 'Living Room' };
const periods = [{ name: 'Morning', start: '06:00', end: '09:00', mode: 'comfort', days: [0] }];
const state = {
  'sensor.heating_assistant_controller_config': {
    attributes: {
      room_schedules: {
        living_room: { enabled: true, periods },
      },
    },
  },
};

const resolved = mod.resolveRoomScheduleData(room, {}, state);
assert(resolved?.periods?.length === 1, 'resolver must fall back to config-entity state');

const merged = mod.mergeRoomSchedulesWithState({}, state);
assert(
  merged.living_room?.periods?.length === 1,
  'mergeRoomSchedulesWithState must preserve config-entity periods',
);

console.log('panel schedule resolver harness: ok');
