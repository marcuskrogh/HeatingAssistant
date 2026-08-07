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
  'heatingassistant/app/static/js/schedules/schedules-shared.js',
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
  /export function getPanelScheduleSnapshot/.test(source),
  'schedules-shared.js must export getPanelScheduleSnapshot',
);
assert(
  /export function getRoomComfortOffset/.test(source),
  'schedules-shared.js must export getRoomComfortOffset',
);
assert(
  /export function patchStateComfortOffset/.test(source),
  'schedules-shared.js must export patchStateComfortOffset',
);
assert(
  /export function mergeRoomSchedulesWithState/.test(source),
  'schedules-shared.js must export mergeRoomSchedulesWithState',
);

const indexSource = readFileSync(
  join(ROOT, 'heatingassistant/app/static/js/schedules/schedules-index.js'),
  'utf8',
);
assert(
  /resolveRoomScheduleData/.test(indexSource),
  'schedules-index.js must use resolveRoomScheduleData',
);
assert(
  /update\(newState\)[\s\S]*state\s*=\s*newState/.test(indexSource),
  'schedules-index.js update() must refresh state for mergeRoomSchedulesWithState fallback',
);

const mod = await import(`${pathToFileURL(SHARED).href}?v=104`);
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

const staleWs = {
  living_room: {
    enabled: true,
    periods: [{ name: 'Old', start: '01:00', end: '02:00', mode: 'comfort', days: [0] }],
  },
};
const freshState = {
  'sensor.heating_assistant_controller_config': {
    attributes: {
      room_schedules: {
        living_room: { enabled: true, periods },
      },
    },
  },
};
const mergedStale = mod.mergeRoomSchedulesWithState(staleWs, freshState);
assert(
  mergedStale.living_room?.periods?.[0]?.name === 'Morning',
  'merge must prefer config-entity when WS is stale but non-empty',
);
const resolvedStale = mod.resolveRoomScheduleData(room, staleWs, freshState);
assert(
  resolvedStale?.periods?.[0]?.name === 'Morning',
  'resolver must prefer config-entity when WS is stale but non-empty',
);

const panelState = { __scheduleSnapshots: { living_room: periods } };
const fromPanel = mod.resolveRoomScheduleData(room, {}, panelState);
assert(
  fromPanel?.periods?.length === 1,
  'panel schedule snapshot must survive navigation when WS is empty',
);

const comfortState = {};
mod.patchStateComfortOffset(comfortState, 'living_room', 3.5);
assert(
  comfortState['sensor.heating_assistant_controller_config']?.attributes
    ?.room_comfort_offsets?.living_room === 3.5,
  'patchStateComfortOffset must update config-entity attrs in panel state',
);
assert(
  mod.getRoomComfortOffset(comfortState, room) === 3.5,
  'getRoomComfortOffset must read patched comfort offset',
);

console.log('panel schedule resolver harness: ok');
