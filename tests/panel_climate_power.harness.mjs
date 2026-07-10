/**
 * Regression harness: climate-card and room-climate-tile power toggles must
 * call HA services and reconcile optimistic state when the backend confirms.
 *
 * Run: node tests/panel_climate_power.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'custom_components/heating_assistant/www/js');

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

// ---- minimal DOM for component construction ---------------------------------
class ClassList {
  constructor(el) { this._el = el; this._classes = new Set(); }
  toggle(cls, on) {
    if (on === undefined) {
      if (this._classes.has(cls)) this._classes.delete(cls);
      else this._classes.add(cls);
    } else if (on) this._classes.add(cls);
    else this._classes.delete(cls);
  }
  add(cls) { this._classes.add(cls); }
  remove(cls) { this._classes.delete(cls); }
  contains(cls) { return this._classes.has(cls); }
}

function matchesSelector(el, sel) {
  if (!sel.startsWith('.')) return false;
  const want = sel.slice(1);
  if (el.classList.contains(want)) return true;
  // compound BEM modifier e.g. .climate-card__step--down
  return el.className.split(/\s+/).includes(want);
}

class DomNode {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.className = '';
    this.classList = new ClassList(this);
    this.style = {};
    this.disabled = false;
    this._listeners = {};
    this.textContent = '';
    this._inner = '';
  }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  setAttribute() {}
  getAttribute() { return null; }
  addEventListener(type, fn) {
    (this._listeners[type] || (this._listeners[type] = [])).push(fn);
  }
  click() {
    for (const fn of this._listeners.click || []) fn({ stopPropagation() {} });
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
    return walk(this);
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => {
      if (matchesSelector(node, sel)) out.push(node);
      for (const ch of node.children) walk(ch);
    };
    walk(this);
    return out;
  }
  set innerHTML(v) {
    this._inner = v;
    this.children = [];
    const tagRe = /<(\w+)([^>]*)>/g;
    const stack = [this];
    let m;
    while ((m = tagRe.exec(v)) !== null) {
      const full = m[0];
      if (full.startsWith('</')) {
        stack.pop();
        continue;
      }
      const tag = m[1];
      const attrs = m[2] || '';
      const el = new DomNode(tag);
      const cls = attrs.match(/class="([^"]*)"/);
      if (cls) {
        el.className = cls[1];
        for (const c of cls[1].split(/\s+/)) if (c) el.classList.add(c);
      }
      stack[stack.length - 1].appendChild(el);
      if (!full.endsWith('/>')) stack.push(el);
    }
  }
  get innerHTML() { return this._inner; }
}

globalThis.document = {
  createElement(tag) { return new DomNode(tag); },
};

// ---- load climate-card with real transitive imports -------------------------
const climateCardUrl = `${pathToFileURL(join(WWW, 'components/climate-card.js')).href}?v=96`;
const { createClimateCard } = await import(climateCardUrl);

// Power click must invoke the onPowerToggle callback.
{
  let toggled = null;
  const card = createClimateCard({
    temperature: 20,
    setpoint: 21,
    power: 0,
    off: false,
    onPowerToggle: (turnOff) => { toggled = turnOff; },
  });
  card.element.querySelector('.climate-card__power').click();
  assert(toggled === true, 'onPowerToggle must be called with turnOff=true when room is on');
  assert(
    card.element.classList.contains('climate-card--off'),
    'card must optimistically show OFF after power click',
  );
}

// Backend confirmation must clear the optimistic override and keep the OFF look.
{
  const card = createClimateCard({
    temperature: 20,
    setpoint: 21,
    power: 0,
    off: false,
    onPowerToggle: () => {},
  });
  card.element.querySelector('.climate-card__power').click();
  card.update({ off: true });
  assert(
    card.element.classList.contains('climate-card--off'),
    'card must stay OFF once backend confirms disabled',
  );
  card.element.querySelector('.climate-card__power').click();
  card.update({ off: false });
  assert(
    !card.element.classList.contains('climate-card--off'),
    'card must show ON once backend confirms enabled',
  );
}

// Optimistic OFF must persist until the backend confirms — no timed revert.
{
  const card = createClimateCard({
    temperature: 20,
    setpoint: 21,
    power: 0,
    off: false,
    onPowerToggle: () => {},
  });
  card.element.querySelector('.climate-card__power').click();
  await new Promise((r) => setTimeout(r, 50));
  assert(
    card.element.classList.contains('climate-card--off'),
    'card must stay OFF while waiting for backend confirmation',
  );
}

// Service failure must revert the optimistic override.
{
  const card = createClimateCard({
    temperature: 20,
    setpoint: 21,
    power: 0,
    off: false,
    onPowerToggle: () => Promise.reject(new Error('service failed')),
  });
  card.element.querySelector('.climate-card__power').click();
  await new Promise((r) => setTimeout(r, 0));
  assert(
    !card.element.classList.contains('climate-card--off'),
    'card must revert to ON when the power service call fails',
  );
}

// ---- static source regressions for overview tile + backend ------------------
const tileSrc = readFileSync(join(WWW, 'components/room-climate-tile.js'), 'utf8');
assert(
  /els\.power\.addEventListener\('click',\s*\(e\)\s*=>\s*\{\s*e\.stopPropagation\(\);\s*togglePower\(\);\s*\}\)/.test(tileSrc),
  'room-climate-tile power button must stopPropagation before togglePower',
);
assert(
  /reconcileOptimisticPower/.test(tileSrc),
  'room-climate-tile must reconcile optimistic power state on update',
);

const cardSrc = readFileSync(join(WWW, 'components/climate-card.js'), 'utf8');
assert(
  /reconcileOptimisticPower/.test(cardSrc),
  'climate-card must reconcile optimistic power state on update',
);
assert(
  !/POWER_OPTIMISTIC_MS/.test(cardSrc),
  'climate-card must not use a timed optimistic power revert',
);
assert(
  !/POWER_OPTIMISTIC_MS/.test(tileSrc),
  'room-climate-tile must not use a timed optimistic power revert',
);

const controlSrc = readFileSync(
  join(ROOT, 'custom_components/heating_assistant/services/control.py'),
  'utf8',
);
const handlerBlock = controlSrc.slice(
  controlSrc.indexOf('async def handle_set_room_enabled'),
  controlSrc.indexOf('def register_control_services'),
);
assert(
  handlerBlock.includes('coordinator.async_update_listeners()'),
  'handle_set_room_enabled must call async_update_listeners before refresh',
);

console.log('panel climate power harness: ok');
