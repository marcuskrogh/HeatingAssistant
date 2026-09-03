/**
 * Expand-order helper for Overview/room KPI sections.
 * Run: node tests/panel_kpi_expand.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const mod = await import(
  `${pathToFileURL(join(ROOT, 'heatingassistant/app/static/js/components/kpi-expand.js')).href}`
);

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg);
    process.exit(1);
  }
}

const keys = ['overall-health', 'nmpc-load', 'comfort'];

{
  const next = mod.expandStateAfterClick({ keys, openKey: null, clickedKey: 'nmpc-load' });
  assert(next.openKey === 'nmpc-load', 'first click must expand nmpc-load');
  assert(next.order[0] === 'nmpc-load', 'expanded card must move to the top');
  assert(JSON.stringify(next.order.slice(1)) === JSON.stringify(['overall-health', 'comfort']), 'remaining order is original minus clicked');
}

{
  const open = mod.expandStateAfterClick({ keys, openKey: 'nmpc-load', clickedKey: 'comfort' });
  assert(open.openKey === 'comfort', 'second card must become the open card');
  assert(open.order[0] === 'comfort', 'new card must sit at the top');
  assert(open.order.includes('nmpc-load'), 'previous card stays in the section');
}

{
  const closed = mod.expandStateAfterClick({ keys, openKey: 'nmpc-load', clickedKey: 'nmpc-load' });
  assert(nextOpenNull(closed), 'clicking the open card must collapse');
  assert(JSON.stringify(closed.order) === JSON.stringify(keys), 'collapse restores original order');
}

function nextOpenNull(result) {
  return result.openKey === null;
}

class ClassList {
  constructor() { this._classes = new Set(); }
  toggle(cls, on) {
    if (on) this._classes.add(cls);
    else this._classes.delete(cls);
  }
}

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.className = '';
    this.classList = new ClassList();
    this.style = {};
    this.dataset = {};
    this._listeners = {};
    this.textContent = '';
    this.hidden = false;
    this.innerHTML = '';
  }
  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.children = child.parentNode.children.filter((c) => c !== child);
    }
    this.children.push(child);
    child.parentNode = this;
    return child;
  }
  setAttribute() {}
  addEventListener(type, fn) {
    (this._listeners[type] || (this._listeners[type] = [])).push(fn);
  }
  contains(other) {
    if (other === this) return true;
    return this.children.some((c) => c.contains(other));
  }
  getBoundingClientRect() {
    return { left: 0, top: 0, width: 120, height: 80 };
  }
  scrollIntoView() {}
}

globalThis.document = {
  createElement(tag) { return new Node(tag); },
};

{
  const grid = new Node('div');
  const host = mod.bindKpiExpandSection(grid);
  const card = new Node('div');
  host.register(card, {
    key: 'nmpc-load',
    detail: () => ({
      description: 'Share of the NMPC load budget.',
      sections: [{ title: 'NMPC', rows: [{ label: 'Load', value: '3%' }] }],
    }),
  });
  host.paint({});
  const wrap = grid.children[0];
  assert(wrap.className.includes('card'), 'wrap must be the visible card');
  const lead = wrap.children.find((c) => c.className === 'kpi-expand__lead');
  assert(!lead, 'collapsed card must not include a description lead');
  host.open('nmpc-load');
  const panel = wrap.children.find((c) => c.className === 'kpi-expand__detail');
  const inner = panel.children.find((c) => c.className === 'kpi-expand__detail-inner');
  assert(inner.innerHTML.includes('Description'), 'open card must write a Description topic');
  assert(inner.innerHTML.includes('Share of the NMPC load budget.'), 'open card must keep the description in the inset');
  assert(inner.innerHTML.includes('NMPC'), 'open card must write section titles');
  assert(inner.innerHTML.includes('3%'), 'open card must write section rows');
}

console.log('panel_kpi_expand.harness.mjs: ok');
