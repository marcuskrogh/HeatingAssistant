/**
 * Panel hash guard harness.
 *
 * Verifies that panel-owned hashes are stripped when HA navigates away via
 * pushState/replaceState (sidebar navigation) while preserving HA deep links.
 *
 * Run: node tests/panel_hash.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'custom_components/heating_assistant/www');

const hashListeners = [];
const locationChangedListeners = [];

globalThis.history = {
  state: null,
  pushState(_state, _title, url) {
    const path = url.split('?')[0];
    const hashIdx = url.indexOf('#');
    window.location._pathname = path;
    window.location._hash = hashIdx >= 0 ? url.slice(hashIdx) : '';
  },
  replaceState(_state, _title, url) {
    const path = url.split('?')[0];
    const hashIdx = url.indexOf('#');
    window.location._pathname = path;
    window.location._hash = hashIdx >= 0 ? url.slice(hashIdx) : '';
  },
};

globalThis.window = {
  __haIndustrialPanelHashGuard: false,
  location: {
    _pathname: '/ha-industrial',
    _hash: '#config/rooms',
    get pathname() { return this._pathname; },
    get search() { return ''; },
    get hash() { return this._hash; },
    set hash(v) {
      if (this._hash !== v) {
        this._hash = v;
        hashListeners.forEach((fn) => fn());
      }
    },
  },
  addEventListener(type, fn) {
    if (type === 'hashchange') hashListeners.push(fn);
    if (type === 'location-changed') locationChangedListeners.push(fn);
  },
  removeEventListener() {},
};

function dispatchLocationChanged() {
  locationChangedListeners.forEach((fn) => fn());
}

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg);
    process.exit(1);
  }
}

const panelHashSrc = readFileSync(join(WWW, 'js/panel-hash.js'), 'utf8')
  .replace(/export const /g, 'const ')
  .replace(/export function /g, 'function ')
  .replace(/\ninstallPanelHashGuard\(\);\s*$/, '');
const panelHashMod = new Function(`${panelHashSrc}\nreturn { isOnPanelPath, isPanelHash, setPanelHash, stripLeakedPanelHash, clearPanelHash, installPanelHashGuard, PANEL_PATH };`)();

panelHashMod.installPanelHashGuard();

// pushState to integrations must strip panel hash
window.location._pathname = '/ha-industrial';
window.location.hash = '#config/rooms';
history.pushState(null, '', '/config/integrations');
assert(window.location.pathname === '/config/integrations', 'pathname must update');
assert(window.location.hash === '', 'pushState away from panel must strip panel hash');

// HA integration deep links must be preserved
window.location._pathname = '/config/integrations';
window.location.hash = '#domain=heating_assistant';
panelHashMod.stripLeakedPanelHash();
assert(window.location.hash === '#domain=heating_assistant', 'HA domain hash must be preserved');

// setPanelHash must not write off-panel
panelHashMod.setPanelHash('#schedules');
assert(window.location.hash === '#domain=heating_assistant', 'setPanelHash must no-op off panel path');

// Deferred write race: timer fires while still on panel, then HA navigates
window.location._pathname = '/ha-industrial';
window.location.hash = '#overview';
panelHashMod.setPanelHash('#config/sources');
assert(window.location.hash === '#config/sources', 'setPanelHash works on panel path');
history.pushState(null, '', '/config/integrations');
assert(window.location.hash === '', 'hash must strip after sidebar navigation');

// location-changed event (HA sidebar pattern)
window.location._pathname = '/ha-industrial';
window.location.hash = '#schedules';
window.location._pathname = '/config/integrations';
dispatchLocationChanged();
assert(window.location.hash === '', 'location-changed must strip leaked panel hash');

// replaceState hook
window.location._pathname = '/ha-industrial';
window.location.hash = '#tuning';
history.replaceState(null, '', '/config/devices');
assert(window.location.hash === '', 'replaceState away from panel must strip panel hash');

// clearPanelHash only strips panel hashes
window.location._pathname = '/config/integrations';
window.location.hash = '#domain=mqtt';
panelHashMod.clearPanelHash();
assert(window.location.hash === '#domain=mqtt', 'clearPanelHash must preserve non-panel hashes');

window.location.hash = '#room/living';
panelHashMod.clearPanelHash();
assert(window.location.hash === '', 'clearPanelHash must strip panel hashes');

console.log('panel hash guard harness: ok');
