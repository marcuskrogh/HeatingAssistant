/**
 * Regression harness: both custom-panel entry scripts share HA's global scope.
 *
 * Before the IIFE fix, visiting Heating Assistant after Charging Assistant (or
 * re-running the same entry script) threw:
 *   SyntaxError: Identifier 'BASE_PATH' has already been declared
 *
 * Run: node tests/panel_global_scope.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PANEL_PATH = join(ROOT, 'custom_components/heating_assistant/www/industrial-dashboard.js');

// Minimal stand-in for another integration's classic entry script (old pattern).
const OTHER_PANEL_ENTRY = `
const BASE_PATH = '/ha-charging-assistant-panel';
const PANEL_VERSION = '17';
const BOOT_WATCHDOG_MS = 6000;
class OtherPanel extends HTMLElement {}
if (!customElements.get('other-panel')) {
  customElements.define('other-panel', OtherPanel);
}
`;

globalThis.customElements = {
  _defs: new Map(),
  define(name, ctor) {
    this._defs.set(name, ctor);
  },
  get(name) {
    return this._defs.get(name);
  },
};
globalThis.HTMLElement = class {};
globalThis.document = {
  currentScript: { src: '/ha-industrial-panel/industrial-dashboard.js?v=87' },
  createElement() {
    return { id: '', innerHTML: '', classList: { toggle() {} } };
  },
  addEventListener() {},
};
globalThis.window = {
  __haIndustrialPanelHashGuard: false,
  innerWidth: 1200,
  location: {
    pathname: '/ha-industrial',
    hash: '#overview',
    search: '',
  },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.history = {
  state: null,
  pushState() {},
  replaceState() {},
};

const panelSource = readFileSync(PANEL_PATH, 'utf8');

function execScript(source) {
  const fn = new Function(
    'customElements',
    'HTMLElement',
    'document',
    'window',
    'history',
    source,
  );
  fn(
    globalThis.customElements,
    globalThis.HTMLElement,
    globalThis.document,
    globalThis.window,
    globalThis.history,
  );
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function testPanelLoadsTwice() {
  execScript(panelSource);
  assert(
    globalThis.customElements.get('ha-industrial-panel'),
    'first load must register the custom element',
  );
  execScript(panelSource);
  assert(
    globalThis.customElements.get('ha-industrial-panel'),
    'second load must keep the custom element registered',
  );
}

function testPanelLoadsAfterOtherPanelGlobals() {
  execScript(OTHER_PANEL_ENTRY);
  execScript(panelSource);
  assert(
    globalThis.customElements.get('ha-industrial-panel'),
    'panel must load after another entry script occupied BASE_PATH in global scope',
  );
  execScript(panelSource);
}

function testOtherThenHeatingThenOtherThenHeating() {
  execScript(OTHER_PANEL_ENTRY);
  execScript(panelSource);
  execScript(OTHER_PANEL_ENTRY);
  execScript(panelSource);
  assert(
    globalThis.customElements.get('ha-industrial-panel'),
    'round-trip across panels must not break registration',
  );
}

testPanelLoadsTwice();
testPanelLoadsAfterOtherPanelGlobals();
testOtherThenHeatingThenOtherThenHeating();
console.log('panel global scope harness: ok');
