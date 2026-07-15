/**
 * HA sidebar lifecycle harness for the Heating Assistant panel.
 *
 * Mirrors ha-panel-custom.ts: setProperties(hass) runs before appendChild, so
 * boot must happen in connectedCallback — not on the first set hass() while
 * disconnected.  After disconnect/reconnect HA often does not call set hass()
 * again (same object reference), so reconnect must also boot from
 * connectedCallback alone.
 *
 * Also verifies panel-nav clicks route through the router even when the hash
 * is already active (hashchange would not fire), and that panel hashes do not
 * leak onto other HA routes (e.g. /config/integrations).
 *
 * Run: node tests/panel_lifecycle.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'custom_components/heating_assistant/www');

// ---- minimal DOM shim -------------------------------------------------------
const hashListeners = [];
const makeNode = (id) => ({
  id, innerHTML: '',
  classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
  appendChild() {}, setAttribute() {},
  getAttribute() { return '#overview'; },
  addEventListener() {}, querySelector() { return makeNode('q'); },
  querySelectorAll() { return []; },
});
const makeShadow = () => {
  let els = new Map();
  let navLinks = [];
  return {
    set innerHTML(_v) { els = new Map(); navLinks = []; },
    get innerHTML() { return ''; },
    getElementById(id) { if (!els.has(id)) els.set(id, makeNode(id)); return els.get(id); },
    querySelector(sel) {
      if (sel === '.panel-nav__link') return navLinks[0] || null;
      return null;
    },
    querySelectorAll(sel) {
      if (sel === '.panel-nav__link') return navLinks;
      return [];
    },
    registerNavLink(link) { navLinks.push(link); },
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
  replaceState(_state, _title, url) {
    const path = url.split('?')[0];
    const hashIdx = url.indexOf('#');
    window.location._pathname = path;
    window.location._hash = hashIdx >= 0 ? url.slice(hashIdx) : '';
  },
};
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
const panelHashMod = new Function(`${panelHashSrc}\nreturn { isOnPanelPath, readPanelRoute, setPanelHash, clearPanelHash, stripLeakedPanelHash, installPanelHashGuard, isPanelHash, PANEL_PATH };`)();
panelHashMod.installPanelHashGuard();
const routerSrc = readFileSync(join(WWW, 'js/router.js'), 'utf8')
  .replace(/^import\s+\{[^}]+\}\s+from\s+['"]\.\/panel-hash\.js[^'"]*['"];\r?\n/m, '')
  .replace(/export class Router/, 'class Router');
const Router = new Function('isOnPanelPath', 'readPanelRoute', 'setPanelHash', `${routerSrc}\nreturn Router;`)(
  panelHashMod.isOnPanelPath,
  panelHashMod.readPanelRoute,
  panelHashMod.setPanelHash,
);
const stub = (name) => (contentEl) => { contentEl.innerHTML = `PAGE:${name}`; return { destroy() {}, update() {} }; };

globalThis.__imp = async (spec) => {
  if (spec.includes('/router.js')) return { Router };
  if (spec.includes('/panel-hash.js')) return panelHashMod;
  if (spec.includes('/ha-connection.js')) return { HaConnection: class { updateHass() {} async getState() { return {}; } } };
  if (spec.includes('/discovery.js')) return { discoverRooms: () => [] };
  if (spec.includes('/pages/overview.js')) return { renderOverview: stub('overview') };
  if (spec.includes('/pages/room-detail.js')) return { renderRoomDetail: stub('room') };
  if (spec.includes('/pages/system-identification.js')) return { renderSystemIdentification: stub('ident') };
  if (spec.includes('/pages/tuning-controller.js')) return { renderControllerTuning: stub('tuning') };
  if (spec.includes('/pages/schedules.js')) return { renderSchedules: stub('schedules') };
  if (spec.includes('/pages/configuration.js')) return { renderConfiguration: stub('config') };
  throw new Error('unknown import ' + spec);
};

let src = readFileSync(join(WWW, 'industrial-dashboard.js'), 'utf8');
src = src.replace(/\bimport\(/g, '__imp(');
src = src.replace(/BOOT_WATCHDOG_MS = \d+/, 'BOOT_WATCHDOG_MS = 3000');
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

function makeNavLink(href) {
  return {
    getAttribute(attr) { return attr === 'href' ? href : null; },
    addEventListener() {},
    classList: { toggle() {} },
  };
}

async function bootAndWait(el) {
  await wait(50);
  assert(el._router, 'expected router after boot');
  assert(content(el).startsWith('PAGE:'), 'expected page content after boot');
}

async function main() {
  // --- HA mount: set hass before connect ------------------------------------
  const el = new Panel();
  el.setProperties({ panel: {}, hass, narrow: false, route: {} });
  assert(!el._router, 'must not boot before element is connected');

  el._connectedFlag = true;
  el.connectedCallback();
  await bootAndWait(el);

  // --- Sidebar away: full teardown ------------------------------------------
  window.location._pathname = '/config/integrations';
  window.location.hash = '#config/rooms';
  el._connectedFlag = false;
  el.disconnectedCallback();
  assert(!el._router, 'router must be cleared on disconnect');
  assert(!el._connection, 'connection must be cleared on disconnect');
  await wait(5);
  assert(window.location.hash === '', 'panel hash must not leak onto integrations route');

  // --- HA sidebar uses pushState; hash must strip even if disconnect missed --
  window.location._pathname = '/ha-industrial';
  window.location.hash = '#config/rooms';
  history.pushState(null, '', '/config/integrations');
  assert(window.location.hash === '', 'pushState to integrations must strip panel hash');

  // --- Sidebar back: new element, reconnect without fresh set hass() ----------
  window.location._pathname = '/ha-industrial';
  window.location.hash = '#schedules';
  const el2 = new Panel();
  el2._hass = hass;
  el2._connectedFlag = true;
  el2.connectedCallback();
  await bootAndWait(el2);
  assert(content(el2) === 'PAGE:schedules', 'reconnect must honour current hash');

  // --- Stale _booting guard must not block reconnect --------------------------
  const el3 = new Panel();
  el3.setProperties({ panel: {}, hass, narrow: false, route: {} });
  el3._connectedFlag = true;
  el3._booting = true;
  el3.connectedCallback();
  await bootAndWait(el3);

  // --- Panel side menu: same-hash click must still render -------------------
  el2.shadowRoot.registerNavLink(makeNavLink('#schedules'));
  window.location.hash = '#schedules';
  el2._navigatePanel('#schedules');
  assert(content(el2) === 'PAGE:schedules', 'same-hash nav must force router render');

  el2._navigatePanel('#tuning');
  assert(content(el2) === 'PAGE:tuning', 'side-menu nav must switch pages');

  // --- setPanelHash must not write hash off-panel ----------------------------
  window.location._pathname = '/config/integrations';
  window.location.hash = '#domain=heating_assistant';
  panelHashMod.setPanelHash('#config');
  assert(window.location.hash === '#domain=heating_assistant', 'setPanelHash must no-op off the panel path');

  console.log('heating lifecycle harness: ok');
  process.exit(0);
}
main().catch((e) => { console.error(e); process.exit(1); });
