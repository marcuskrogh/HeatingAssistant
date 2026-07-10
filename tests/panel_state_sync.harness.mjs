/**
 * Regression harness: panel must detect controller-config attribute changes even
 * when HA reuses the same state object reference.
 *
 * Run: node tests/panel_state_sync.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DASHBOARD = join(ROOT, 'custom_components/heating_assistant/www/industrial-dashboard.js');

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const src = readFileSync(DASHBOARD, 'utf8');
assert(/function controllerConfigAttrsChanged/.test(src), 'dashboard must compare controller-config attrs');
assert(/function snapshotConfigAttrs/.test(src), 'dashboard must snapshot controller-config attrs');
assert(/function sensorStateChanged/.test(src), 'dashboard must define sensorStateChanged');
assert(
  /sensorStateChanged\(this\._state\[id\], state, snapshot\)/.test(src),
  'dashboard must use sensorStateChanged with config snapshot in hass state sync',
);

// Evaluate the helpers in isolation (mirrors industrial-dashboard.js).
const helperBlock = src.slice(
  src.indexOf('const CONTROLLER_CONFIG_ENTITY'),
  src.indexOf('class HaIndustrialPanel'),
);
const {
  controllerConfigAttrsChanged,
  snapshotConfigAttrs,
  sensorStateChanged,
} = new Function(`${helperBlock}\nreturn { controllerConfigAttrsChanged, snapshotConfigAttrs, sensorStateChanged };`)();

const entityId = 'sensor.heating_assistant_controller_config';

const shared = {
  entity_id: entityId,
  state: 'ok',
  last_updated: '2026-07-10T12:00:00.000Z',
  attributes: {
    room_active: { living_room: true },
    room_enabled: { living_room: true },
  },
};

const snapshot = snapshotConfigAttrs(shared.attributes);
assert(
  sensorStateChanged(shared, shared, snapshot) === false,
  'unchanged in-place state must not register as changed',
);

// HA may mutate attributes on the same object without changing last_updated.
shared.attributes = {
  room_active: { living_room: false },
  room_enabled: { living_room: false },
};
assert(
  sensorStateChanged(shared, shared, snapshot),
  'in-place room_active/room_enabled mutation must register as changed',
);

const before = {
  entity_id: entityId,
  state: 'ok',
  last_updated: '2026-07-10T12:00:00.000Z',
  attributes: {
    room_active: { living_room: true },
    room_enabled: { living_room: true },
  },
};
const after = {
  entity_id: entityId,
  state: 'ok',
  last_updated: '2026-07-10T12:00:01.000Z',
  attributes: {
    room_active: { living_room: false },
    room_enabled: { living_room: false },
  },
};
assert(
  sensorStateChanged(before, after, snapshotConfigAttrs(before.attributes)),
  'new state object with room off must register as changed',
);
assert(
  controllerConfigAttrsChanged(before.attributes, after.attributes),
  'controllerConfigAttrsChanged must detect room off transition',
);

console.log('panel state sync harness: ok');
