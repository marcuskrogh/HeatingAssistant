/**
 * Harness: System Status page renders MODULES + escapes HTML, and the panel
 * health indicator maps system_quality to HEALTHY/WARNING/ERROR classes.
 *
 * Run: node tests/panel_system_status.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'heatingassistant/app/static/js');
const DASHBOARD = join(ROOT, 'heatingassistant/app/static/industrial-dashboard.js');
const INDUSTRIAL_CSS = join(ROOT, 'heatingassistant/app/static/css/industrial.css');

function assert(cond, msg) {
  if (!cond) { console.error('FAIL:', msg); process.exit(1); }
}

class ClassList {
  constructor() { this._classes = new Set(); }
  toggle(cls, on) {
    if (on === undefined) {
      if (this._classes.has(cls)) this._classes.delete(cls); else this._classes.add(cls);
    } else if (on) this._classes.add(cls);
    else this._classes.delete(cls);
  }
  add(...cls) { cls.forEach((c) => this._classes.add(c)); }
  remove(...cls) { cls.forEach((c) => this._classes.delete(c)); }
  contains(cls) { return this._classes.has(cls); }
}

class DomNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.className = '';
    this.classList = new ClassList();
    this.style = {};
    this.dataset = {};
    this.textContent = '';
    this._inner = '';
    this.id = '';
  }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  getElementById(id) {
    if (this.id === id) return this;
    for (const ch of this.children) {
      const hit = ch.getElementById?.(id);
      if (hit) return hit;
    }
    return null;
  }
  set innerHTML(v) {
    this._inner = String(v);
    this.textContent = this._inner.replace(/<[^>]+>/g, ' ');
  }
  get innerHTML() { return this._inner; }
}

globalThis.document = {
  createElement(tag) { return new DomNode(tag); },
};
globalThis.history = { state: null, pushState() {}, replaceState() {} };
globalThis.window = {
  __haIndustrialPanelHashGuard: false,
  location: { pathname: '/ha-industrial', search: '', hash: '' },
  addEventListener() {},
  removeEventListener() {},
};

const { renderSystemStatus } = await import(
  `${pathToFileURL(join(WWW, 'pages/system-status.js')).href}?v=1`
);

const state = {
  'sensor.heating_assistant_system_summary': {
    state: '0',
    attributes: {
      system_quality: 'warning',
      issue_summary: '<b>MQTT</b> & "bad"',
      mqtt_connected: true,
      uptime_s: 90,
      entity_catalog_count: 12,
      bindings_count: 3,
      control_mode: 'mpc',
      fallback_reason: null,
      modules: [
        { id: 'mqtt', label: 'MQTT', quality: 'healthy', detail: 'connected' },
        { id: 'sensors', label: 'Sensors / tags', quality: 'warning', detail: 'BAD quality on 1 tag(s)' },
      ],
    },
  },
  'sensor.heating_assistant_mpc_performance': {
    state: '0.4',
    attributes: { last_run_ts: 1, dt_s: 900, mean_tracking_error: 0.1 },
  },
};

const container = new DomNode('div');
const page = renderSystemStatus(container, [{ slug: 'living', name: 'Living' }], state, {}, { states: state });
const html = container.children[0]?._inner || '';
assert(/MODULES/.test(html), 'System Status must render MODULES section');
assert(/Sensors \/ tags/.test(html), 'MODULES must include sensor module label');
assert(!html.includes('<b>MQTT</b>'), 'issue_summary HTML must be escaped');
assert(html.includes('&lt;b&gt;MQTT&lt;/b&gt;'), 'escaped issue_summary must appear');
assert(/system-status__issue--warning/.test(html), 'issue block must use warning class');
page.destroy();

// Health indicator CSS + dashboard method presence
const css = readFileSync(INDUSTRIAL_CSS, 'utf8');
assert(css.includes('.live-dot--healthy'), 'industrial.css must define live-dot--healthy');
assert(css.includes('.live-label--error'), 'industrial.css must define live-label--error');
const dashboard = readFileSync(DASHBOARD, 'utf8');
assert(dashboard.includes('_updateHealthIndicator'), 'dashboard must update health indicator');
assert(dashboard.includes('live-dot--${quality}') || dashboard.includes('live-dot--${quality}') || /live-dot--\$\{quality\}/.test(dashboard),
  'dashboard must apply live-dot--{quality} class');

console.log('panel system status harness: ok');
