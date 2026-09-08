/**
 * Spec locks for unique config entity labels (device + entity name).
 *
 * Loads the helper functions from production config-ui.js without executing
 * the module graph (query-string imports are HA cache-busts, not Node paths).
 *
 * Run: node tests/panel_entity_display_name.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'heatingassistant/app/static/js/config/config-ui.js');

function assert(cond, msg) {
  if (!cond) { console.error('FAIL:', msg); process.exit(1); }
}

const src = readFileSync(SRC, 'utf8');
const start = src.indexOf('function combineDeviceEntityName');
const end = src.indexOf('/** True when');
assert(start >= 0 && end > start, 'expected unique-name helpers in config-ui.js');
const fnSrc = src.slice(start, end);
const moduleUrl = `data:text/javascript,${encodeURIComponent(
  `${fnSrc}\nexport { combineDeviceEntityName, entityFriendlyName };`
)}`;
const { combineDeviceEntityName, entityFriendlyName } = await import(moduleUrl);

assert(combineDeviceEntityName('Living Room Window', 'TempPV', 'sensor.a')
  === 'Living Room Window TempPV',
  'combined device + entity name');
assert(combineDeviceEntityName('Living Room Window', 'Living Room Window TempPV', 'sensor.a')
  === 'Living Room Window TempPV',
  'do not duplicate device prefix already in the entity name');
assert(combineDeviceEntityName('', 'TempPV', 'sensor.a') === 'TempPV',
  'entity name only when no device');
assert(combineDeviceEntityName('', '', 'sensor.a') === 'sensor.a',
  'entity id fallback');

const living = 'sensor.living_temppv';
const kitchen = 'sensor.kitchen_temppv';
const hass = {
  states: {
    [living]: { attributes: { friendly_name: 'TempPV' } },
    [kitchen]: { attributes: { friendly_name: 'TempPV' } },
  },
  entities: {
    [living]: { device_id: 'dev-living', name: 'TempPV' },
    [kitchen]: { device_id: 'dev-kitchen', name: 'TempPV' },
  },
  devices: {
    'dev-living': { name: 'Living Room Window' },
    'dev-kitchen': { name: 'Kitchen Window' },
  },
};

const livingLabel = entityFriendlyName(hass, living);
const kitchenLabel = entityFriendlyName(hass, kitchen);
assert(livingLabel === 'Living Room Window TempPV', `living label: ${livingLabel}`);
assert(kitchenLabel === 'Kitchen Window TempPV', `kitchen label: ${kitchenLabel}`);
assert(livingLabel !== kitchenLabel, 'same entity name on two devices must differ');

assert(entityFriendlyName({
  states: { [living]: { attributes: { friendly_name: 'Living Room Window TempPV' } } },
}, living) === 'Living Room Window TempPV',
  'Ingress catalog friendly_name is used when registries are absent');

assert(entityFriendlyName({}, 'sensor.orphan') === 'sensor.orphan',
  'unknown entity falls back to entity id');

console.log('panel_entity_display_name.harness.mjs: ok');
