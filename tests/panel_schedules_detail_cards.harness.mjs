/**
 * SWD-491: room schedule detail must render persisted period cards and hide
 * the empty inactive section so periods can be opened and edited.
 *
 * Run: node tests/panel_schedules_detail_cards.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DETAIL = join(ROOT, 'heatingassistant/app/static/js/schedules/schedules-detail.js');
const INDEX = join(ROOT, 'heatingassistant/app/static/js/schedules/schedules-index.js');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class ClassList {
  constructor(el) {
    this._el = el;
    this._classes = new Set();
  }
  _sync() {
    this._el._className = [...this._classes].join(' ');
  }
  toggle(cls, on) {
    if (on === undefined) {
      if (this._classes.has(cls)) this._classes.delete(cls);
      else this._classes.add(cls);
    } else if (on) this._classes.add(cls);
    else this._classes.delete(cls);
    this._sync();
  }
  add(...cls) {
    for (const c of cls) if (c) this._classes.add(c);
    this._sync();
  }
  remove(...cls) {
    for (const c of cls) this._classes.delete(c);
    this._sync();
  }
  contains(cls) {
    return this._classes.has(cls);
  }
}

function parseAttrs(raw) {
  const attrs = {};
  const re = /([:@]?[\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  let m;
  while ((m = re.exec(raw)) !== null) {
    attrs[m[1]] = m[2] ?? m[3] ?? m[4] ?? '';
  }
  return attrs;
}

function matches(el, sel) {
  const parts = sel.trim().split(/(?=[.#\[])/).filter(Boolean);
  if (!parts.length) return false;
  for (const part of parts) {
    if (part.startsWith('#')) {
      if (el.id !== part.slice(1)) return false;
    } else if (part.startsWith('.')) {
      if (!el.classList.contains(part.slice(1))) return false;
    } else if (part.startsWith('[')) {
      const inner = part.slice(1, -1);
      const eq = inner.indexOf('=');
      if (eq < 0) {
        if (el.getAttribute(inner) == null && el.dataset[inner.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase())] == null) {
          return false;
        }
      } else {
        const key = inner.slice(0, eq).trim();
        const val = inner.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
        if (String(el.getAttribute(key) ?? '') !== val) return false;
      }
    } else if (part.toUpperCase() !== el.tagName) {
      return false;
    }
  }
  return true;
}

class DomNode {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._className = '';
    this.classList = new ClassList(this);
    this.style = {};
    this.dataset = {};
    this._attrs = {};
    this._listeners = {};
    this._text = '';
    this._inner = '';
    this.disabled = false;
    this._hidden = false;
    this.id = '';
    this.value = '';
    this.name = '';
  }
  get className() {
    return this._className;
  }
  set className(v) {
    this._className = String(v || '');
    this.classList._classes = new Set(this._className.split(/\s+/).filter(Boolean));
  }
  get hidden() {
    return this._hidden;
  }
  set hidden(v) {
    this._hidden = Boolean(v);
  }
  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) {
    this._text = String(v ?? '');
    this.children = [];
  }
  get firstChild() {
    return this.children[0] || null;
  }
  get firstElementChild() {
    return this.children[0] || null;
  }
  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.children = child.parentNode.children.filter((c) => c !== child);
    }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child, ref) {
    if (!ref) return this.appendChild(child);
    const idx = this.children.indexOf(ref);
    if (idx < 0) return this.appendChild(child);
    if (child.parentNode) {
      child.parentNode.children = child.parentNode.children.filter((c) => c !== child);
    }
    child.parentNode = this;
    this.children.splice(idx, 0, child);
    return child;
  }
  setAttribute(name, value) {
    const key = String(name);
    const val = String(value ?? '');
    this._attrs[key] = val;
    if (key === 'id') this.id = val;
    if (key === 'class') this.className = val;
    if (key === 'hidden') this._hidden = true;
    if (key.startsWith('data-')) {
      const camel = key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      this.dataset[camel] = val;
    }
  }
  getAttribute(name) {
    const key = String(name);
    if (key === 'id') return this.id || null;
    if (key === 'class') return this.className || null;
    if (key === 'hidden') return this._hidden ? '' : null;
    return Object.prototype.hasOwnProperty.call(this._attrs, key) ? this._attrs[key] : null;
  }
  removeAttribute(name) {
    delete this._attrs[name];
    if (name === 'hidden') this._hidden = false;
    if (name === 'draggable') delete this._attrs.draggable;
  }
  addEventListener(type, fn) {
    (this._listeners[type] || (this._listeners[type] = [])).push(fn);
  }
  click() {
    const event = {
      target: this,
      stopPropagation() {},
      preventDefault() {},
    };
    for (const fn of this._listeners.click || []) fn(event);
  }
  closest(sel) {
    let node = this;
    while (node) {
      if (matches(node, sel)) return node;
      node = node.parentNode;
    }
    return null;
  }
  scrollIntoView() {}
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => {
      if (matches(node, sel)) out.push(node);
      for (const ch of node.children) walk(ch);
    };
    for (const ch of this.children) walk(ch);
    return out;
  }
  set innerHTML(v) {
    this._inner = String(v ?? '');
    this.children = [];
    const tokenRe = /<\/?[\w-]+[^>]*>/g;
    const stack = [this];
    let last = 0;
    let m;
    while ((m = tokenRe.exec(this._inner)) !== null) {
      if (m.index > last) {
        const text = this._inner.slice(last, m.index);
        if (text.trim() && stack[stack.length - 1] !== this) {
          stack[stack.length - 1]._text += text.replace(/\s+/g, ' ').trim();
        }
      }
      last = m.index + m[0].length;
      const full = m[0];
      if (full.startsWith('</')) {
        if (stack.length > 1) stack.pop();
        continue;
      }
      const open = full.match(/^<([\w-]+)([^>]*)\/?>$/);
      if (!open) continue;
      const el = new DomNode(open[1]);
      const attrs = parseAttrs(open[2] || '');
      for (const [k, val] of Object.entries(attrs)) el.setAttribute(k, val);
      stack[stack.length - 1].appendChild(el);
      if (!full.endsWith('/>') && !['BR', 'INPUT', 'IMG', 'HR', 'META'].includes(el.tagName)) {
        stack.push(el);
      }
    }
  }
  get innerHTML() {
    return this._inner;
  }
}

globalThis.window = {
  location: { pathname: '/ha-industrial', hash: '#schedules/living_room', search: '' },
  __HA_INGRESS_BASE: true,
  addEventListener() {},
  removeEventListener() {},
};
globalThis.history = { state: null, replaceState() {}, pushState() {} };
globalThis.sessionStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.document = {
  createElement(tag) { return new DomNode(tag); },
  addEventListener() {},
};

const weeklyPeriod = {
  name: 'Evening',
  schedule_type: 'weekly_recurring',
  time_mode: 'window',
  start: '18:00',
  end: '22:00',
  days: [0, 1, 2, 3, 4],
  mode: 'comfort',
  enabled: true,
};
const rooms = [{ slug: 'living_room', name: 'Living Room' }];
const state = {};
const hass = { callService: async () => {} };
const payload = { living_room: { enabled: true, periods: [weeklyPeriod] } };
const connection = {
  getSchedules: async () => payload,
  listExperiments: async () => [],
};

const { renderScheduleDetail } = await import(`${pathToFileURL(DETAIL).href}?v=152`);
const detailRoot = new DomNode('div');
const detailPage = renderScheduleDetail(detailRoot, 'living_room', rooms, state, connection, hass);
await new Promise((r) => setTimeout(r, 0));
await new Promise((r) => setTimeout(r, 0));

const title = detailRoot.querySelector('#sched-periods-title');
assert(title, 'detail must render the comfort-periods title');
assert(
  String(title.textContent).includes('(1)'),
  `comfort title must count the persisted period, got ${JSON.stringify(title.textContent)}`,
);

const cards = detailRoot.querySelectorAll('.schedule-form__period');
const periodCards = cards.filter((el) => el.dataset.periodIndex != null);
assert(
  periodCards.length === 1,
  `detail must render one period card, got ${periodCards.length}`,
);
assert(
  String(periodCards[0].textContent).includes('Evening'),
  'period card must show the persisted period name',
);

const inactiveHeader = detailRoot.querySelector('.sched-detail__section-header--inactive');
assert(inactiveHeader, 'inactive section header exists in the detail page');
assert(
  inactiveHeader.hidden === true,
  'INACTIVE PERIODS must stay hidden when every period is active',
);

const body = periodCards[0].querySelector('.schedule-form__period-body');
assert(body, 'period card must include an editor body');
assert(body.hidden === true, 'editor body starts collapsed');
periodCards[0].querySelector('.schedule-form__period-header').click();
assert(body.hidden === false, 'clicking the header must expand the editor');
assert(
  periodCards[0].querySelector('[data-field="name"]'),
  'expanded editor must expose the period name field',
);
detailPage.destroy();

const { renderScheduleIndex } = await import(`${pathToFileURL(INDEX).href}?v=152`);
const indexRoot = new DomNode('div');
renderScheduleIndex(indexRoot, rooms, state, connection, hass);
await new Promise((r) => setTimeout(r, 0));
await new Promise((r) => setTimeout(r, 0));
assert(
  String(indexRoot.textContent).includes('Evening'),
  'schedules overview must still list the persisted period',
);

console.log('panel schedules detail cards harness: ok');
