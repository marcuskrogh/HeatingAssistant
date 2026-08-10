/**
 * Real-boot watchdog harness for the Heating Assistant panel.
 *
 * Drives the REAL _boot() — real router.js and the real boot generation/watchdog
 * logic — through the failure mode that previously left the panel stuck on
 * "INITIALIZING…" and forced a manual page reload: a dynamic import() that
 * stalls on the first attempt. The boot watchdog must abandon the stalled
 * attempt and retry so the panel recovers on its own.
 *
 * Run: node tests/panel_watchdog.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'heatingassistant/app/static');

// ---- minimal DOM shim -------------------------------------------------------
const hashListeners = [];
const makeNode = (id) => ({
  id, innerHTML: '',
  classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
  appendChild() {}, setAttribute() {}, getAttribute() { return '#overview'; },
  addEventListener() {}, querySelector() { return makeNode('q'); }, querySelectorAll() { return []; },
});
const makeShadow = () => {
  let els = new Map();
  return {
    set innerHTML(_v) { els = new Map(); },
    get innerHTML() { return ''; },
    getElementById(id) { if (!els.has(id)) els.set(id, makeNode(id)); return els.get(id); },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
};
class Shim {
  constructor() { this._shadow = null; this._connectedFlag = false; }
  attachShadow() { this._shadow = makeShadow(); return this._shadow; }
  get shadowRoot() { return this._shadow; }
  get isConnected() { return this._connectedFlag; }
  appendChild() {} addEventListener() {} removeEventListener() {}
}
globalThis.HTMLElement = Shim;
globalThis.customElements = {
  _defs: new Map(),
  define(name, ctor) {
    this._defs.set(name, ctor);
  },
  get(name) {
    return this._defs.get(name);
  },
};
globalThis.document = { currentScript: { src: '/x?v=86' }, createElement() { return new Shim(); }, addEventListener() {} };
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
  innerWidth: 1200,
  location: {
    _pathname: '/ha-industrial',
    _hash: '',
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
  addEventListener(t, fn) { if (t === 'hashchange') hashListeners.push(fn); },
  removeEventListener(t, fn) { const i = hashListeners.indexOf(fn); if (i >= 0) hashListeners.splice(i, 1); },
};

// ---- real router.js + panel-hash.js -----------------------------------------
const panelHashSrc = readFileSync(join(WWW, 'js/panel-hash.js'), 'utf8')
  .replace(/export const /g, 'const ')
  .replace(/export function /g, 'function ')
  .replace(/\ninstallPanelHashGuard\(\);\s*$/, '');
const panelHashMod = new Function(`${panelHashSrc}\nreturn { isOnPanelPath, readPanelRoute, setPanelHash, clearPanelHash, isPanelHash, PANEL_PATH };`)();
const routerSrc = readFileSync(join(WWW, 'js/router.js'), 'utf8')
  .replace(/^import\s+\{[^}]+\}\s+from\s+['"]\.\/panel-hash\.js[^'"]*['"];\r?\n/m, '')
  .replace(/export class Router/, 'class Router');
const Router = new Function('isOnPanelPath', 'readPanelRoute', 'setPanelHash', `${routerSrc}\nreturn Router;`)(
  panelHashMod.isOnPanelPath,
  panelHashMod.readPanelRoute,
  panelHashMod.setPanelHash,
);
const stub = (name) => (contentEl) => { contentEl.innerHTML = `PAGE:${name}`; return { destroy() {}, update() {} }; };

// import() loader — first router import stalls, the rest resolve.
let routerImportDone = false;
globalThis.__imp = async (spec) => {
  if (spec.includes('/router.js')) {
    if (!routerImportDone) { routerImportDone = true; return new Promise(() => {}); }
    return { Router };
  }
  if (spec.includes('/panel-hash.js')) return panelHashMod;
  if (spec.includes('/ha-connection.js')) return { HaConnection: class { updateHass() {} async getState() { return {}; } async subscribe() { return () => {}; } } };
  if (spec.includes('/discovery.js')) return { discoverRooms: () => [] };
  if (spec.includes('/pages/overview.js')) return { renderOverview: stub('overview') };
  if (spec.includes('/pages/room-detail.js')) return { renderRoomDetail: stub('room') };
  if (spec.includes('/pages/parameter-estimation.js')) return { renderParameterEstimation: stub('parameter-estimation') };
  if (spec.includes('/pages/system-status.js')) return { renderSystemStatus: stub('system-status') };
  if (spec.includes('/pages/tuning-controller.js')) return { renderControllerTuning: stub('tuning') };
  if (spec.includes('/pages/schedules.js')) return { renderSchedules: stub('schedules') };
  if (spec.includes('/pages/configuration.js')) return { renderConfiguration: stub('config') };
  throw new Error('unknown import ' + spec);
};

// ---- load the real panel, lowering the watchdog for a fast test -------------
let src = readFileSync(join(WWW, 'industrial-dashboard.js'), 'utf8');
src = src.replace(/\bimport\(/g, '__imp(');
src = src.replace(/BOOT_WATCHDOG_MS = \d+/, 'BOOT_WATCHDOG_MS = 120');
const panelBoot = new Function('customElements', 'HTMLElement', 'document', 'window', src);
panelBoot(
  globalThis.customElements,
  globalThis.HTMLElement,
  globalThis.document,
  globalThis.window,
);
const Panel = globalThis.customElements.get('ha-industrial-panel');
if (!Panel) {
  throw new Error('panel script must register ha-industrial-panel');
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const content = (el) => el.shadowRoot.getElementById('content').innerHTML;
const hass = { states: {}, callService: async () => {} };
function assert(cond, msg) { if (!cond) { console.error('FAIL:', msg); process.exit(1); } }

async function main() {
  const el = new Panel();
  el.setProperties({ panel: {}, hass, narrow: false, route: {} });
  el._connectedFlag = true;
  el.connectedCallback();

  await wait(60);
  assert(!el._router && content(el) === '', 'expected stalled boot before watchdog fires');

  await wait(250);
  assert(content(el).startsWith('PAGE:'), 'watchdog must recover a stalled boot');
  assert(!el._booting, 'booting guard must be released after recovery');

  console.log('heating watchdog harness: ok');
  process.exit(0);
}
main().catch((e) => { console.error(e); process.exit(1); });
