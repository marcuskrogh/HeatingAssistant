/**
 * Regression harness for the REAL pages/overview.js: page renders with rooms
 * present and absent, update() propagates state without throwing, schedule
 * refreshes are debounced, and destroy() releases every timer.
 *
 * Run: node tests/panel_overview_page.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'heatingassistant/app/static/js');

function assert(cond, msg) {
  if (!cond) { console.error('FAIL:', msg); process.exit(1); }
}

// ---- minimal DOM (superset of panel_climate_power's stub: adds dataset,
// textContent-bearing innerHTML children, remove/contains) -------------------
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

function matchesSelector(el, sel) {
  if (!sel.startsWith('.')) return false;
  const want = sel.slice(1);
  if (el.classList.contains(want)) return true;
  return el.className.split(/\s+/).includes(want);
}

class DomNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.className = '';
    this.classList = new ClassList();
    this.style = {};
    this.dataset = {};
    this.disabled = false;
    this.textContent = '';
    this.title = '';
    this._listeners = {};
    this._inner = '';
  }
  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.children = child.parentNode.children.filter((c) => c !== child);
    }
    this.children.push(child);
    child.parentNode = this;
    return child;
  }
  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    }
  }
  contains(other) {
    if (other === this) return true;
    return this.children.some((c) => c.contains(other));
  }
  setAttribute(name, value) { if (name.startsWith('data-')) this.dataset[name.slice(5)] = value; }
  getAttribute() { return null; }
  addEventListener(type, fn) {
    (this._listeners[type] || (this._listeners[type] = [])).push(fn);
  }
  removeEventListener(type, fn) {
    this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn);
  }
  click() {
    for (const fn of this._listeners.click || []) fn({ stopPropagation() {}, target: this });
  }
  querySelector(sel) {
    const walk = (node) => {
      if (matchesSelector(node, sel)) return node;
      for (const ch of node.children) {
        const hit = walk(ch);
        if (hit) return hit;
      }
      return null;
    };
    for (const ch of this.children) {
      const hit = walk(ch);
      if (hit) return hit;
    }
    return null;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => {
      if (matchesSelector(node, sel)) out.push(node);
      for (const ch of node.children) walk(ch);
    };
    this.children.forEach(walk);
    return out;
  }
  set innerHTML(v) {
    this._inner = v;
    this.children = [];
    // Naive parser: enough for the static templates these components emit.
    const tagRe = /<(\/?)([\w-]+)([^>]*)>([^<]*)/g;
    const stack = [this];
    let m;
    while ((m = tagRe.exec(v)) !== null) {
      const [, closing, tag, attrs, text] = m;
      if (closing) {
        if (stack.length > 1) stack.pop();
        if (text.trim()) stack[stack.length - 1].textContent += text.trim();
        continue;
      }
      const el = new DomNode(tag);
      const cls = attrs.match(/class="([^"]*)"/);
      if (cls) {
        el.className = cls[1];
        for (const c of cls[1].split(/\s+/)) if (c) el.classList.add(c);
      }
      for (const dm of attrs.matchAll(/data-([\w-]+)="([^"]*)"/g)) el.dataset[dm[1]] = dm[2];
      if (text.trim()) el.textContent = text.trim();
      stack[stack.length - 1].appendChild(el);
      const selfClosing = attrs.endsWith('/') || ['br', 'hr', 'img', 'input'].includes(tag);
      if (!selfClosing) stack.push(el);
    }
  }
  get innerHTML() { return this._inner; }
}

globalThis.document = {
  createElement(tag) { return new DomNode(tag); },
  addEventListener() {},
  removeEventListener() {},
};
// panel-hash.js (a transitive import) installs its guard at module load and
// touches history/location; give it the same inert stubs panel_hash uses.
globalThis.history = {
  state: null,
  pushState() {},
  replaceState() {},
};
globalThis.window = {
  __haIndustrialPanelHashGuard: false,
  location: {
    pathname: '/ha-industrial',
    search: '',
    hash: '',
  },
  addEventListener() {},
  removeEventListener() {},
  setInterval,
  clearInterval,
  setTimeout,
  clearTimeout,
};
globalThis.location = globalThis.window.location;

// ---- load the REAL page module ----------------------------------------------
const { renderOverview } = await import(
  `${pathToFileURL(join(WWW, 'pages/overview.js')).href}?v=96`
);

const room = (slug) => ({
  slug,
  name: slug,
  entities: {
    temperature_filtered: `sensor.heating_assistant_${slug}_temperature_filtered`,
    setpoint: `sensor.heating_assistant_${slug}_setpoint`,
    heating_power_measured: `sensor.heating_assistant_${slug}_heating_power_measured`,
    constraint_lower: `sensor.heating_assistant_${slug}_constraint_lower`,
    constraint_upper: `sensor.heating_assistant_${slug}_constraint_upper`,
    room_enabled: `switch.heating_assistant_${slug}_room_enabled`,
  },
});
const ent = (state, attributes = {}) => ({ state: String(state), attributes });

const rooms = [room('living'), room('kitchen')];
const nowS = Date.now() / 1000;
const state = {
  [rooms[0].entities.temperature_filtered]: ent('21.0'),
  [rooms[0].entities.setpoint]: ent('21.0'),
  [rooms[0].entities.constraint_lower]: ent('20.0'),
  [rooms[0].entities.constraint_upper]: ent('22.0'),
  [rooms[1].entities.temperature_filtered]: ent('19.0'),
  [rooms[1].entities.setpoint]: ent('20.0'),
  'sensor.heating_assistant_system_summary': ent('ok', { system_enabled: true }),
  'sensor.heating_assistant_mpc_performance': ent('0.12', {
    last_run_ts: nowS - 30,
    dt_s: 900,
    last_nmpc_ts: nowS - 600,
    nmpc_period_s: 7200,
  }),
};

let scheduleCalls = 0;
let experimentCalls = 0;
const connection = {
  getSchedules() { scheduleCalls += 1; return Promise.resolve({ schedules: [] }); },
  listExperiments() { experimentCalls += 1; return Promise.resolve([]); },
};
const hass = { callService() { return Promise.resolve(); } };

// ---- render with rooms -------------------------------------------------------
const container = new DomNode('div');
const page = renderOverview(container, rooms, state, connection, hass);

assert(container.children.length === 3, 'overview must render system status, controller KPIs, and rooms');
assert(
  /SYSTEM STATUS/.test(container.children[0]?._inner || ''),
  'first section must be SYSTEM STATUS',
);
assert(
  /CONTROLLER KPI/.test(container.children[1]?._inner || ''),
  'second section must be CONTROLLER KPIs',
);
assert(
  /ROOMS/.test(container.children[2]?._inner || ''),
  'third section must be ROOMS',
);
const tiles = container.querySelectorAll('.room-climate-tile');
assert(tiles.length === 2, `overview must render one tile per room (got ${tiles.length})`);
const countdownCards = container.querySelectorAll('.countdown');
assert(
  countdownCards.length === 2,
  `overview must render NEXT CONTROL and NEXT NMPC rings (got ${countdownCards.length})`,
);
const countdownLabels = countdownCards.map((card) => {
  const label = card.querySelector('.countdown__label');
  return label ? label.textContent : '';
});
assert(
  countdownLabels.includes('NEXT CONTROL'),
  `control countdown label missing (got ${countdownLabels.join(', ')})`,
);
assert(
  countdownLabels.includes('NEXT NMPC'),
  `NMPC countdown label missing (got ${countdownLabels.join(', ')})`,
);
assert(scheduleCalls === 1, 'initial render must fetch schedules exactly once (immediate)');
assert(experimentCalls === 1, 'initial render must fetch experiments exactly once');

// ---- update() must propagate without throwing and debounce schedule fetches --
const newState = { ...state, [rooms[0].entities.temperature_filtered]: ent('23.0') };
page.update(newState);
page.update(newState);
page.update(newState);
assert(scheduleCalls === 1, 'burst updates must not fetch schedules before the debounce window');
await new Promise((r) => setTimeout(r, 400));
assert(scheduleCalls === 2, `burst of 3 updates must coalesce to one schedule fetch (got ${scheduleCalls - 1})`);

// ---- destroy() must release every timer so the page can be torn down ---------
page.destroy();
const callsAfterDestroy = scheduleCalls + experimentCalls;
await new Promise((r) => setTimeout(r, 350));
assert(
  scheduleCalls + experimentCalls === callsAfterDestroy,
  'destroy() must cancel pending schedule/experiment timers',
);

// ---- empty room list must not throw -------------------------------------------
const emptyContainer = new DomNode('div');
const emptyPage = renderOverview(emptyContainer, [], state, connection, hass);
assert(
  emptyContainer.querySelectorAll('.room-climate-tile').length === 0,
  'overview with no rooms must render zero tiles',
);
emptyPage.destroy();

console.log('panel overview page harness: ok');
process.exit(0);
