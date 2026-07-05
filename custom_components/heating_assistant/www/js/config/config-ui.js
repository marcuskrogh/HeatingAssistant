// Shared DOM builders for configuration sub-pages.
import { createCollapsible } from '../components/collapsible.js?v=92';
import { setPanelHash } from '../panel-hash.js?v=92';

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}

function escapeAttr(v) {
  return v != null ? String(v).replace(/"/g, '&quot;') : '';
}

/** Deferred in-panel navigation; callers must clear via returned timer id on destroy. */
function schedulePanelNav(hash, delayMs = 800) {
  return setTimeout(() => { setPanelHash(hash); }, delayMs);
}

function backNav(label, hash) {
  const nav = el('button', 'nav-back');
  nav.innerHTML = `<span class="nav-back__arrow">←</span> ${label}`;
  nav.addEventListener('click', () => { setPanelHash(hash); });
  return nav;
}

function sectionCard(title, desc) {
  const card = el('div', 'card config-section');
  if (title) card.appendChild(el('div', 'config-section__title', title));
  if (desc) card.appendChild(el('p', 'config-section__desc', desc));
  return card;
}

// Collapsible "Advanced" subsection appended inside a section card. Uses the
// shared collapsible primitive (title left, grey chevron right) so every
// expandable menu in the app looks and behaves identically. Returns the body
// element callers append fields to.
function advancedSubsection(parent, title = 'Advanced settings') {
  const sec = createCollapsible({ title, open: false });
  sec.element.classList.add('config-advanced');
  parent.appendChild(sec.element);
  return sec.body;
}

function configListHeader(title, addLabel, onAdd) {
  const header = el('div', 'sched-detail__section-header');
  header.appendChild(el('span', 'sched-detail__section-title', title));
  const addBtn = el('button', 'btn btn--primary btn--sm');
  addBtn.dataset.role = 'add';
  addBtn.textContent = addLabel;
  addBtn.addEventListener('click', onAdd);
  header.appendChild(addBtn);
  return header;
}

function configPageShell(container, { backLabel, backHash, title, description } = {}) {
  container.innerHTML = '';
  if (backLabel != null && backHash != null) {
    container.appendChild(backNav(backLabel, backHash));
  }
  if (title) {
    container.appendChild(el('div', 'section-header', title));
  }
  if (description) {
    container.appendChild(el('p', 'config-section__desc', description));
  }
  const body = el('div');
  container.appendChild(body);
  return { body };
}

function actionsBar(primaryLabel, { placement = 'top', secondaryLabel } = {}) {
  const classes = placement === 'footer'
    ? 'tuning-actions tuning-actions--footer'
    : 'tuning-actions';
  const row = el('div', classes);
  let html = `<button class="btn btn--primary tuning-actions__btn" data-role="save">${primaryLabel}</button>`;
  if (secondaryLabel) {
    html += `<button class="btn btn--secondary" data-role="secondary">${secondaryLabel}</button>`;
  }
  html += `<span class="tuning-actions__status" data-role="status"></span>`;
  row.innerHTML = html;
  return row;
}

function editorActionsBar({ primaryLabel, showDelete = false, deleteLabel } = {}) {
  const row = el('div', 'tuning-actions tuning-actions--footer');
  row.innerHTML = `
    <button class="btn btn--primary tuning-actions__btn" data-role="save">${primaryLabel}</button>
    <span class="tuning-actions__status" data-role="status"></span>
  `;
  if (showDelete && deleteLabel) {
    const statusEl = row.querySelector('[data-role="status"]');
    const deleteBtn = el('button', 'btn btn--danger');
    deleteBtn.dataset.role = 'delete';
    deleteBtn.textContent = deleteLabel;
    row.insertBefore(deleteBtn, statusEl);
  }
  return row;
}

function setStatus(statusEl, text, type = '') {
  statusEl.textContent = text;
  statusEl.className = 'tuning-actions__status';
  if (type) statusEl.classList.add(`tuning-actions__status--${type}`);
}

// A labelled numeric field bound to obj[key]. Empty input deletes the key so
// the backend default applies.
function numberField(obj, key, label, { step = 1, unit = '', hint = '', min, max, onChange } = {}) {
  const group = el('div', 'form-group');
  const val = obj[key];
  const minAttr = min != null ? ` min="${min}"` : '';
  const maxAttr = max != null ? ` max="${max}"` : '';
  group.innerHTML = `
    <label class="form-label">${label}</label>
    <input class="form-input" type="number" step="${step}"${minAttr}${maxAttr}
      value="${val != null ? val : ''}">
    <span class="form-hint">${unit ? unit + ' — ' : ''}${hint}</span>
  `;
  const input = group.querySelector('input');
  input.addEventListener('change', () => {
    if (input.value === '') { delete obj[key]; }
    else { obj[key] = Number(input.value); }
    if (onChange) onChange();
  });
  return group;
}

function textField(obj, key, label, { hint = '', placeholder = '' } = {}) {
  const group = el('div', 'form-group');
  group.innerHTML = `
    <label class="form-label">${label}</label>
    <input class="form-input" type="text" placeholder="${placeholder}"
      value="${escapeAttr(obj[key])}">
    <span class="form-hint">${hint}</span>
  `;
  const input = group.querySelector('input');
  input.addEventListener('change', () => {
    const v = input.value.trim();
    if (v === '') { delete obj[key]; return; }
    obj[key] = v;
  });
  return group;
}

function selectField(obj, key, label, options, { hint = '', def, onChange } = {}) {
  const group = el('div', 'form-group');
  const current = obj[key] != null ? obj[key] : def;
  const values = options.map((o) => typeof o === 'object' ? o.value : o);
  // If the stored value is stale (not in the options list), snap to the first
  // valid option so the visible selection always reflects what will be saved.
  const effectiveCurrent = values.length > 0 && !values.some((v) => String(v) === String(current))
    ? values[0]
    : current;
  if (effectiveCurrent !== current) obj[key] = String(effectiveCurrent);
  const opts = options.map((o) => {
    const value = typeof o === 'object' ? o.value : o;
    const text = typeof o === 'object' ? o.label : prettify(o);
    const sel = String(value) === String(effectiveCurrent) ? ' selected' : '';
    return `<option value="${value}"${sel}>${text}</option>`;
  }).join('');
  group.innerHTML = `
    <label class="form-label">${label}</label>
    <select class="form-input config-select">${opts}</select>
    <span class="form-hint">${hint}</span>
  `;
  const select = group.querySelector('select');
  select.addEventListener('change', () => {
    obj[key] = select.value;
    if (onChange) onChange(select.value);
  });
  return group;
}

function paramGrid(...fields) {
  const grid = el('div', 'tuning-params-grid tuning-params-grid--wide');
  fields.forEach((f) => f && grid.appendChild(f));
  return grid;
}

function prettify(token) {
  return String(token)
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function loadingNode(text = 'Loading configuration…') {
  return el('div', 'loading', text);
}

function fmt(v, unit, def) {
  const value = v != null ? v : def;
  return value != null ? `${value}${unit}` : '—';
}

// ---------------------------------------------------------------------------
// Searchable Home Assistant entity picker (modal)
// ---------------------------------------------------------------------------

function entityFriendlyName(hass, id) {
  return hass?.states?.[id]?.attributes?.friendly_name || id;
}

function openEntityPicker(root, hass, { title, domains, onSelect }) {
  const overlay = el('div', 'ha-modal-overlay');
  const modal = el('div', 'ha-modal');
  modal.innerHTML = `
    <div class="ha-modal__head">
      <span class="ha-modal__title">${title}</span>
      <button class="ha-modal__close" aria-label="Close" type="button">×</button>
    </div>
    <input class="form-input ha-modal__search" type="text" placeholder="Search entities…">
    <div class="ha-modal__list"></div>
  `;
  overlay.appendChild(modal);
  root.appendChild(overlay);

  const searchEl = modal.querySelector('.ha-modal__search');
  const listEl = modal.querySelector('.ha-modal__list');

  const entities = Object.entries(hass?.states || {})
    .filter(([id]) => domains.some((d) => id.startsWith(d + '.')))
    .map(([id, s]) => ({
      id,
      name: s.attributes?.friendly_name || id,
      state: s.state,
      unit: s.attributes?.unit_of_measurement || '',
    }))
    .sort((a, b) => a.name.localeCompare(b.name));

  function close() { overlay.remove(); }

  function draw(filterText) {
    const f = (filterText || '').trim().toLowerCase();
    listEl.innerHTML = '';
    const matches = entities.filter(
      (e) => !f || e.name.toLowerCase().includes(f) || e.id.toLowerCase().includes(f),
    );
    if (matches.length === 0) {
      listEl.appendChild(el('div', 'ha-modal__empty',
        domains.length ? `No ${domains.join(' / ')} entities found.` : 'No entities found.'));
      return;
    }
    matches.slice(0, 400).forEach((e) => {
      const row = el('button', 'ha-modal__row');
      row.type = 'button';
      row.innerHTML = `
        <span class="ha-modal__row-name">${e.name}</span>
        <span class="ha-modal__row-id">${e.id}</span>
        <span class="ha-modal__row-state">${e.state}${e.unit ? ' ' + e.unit : ''}</span>
      `;
      row.addEventListener('click', () => { close(); onSelect(e.id); });
      listEl.appendChild(row);
    });
  }

  modal.querySelector('.ha-modal__close').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  searchEl.addEventListener('input', () => draw(searchEl.value));
  draw('');
  setTimeout(() => searchEl.focus(), 30);
}

// Unified entity selector — one consistent design across the whole config UI.
// Configured entities are shown as chips; a single "Choose…/Add" button opens
// the searchable picker. ``multiple: true`` keeps a list (chips accumulate);
// otherwise it holds a single value (choosing replaces it).
function entitySelectorField(root, hass, obj, key, label, domains, { hint = '', multiple = false, emptyText } = {}) {
  if (multiple && !Array.isArray(obj[key])) obj[key] = [];
  const group = el('div', 'form-group form-group--full');
  group.innerHTML = `
    <div class="config-list-editor__head">
      <span class="form-label">${label}</span>
      <button class="btn btn--secondary btn--sm" type="button" data-role="add"></button>
    </div>
    <div class="entity-chips" data-role="chips"></div>
    <span class="form-hint">${hint}</span>
  `;
  const chips = group.querySelector('[data-role="chips"]');
  const addBtn = group.querySelector('[data-role="add"]');
  const placeholder = emptyText || (multiple ? 'None selected.' : 'No entity selected.');

  function values() {
    if (multiple) return obj[key];
    return obj[key] ? [obj[key]] : [];
  }
  function removeAt(i) {
    if (multiple) obj[key].splice(i, 1);
    else delete obj[key];
    draw();
  }
  function draw() {
    const vals = values();
    addBtn.textContent = multiple ? '+ Add' : (vals.length ? 'Change…' : 'Choose…');
    chips.innerHTML = '';
    if (vals.length === 0) {
      chips.appendChild(el('div', 'config-empty config-empty--inline', placeholder));
      return;
    }
    vals.forEach((id, i) => {
      const chip = el('span', 'entity-chip');
      chip.innerHTML = `<span class="entity-chip__label" title="${escapeAttr(id)}">${entityFriendlyName(hass, id)}</span>`;
      const rm = el('button', 'entity-chip__remove', '×');
      rm.type = 'button';
      rm.title = 'Remove';
      rm.addEventListener('click', () => removeAt(i));
      chip.appendChild(rm);
      chips.appendChild(chip);
    });
  }
  addBtn.addEventListener('click', () => {
    openEntityPicker(root, hass, {
      title: `${multiple ? 'Add' : 'Select'} ${label.toLowerCase()}`,
      domains,
      onSelect: (id) => {
        if (multiple) { if (!obj[key].includes(id)) obj[key].push(id); }
        else { obj[key] = id; }
        draw();
      },
    });
  });
  draw();
  return group;
}

export {
  el,
  escapeAttr,
  schedulePanelNav,
  backNav,
  sectionCard,
  advancedSubsection,
  configListHeader,
  configPageShell,
  actionsBar,
  editorActionsBar,
  setStatus,
  numberField,
  textField,
  selectField,
  paramGrid,
  prettify,
  loadingNode,
  fmt,
  entityFriendlyName,
  openEntityPicker,
  entitySelectorField,
  listEditor,
};
