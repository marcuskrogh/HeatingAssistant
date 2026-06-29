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
 * is already active (hashchange would not fire).
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
globalThis.customElements = { define() {}, get() { return undefined; } };
globalThis.document = { currentScript: { src: '/x?v=84' }, createElement() { return new Shim(); }, addEventListener() {} };
globalThis.window = {
  innerWidth: 1200,
  location: {
    _hash: '',
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

// ---- real router.js ---------------------------------------------------------
const routerSrc = readFileSync(join(WWW, 'js/router.js'), 'utf8').replace(/export\s+class\s+Router/, 'class Router');
const Router = new Function(`${routerSrc}\nreturn Router;`)();
const stub = (name) => (contentEl) => { contentEl.innerHTML = `PAGE:${name}`; return { destroy() {}, update() {} }; };

globalThis.__imp = async (spec) => {
  if (spec.includes('/router.js')) return { Router };
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
src = src.replace(/\bimport\(/g, '__imp(').replace(/customElements\.define\([^;]*\);?/, '');
src = src.replace(/BOOT_WATCHDOG_MS = \d+/, 'BOOT_WATCHDOG_MS = 3000');
const Panel = new Function(`${src}\nreturn HaIndustrialPanel;`)();

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
  el._connectedFlag = false;
  el.disconnectedCallback();
  assert(!el._router, 'router must be cleared on disconnect');
  assert(!el._connection, 'connection must be cleared on disconnect');

  // --- Sidebar back: reconnect without a fresh set hass() -------------------
  window.location.hash = '#schedules';
  el._connectedFlag = true;
  el.connectedCallback();
  await bootAndWait(el);
  assert(content(el) === 'PAGE:schedules', 'reconnect must honour current hash');

  // --- Panel side menu: same-hash click must still render -------------------
  el.shadowRoot.registerNavLink(makeNavLink('#schedules'));
  window.location.hash = '#schedules';
  el._navigatePanel('#schedules');
  assert(content(el) === 'PAGE:schedules', 'same-hash nav must force router render');

  el._navigatePanel('#tuning');
  assert(content(el) === 'PAGE:tuning', 'side-menu nav must switch pages');

  console.log('heating lifecycle harness: ok');
  process.exit(0);
}
main().catch((e) => { console.error(e); process.exit(1); });
