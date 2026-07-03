// Configuration page — central place to configure everything about the
// Heating Assistant from inside the industrial UI, instead of the native Home
// Assistant config flow.  Sub-pages are reached via the hash:
//
//   #config              → landing (cards)
//   #config/display      → plot / display settings
//   #config/rooms        → room list
//   #config/rooms/<i>    → edit a room (i = index, or "new")
//   #config/sources      → heat-source list
//   #config/sources/<i>  → edit a heat source (i = index, or "new")
//   #config/system       → environment sensors + site location

import { createCollapsible } from '../components/collapsible.js?v=88';
import { setPanelHash } from '../panel-hash.js?v=88';

// ---------------------------------------------------------------------------
// Teal line-art icons (same design language as the panel logo: teal strokes on
// a transparent background, no fills).
// ---------------------------------------------------------------------------

const TEAL = '#00d4aa';

const ICONS = {
  display: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <line x1="20" y1="18" x2="20" y2="80" stroke="${TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.4"/>
    <line x1="20" y1="80" x2="84" y2="80" stroke="${TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.4"/>
    <polyline points="26,66 44,48 58,58 82,28" fill="none" stroke="${TEAL}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="26,74 44,64 58,70 82,52" fill="none" stroke="${TEAL}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2 6" opacity="0.55"/>
  </svg>`,
  rooms: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <path d="M18 84 V46 L50 20 L82 46 V84 Z" fill="none" stroke="${TEAL}" stroke-width="6" stroke-linejoin="round" stroke-linecap="round"/>
    <path d="M42 84 V60 H58 V84" fill="none" stroke="${TEAL}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round" opacity="0.6"/>
  </svg>`,
  sources: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <path d="M52 16 C 66 36, 76 46, 76 60 a26 26 0 0 1 -52 0 C 24 50, 34 48, 38 34 C 46 42, 52 40, 52 16 Z"
      fill="none" stroke="${TEAL}" stroke-width="6" stroke-linejoin="round" stroke-linecap="round"/>
    <path d="M50 78 a12 12 0 0 1 -12 -14 C 42 60, 46 56, 48 48 C 52 56, 62 58, 62 66 a12 12 0 0 1 -12 12 Z"
      fill="none" stroke="${TEAL}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round" opacity="0.55"/>
  </svg>`,
  system: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <circle cx="38" cy="38" r="13" fill="none" stroke="${TEAL}" stroke-width="5"/>
    <g stroke="${TEAL}" stroke-width="4" stroke-linecap="round" opacity="0.7">
      <line x1="38" y1="14" x2="38" y2="8"/>
      <line x1="18" y1="38" x2="12" y2="38"/>
      <line x1="22" y1="22" x2="17" y2="17"/>
      <line x1="54" y1="22" x2="59" y2="17"/>
    </g>
    <path d="M44 78 H72 a13 13 0 0 0 1 -26 a18 18 0 0 0 -34 -2 a13 13 0 0 0 -7 28 Z"
      fill="none" stroke="${TEAL}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`,
  params: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <ellipse cx="50" cy="28" rx="28" ry="10" fill="none" stroke="${TEAL}" stroke-width="5" stroke-linecap="round"/>
    <path d="M22 28 v18 c0 5.5 12.5 10 28 10 s28 -4.5 28 -10 V28" fill="none" stroke="${TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.7"/>
    <path d="M22 46 v18 c0 5.5 12.5 10 28 10 s28 -4.5 28 -10 V46" fill="none" stroke="${TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.45"/>
  </svg>`,
};

// ---------------------------------------------------------------------------
// Categorical thermal-model presets (the visible, required choices).  Precise
// values are identified per-room on the Identification page; these only set a
// sensible baseline.
// ---------------------------------------------------------------------------

const ROOM_SIZE_PRESETS = [
  { value: 'small', label: 'Small room', thermal_mass: 2500000, hint: 'bathroom, small bedroom' },
  { value: 'medium', label: 'Medium room', thermal_mass: 5000000, hint: 'bedroom, office' },
  { value: 'large', label: 'Large room', thermal_mass: 9000000, hint: 'living room' },
  { value: 'open', label: 'Open / open-plan', thermal_mass: 14000000, hint: 'open-plan, hall' },
];

const HOUSE_AGE_PRESETS = [
  { value: 'old', label: 'Old / poorly insulated', r_external: 0.03, tightness: 'leaky' },
  { value: 'standard', label: 'Standard insulation', r_external: 0.05, tightness: 'typical' },
  { value: 'modern', label: 'Modern / well insulated', r_external: 0.08, tightness: 'tight' },
  { value: 'passive', label: 'Passive house', r_external: 0.12, tightness: 'passive_house' },
];

function nearestPreset(presets, key, value) {
  if (value == null) return presets[1] || presets[0];
  let best = presets[0];
  let bestDiff = Infinity;
  for (const p of presets) {
    const d = Math.abs(Number(p[key]) - Number(value));
    if (d < bestDiff) { bestDiff = d; best = p; }
  }
  return best;
}

// ---------------------------------------------------------------------------
// Small DOM builders shared by every sub-page
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Entry point / router
// ---------------------------------------------------------------------------

export function renderConfiguration(container, rooms, state, connection, hass, slug) {
  const parts = (slug || '').split('/').filter(Boolean);
  const page = parts[0] || '';

  if (page === 'display') return renderDisplay(container, connection, hass);
  if (page === 'system') return renderSystem(container, connection, hass);
  if (page === 'params') return renderSystemParams(container, connection, hass);
  if (page === 'rooms' && parts[1] != null) return renderRoomEditor(container, connection, hass, parts[1]);
  if (page === 'rooms') return renderRoomList(container, connection, hass);
  if (page === 'sources' && parts[1] != null) return renderSourceEditor(container, connection, hass, parts[1]);
  if (page === 'sources') return renderSourceList(container, connection, hass);
  return renderLanding(container);
}

// ---------------------------------------------------------------------------
// Landing page — cards linking to each configuration area
// ---------------------------------------------------------------------------

const LANDING_CARDS = [
  {
    hash: '#config/display',
    icon: ICONS.display,
    title: 'Display & Plots',
    desc: 'How much history and forecast the room charts show. Decoupled from the controller horizon.',
  },
  {
    hash: '#config/rooms',
    icon: ICONS.rooms,
    title: 'Rooms',
    desc: 'Thermal model, comfort setpoints, sensors, windows and inter-room connections for each room.',
  },
  {
    hash: '#config/sources',
    icon: ICONS.sources,
    title: 'Heat Sources',
    desc: 'Electric heaters and heat pumps: capacity, efficiency, COP and the entity each one drives.',
  },
  {
    hash: '#config/system',
    icon: ICONS.system,
    title: 'Environment',
    desc: 'Outdoor temperature, weather, solar irradiance and electricity-price sensors.',
  },
  {
    hash: '#config/params',
    icon: ICONS.params,
    title: 'System Parameters',
    desc: 'Data retention, history depth and other system-level settings that control how the integration stores and manages data.',
  },
];

function renderLanding(container) {
  container.innerHTML = '';
  container.appendChild(el('div', 'section-header', 'CONFIGURATION'));
  container.appendChild(el(
    'p', 'config-section__desc',
    'Configure every part of the Heating Assistant here. Changes to rooms or heat sources '
    + 'restart the model so new parameters take effect; display and environment changes apply live.',
  ));

  const grid = el('div', 'config-landing-grid');
  for (const c of LANDING_CARDS) {
    const card = el('div', 'card card--clickable config-landing-card');
    card.innerHTML = `
      <div class="config-landing-card__icon">${c.icon}</div>
      <div class="config-landing-card__body">
        <div class="config-landing-card__title">${c.title}</div>
        <div class="config-landing-card__desc">${c.desc}</div>
      </div>
      <div class="config-landing-card__chevron">›</div>
    `;
    card.addEventListener('click', () => { setPanelHash(c.hash); });
    grid.appendChild(card);
  }
  container.appendChild(grid);
  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Display settings
// ---------------------------------------------------------------------------

function renderDisplay(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    title: 'DISPLAY & PLOTS',
    description: 'How much history and forecast the room charts show. Decoupled from the controller horizon.',
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const ui = (cfg && cfg.ui_settings) || {};
    const working = {
      plot_history_hours: ui.plot_history_hours,
      plot_forecast_hours: ui.plot_forecast_hours,
    };

    body.innerHTML = '';
    const actions = actionsBar('Apply Changes');
    body.appendChild(actions);

    const card = sectionCard(
      'Room chart windows',
      'These control only the industrial dashboard plots — never the controller. '
      + 'The prediction horizon is independent of the MPC controller horizon: if you '
      + 'plot further ahead than the controller plans, the final actuation is held flat '
      + 'and the temperature is simulated forward to fill the window.',
    );
    card.appendChild(paramGrid(
      numberField(working, 'plot_history_hours', 'History window', {
        step: 1, unit: 'h', min: 1,
        hint: 'How far back the measured history is drawn.',
      }),
      numberField(working, 'plot_forecast_hours', 'Forecast horizon', {
        step: 1, unit: 'h', min: 0,
        hint: '0 = match the controller horizon. Larger extends the plot past it.',
      }),
    ));
    body.appendChild(card);

    const statusEl = actions.querySelector('[data-role="status"]');
    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      setStatus(statusEl, 'Applying…', 'running');
      try {
        const data = {};
        if (working.plot_history_hours != null) data.plot_history_hours = Number(working.plot_history_hours);
        if (working.plot_forecast_hours != null) data.plot_forecast_hours = Number(working.plot_forecast_hours);
        await hass.callService('heating_assistant', 'update_ui_settings', data);
        setStatus(statusEl, 'Applied. Reopen a room to see the new window.', 'success');
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
      }
      btn.disabled = false;
    });
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Rooms — list
// ---------------------------------------------------------------------------

function renderRoomList(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    description: 'Thermal model, comfort setpoints, sensors, windows and inter-room connections for each room.',
  });
  container.insertBefore(
    configListHeader('ROOMS', '+ Add Room', () => {
      setPanelHash('#config/rooms/new');
    }),
    body,
  );
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const list = (cfg && cfg.rooms) || [];
    body.innerHTML = '';
    if (list.length === 0) {
      body.appendChild(el('div', 'config-empty',
        'No rooms configured yet. Click <strong>+ Add Room</strong> to create one.'));
    }
    const grid = el('div', 'config-list-grid');
    list.forEach((room, i) => {
      const card = el('div', 'card card--clickable config-list-card');
      const topLevelSources = (cfg.heat_sources || []).filter((s) => s.room === room.name).length;
      const roomLevelSources = (room.heat_sources || []).length;
      const sources = topLevelSources + roomLevelSources;
      const sensorCount = (room.temp_sensors || []).length || (room.temp_sensor ? 1 : 0);
      const connectionCount = (room.connections || []).length;
      card.innerHTML = `
        <div class="config-list-card__name">${room.name || 'Room ' + (i + 1)}</div>
        <div class="config-list-card__meta">
          <span>${sensorCount} sensor(s)</span>
          <span>${(room.windows || []).length} window(s)</span>
          <span>${connectionCount} connection(s)</span>
          <span>${sources} heater(s)</span>
        </div>
        <div class="config-landing-card__chevron">›</div>
      `;
      card.addEventListener('click', () => { setPanelHash(`#config/rooms/${i}`); });
      grid.appendChild(card);
    });
    body.appendChild(grid);
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Rooms — editor
// ---------------------------------------------------------------------------

function renderRoomEditor(container, connection, hass, idxParam) {
  const navTimers = [];
  const page = {
    update() {},
    destroy() { navTimers.forEach((id) => clearTimeout(id)); },
  };

  const isNewInitial = idxParam === 'new';
  const { body } = configPageShell(container, {
    backLabel: 'ROOMS',
    backHash: '#config/rooms',
    title: isNewInitial ? 'NEW ROOM' : 'EDIT ROOM',
    description: LANDING_CARDS.find((c) => c.hash === '#config/rooms').desc,
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const allRooms = (cfg && cfg.rooms) ? cfg.rooms.map((r) => ({ ...r })) : [];
    const enums = (cfg && cfg.enums) || {};
    const tightMap = enums.envelope_tightness_map || {};
    const isNew = idxParam === 'new';
    const idx = isNew ? allRooms.length : Number(idxParam);

    if (!isNew && (Number.isNaN(idx) || idx < 0 || idx >= allRooms.length)) {
      body.innerHTML = '<div class="loading">Room not found.</div>';
      return;
    }

    // Working copy — preserves unknown keys (schedule, etc.) via spread.
    const room = isNew
      ? { name: '', setpoint: 22, comfort_offset: 2.0, windows: [], connections: [], temp_sensors: [] }
      : { ...allRooms[idx] };
    room.windows = room.windows ? room.windows.map((w) => ({ ...w })) : [];
    room.connections = room.connections ? room.connections.map((c) => ({ ...c })) : [];
    // Consolidate temperature sensors into a single averaged list.
    room.temp_sensors = Array.isArray(room.temp_sensors) ? [...room.temp_sensors] : [];
    if (room.temp_sensor && !room.temp_sensors.includes(room.temp_sensor)) {
      room.temp_sensors.unshift(room.temp_sensor);
    }
    delete room.temp_sensor;

    const titleEl = container.querySelector('.section-header');
    if (titleEl) titleEl.textContent = isNew ? 'NEW ROOM' : `EDIT ROOM: ${room.name || ''}`;

    body.innerHTML = '';

    // --- Identity -----------------------------------------------------------
    const idCard = sectionCard('Room identity',
      'The room name is the unique key used across the model and dashboard. The target '
      + 'temperature and comfort band are set per room from its climate card or schedules — '
      + 'not here. New rooms start at a sensible default.');
    idCard.appendChild(paramGrid(
      textField(room, 'name', 'Room name', { placeholder: 'living_room', hint: 'Unique identifier. Changing it on an existing room re-creates its entities.' }),
    ));
    body.appendChild(idCard);

    // --- Sensors ------------------------------------------------------------
    const sensorCard = sectionCard('Temperature sensors',
      'One or more sensors measuring this room. When several are added their readings are '
      + 'averaged in the backend.');
    sensorCard.appendChild(entitySelectorField(container, hass, room, 'temp_sensors',
      'Temperature sensors', ['sensor'],
      { multiple: true, hint: 'Averaged when more than one is selected.', emptyText: 'No temperature sensors — add at least one.' }));
    const sensorAdv = advancedSubsection(sensorCard, 'Window / contact sensors');
    sensorAdv.appendChild(entitySelectorField(container, hass, room, 'window_sensors',
      'Window sensors', ['binary_sensor'],
      { multiple: true, hint: 'Heating pauses for this room while any of these report open.', emptyText: 'No window sensors.' }));
    body.appendChild(sensorCard);

    // --- Thermal model (categorical) ---------------------------------------
    const thermCard = sectionCard('Thermal model',
      'Pick the rough size and construction — these set a baseline. The precise thermal mass '
      + 'and insulation are identified per room on the Identification page.');
    const sizePreset = nearestPreset(ROOM_SIZE_PRESETS, 'thermal_mass', room.thermal_mass);
    const agePreset = nearestPreset(HOUSE_AGE_PRESETS, 'r_external', room.r_external);
    // Seed baseline values so a brand-new room is valid even if untouched.
    if (room.thermal_mass == null) room.thermal_mass = sizePreset.thermal_mass;
    if (room.r_external == null) room.r_external = agePreset.r_external;

    // Forward declarations so the preset onChange handlers can sync the
    // advanced numeric overrides below.
    let tmInput = null;
    let rInput = null;
    let infInput = null;

    const sizeField = selectField(
      { v: sizePreset.value }, 'v', 'Room size',
      ROOM_SIZE_PRESETS.map((p) => ({ value: p.value, label: `${p.label} (${p.hint})` })),
      {
        hint: 'Sets the baseline thermal mass.',
        onChange: (v) => {
          const p = ROOM_SIZE_PRESETS.find((x) => x.value === v);
          if (p) { room.thermal_mass = p.thermal_mass; if (tmInput) tmInput.value = p.thermal_mass; }
        },
      },
    );
    const ageField = selectField(
      { v: agePreset.value }, 'v', 'Construction / insulation',
      HOUSE_AGE_PRESETS.map((p) => ({ value: p.value, label: p.label })),
      {
        hint: 'Sets the baseline insulation and air-tightness.',
        onChange: (v) => {
          const p = HOUSE_AGE_PRESETS.find((x) => x.value === v);
          if (p) {
            room.r_external = p.r_external;
            if (rInput) rInput.value = p.r_external;
            if (tightMap[p.tightness] != null) {
              room.infiltration_fraction = tightMap[p.tightness];
              if (infInput) infInput.value = tightMap[p.tightness];
            }
          }
        },
      },
    );
    thermCard.appendChild(paramGrid(sizeField, ageField));

    const thermAdv = advancedSubsection(thermCard, 'Advanced thermal model');
    const tmGroup = numberField(room, 'thermal_mass', 'Thermal mass (override)', { step: 100000, unit: 'J/K', min: 1000, hint: 'Overrides the size preset.' });
    const rGroup = numberField(room, 'r_external', 'External resistance (override)', { step: 0.005, unit: 'K/W', min: 0.0001, hint: 'Overrides the insulation preset.' });
    const infGroup = numberField(room, 'infiltration_fraction', 'Infiltration fraction', { step: 0.05, min: 0, max: 0.95, hint: 'Share of envelope loss from air leakage.' });
    tmInput = tmGroup.querySelector('input');
    rInput = rGroup.querySelector('input');
    infInput = infGroup.querySelector('input');
    thermAdv.appendChild(paramGrid(
      tmGroup, rGroup, infGroup,
      selectField(room, 'floor_type', 'Floor type', enums.floor_types || ['none'], { def: 'none', hint: 'Slab / underfloor-heating coupling.' }),
    ));
    body.appendChild(thermCard);

    // --- Solar & windows (advanced) ----------------------------------------
    const solarCard = sectionCard('Solar gain',
      'Optional. How much sun this room collects. There are two ways to describe it — '
      + 'pick whichever is easier. Left at defaults, the solar gain is identified from data.');
    const solarAdv = advancedSubsection(solarCard, 'Configure solar & windows');

    solarAdv.appendChild(el('div', 'config-subhead', 'Option A · Quick estimate'));
    solarAdv.appendChild(el('p', 'config-section__desc',
      'Approximate the glazing with a single exposure level and the direction it mostly faces.'));
    solarAdv.appendChild(paramGrid(
      selectField(room, 'solar_exposure', 'Solar exposure', enums.solar_exposures || ['none'], { def: 'none', hint: 'Coarse glazing / aperture amount.' }),
      numberField(room, 'solar_facing', 'Facing direction', { step: 5, unit: '°', min: 0, max: 360, hint: '0=N, 90=E, 180=S, 270=W.' }),
    ));

    solarAdv.appendChild(el('div', 'config-subhead config-subhead--spaced', 'Option B · Individual windows'));
    solarAdv.appendChild(el('p', 'config-section__desc',
      'Enter each window precisely. When any window is listed here it takes precedence over '
      + 'the quick estimate above.'));
    solarAdv.appendChild(listEditor({
      title: 'Windows',
      items: room.windows,
      addLabel: '+ Add window',
      emptyText: 'No individual windows — the quick estimate above is used instead.',
      renderRow: (arr, i) => {
        const w = arr[i];
        const rowEl = el('div', 'config-row tuning-params-grid tuning-params-grid--wide');
        rowEl.appendChild(numberField(w, 'area', 'Area', { step: 0.5, unit: 'm²', min: 0, hint: 'Glazed area.' }));
        rowEl.appendChild(numberField(w, 'orientation', 'Orientation', { step: 5, unit: '°', min: 0, max: 360, hint: '0=N, 90=E, 180=S, 270=W.' }));
        rowEl.appendChild(numberField(w, 'tilt', 'Tilt', { step: 5, unit: '°', min: 0, max: 90, hint: '90 = vertical.' }));
        return rowEl;
      },
      newItem: () => ({ area: 1.0, orientation: 180, tilt: 90 }),
    }));
    body.appendChild(solarCard);

    // --- Connections (advanced) --------------------------------------------
    const otherRooms = allRooms.map((r) => r.name).filter((n) => n && n !== room.name);
    const connCard = sectionCard('Inter-room connections',
      'Optional. Thermal links to adjacent rooms (through internal walls/doors).');
    const connAdv = advancedSubsection(connCard, 'Configure connections');
    connAdv.appendChild(listEditor({
      title: 'Connections',
      items: room.connections,
      addLabel: '+ Add connection',
      emptyText: 'No inter-room connections configured.',
      renderRow: (arr, i) => {
        const c = arr[i];
        const rowEl = el('div', 'config-row tuning-params-grid tuning-params-grid--wide');
        rowEl.appendChild(selectField(c, 'room', 'Connected room',
          otherRooms.length ? otherRooms : [''], { hint: 'Adjacent room.' }));
        rowEl.appendChild(numberField(c, 'r_value', 'R-value', { step: 0.05, unit: 'K/W', min: 0.0001, hint: 'Lower = stronger coupling.' }));
        return rowEl;
      },
      newItem: () => ({ room: otherRooms[0] || '', r_value: 0.2 }),
    }));
    body.appendChild(connCard);

    // --- Advanced envelope --------------------------------------------------
    const envCard = sectionCard('Building envelope',
      'Optional fine corrections, normally identified from data.');
    const envAdv = advancedSubsection(envCard, 'Advanced envelope settings');
    envAdv.appendChild(paramGrid(
      selectField(room, 'facade_colour', 'Facade colour', enums.facade_colours || ['medium'], { def: 'medium', hint: 'Solar absorptance of the opaque facade.' }),
      numberField(room, 'facade_solar_share', 'Facade solar share', { step: 0.05, min: 0, max: 1, hint: 'Sol-air share on the wall node (0 = off).' }),
      numberField(room, 'sky_radiative_ua', 'Sky radiative UA', { step: 0.5, unit: 'W/K', min: 0, hint: 'Clear-night radiative cooling (0 = off).' }),
      numberField(room, 'thermal_bridge_psi_l', 'Thermal bridge', { step: 0.5, unit: 'W/K', min: 0, hint: 'Linear thermal-bridge correction (0 = off).' }),
      numberField(room, 'c_air_fraction', 'Air-mass fraction', { step: 0.01, min: 0, max: 1, hint: 'Fast air node share (~0.05).' }),
      numberField(room, 'r_aw_fraction', 'Air↔wall film fraction', { step: 0.01, min: 0, max: 1, hint: 'Internal film share (~0.05).' }),
    ));
    body.appendChild(envCard);

    // --- Save / delete ------------------------------------------------------
    const actions = editorActionsBar({
      primaryLabel: isNew ? 'Create Room' : 'Save Changes',
      showDelete: !isNew,
      deleteLabel: 'Delete Room',
    });
    body.appendChild(actions);
    const statusEl = actions.querySelector('[data-role="status"]');

    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (!room.name || !String(room.name).trim()) {
        setStatus(statusEl, 'A room name is required.', 'error');
        return;
      }
      if (!Array.isArray(room.temp_sensors) || room.temp_sensors.length === 0) {
        setStatus(statusEl, 'Add at least one temperature sensor.', 'error');
        return;
      }
      btn.disabled = true;
      setStatus(statusEl, 'Saving… (the model will restart)', 'running');
      try {
        const cleaned = cleanRoom(room);
        const next = allRooms.map((r) => ({ ...r }));
        if (isNew) next.push(cleaned); else next[idx] = cleaned;
        const payload = { rooms: next };
        // On a rename, tell the backend so it migrates all data (persisted
        // state, heat-source links, connections, entities) to the new name.
        const originalName = isNew ? null : allRooms[idx].name;
        if (originalName && originalName !== cleaned.name) {
          payload.renames = { [originalName]: cleaned.name };
        }
        await hass.callService('heating_assistant', 'update_rooms', payload);
        setStatus(statusEl, 'Saved. Restarting model…', 'success');
        navTimers.push(schedulePanelNav('#config/rooms', 800));
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
        btn.disabled = false;
      }
    });

    const delBtn = actions.querySelector('[data-role="delete"]');
    if (delBtn) {
      delBtn.addEventListener('click', async () => {
        if (!window.confirm(`Delete room "${room.name}"? Heat sources assigned to it must be reassigned or removed.`)) return;
        delBtn.disabled = true;
        setStatus(statusEl, 'Deleting…', 'running');
        try {
          const next = allRooms.filter((_, i) => i !== idx).map((r) => ({ ...r }));
          await hass.callService('heating_assistant', 'update_rooms', { rooms: next });
          setStatus(statusEl, 'Deleted. Restarting model…', 'success');
          navTimers.push(schedulePanelNav('#config/rooms', 800));
        } catch (err) {
          setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
          delBtn.disabled = false;
        }
      });
    }
  });

  return page;
}

// Strip empty/incomplete sub-records so backend validation passes.
function cleanRoom(room) {
  const out = { ...room };
  out.name = String(out.name).trim();
  if (Array.isArray(out.windows)) {
    out.windows = out.windows
      .filter((w) => w && Number(w.area) > 0 && w.orientation != null)
      .map((w) => ({ area: Number(w.area), orientation: Number(w.orientation), tilt: Number(w.tilt != null ? w.tilt : 90) }));
    if (out.windows.length === 0) delete out.windows;
  }
  if (Array.isArray(out.connections)) {
    out.connections = out.connections
      .filter((c) => c && c.room && Number(c.r_value) > 0)
      .map((c) => ({ room: c.room, r_value: Number(c.r_value) }));
    if (out.connections.length === 0) delete out.connections;
  }
  if (Array.isArray(out.temp_sensors)) {
    out.temp_sensors = out.temp_sensors.map((s) => String(s).trim()).filter(Boolean);
    if (out.temp_sensors.length === 0) delete out.temp_sensors;
  }
  if (Array.isArray(out.window_sensors)) {
    out.window_sensors = out.window_sensors.map((s) => String(s).trim()).filter(Boolean);
    if (out.window_sensors.length === 0) delete out.window_sensors;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Heat sources — list
// ---------------------------------------------------------------------------

function renderSourceList(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    description: 'Electric heaters and heat pumps: capacity, efficiency, COP and the entity each one drives.',
  });
  container.insertBefore(
    configListHeader('HEAT SOURCES', '+ Add Heat Source', () => {
      setPanelHash('#config/sources/new');
    }),
    body,
  );
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const list = (cfg && cfg.heat_sources) || [];
    body.innerHTML = '';
    if (list.length === 0) {
      body.appendChild(el('div', 'config-empty',
        'No heat sources configured yet. Click <strong>+ Add Heat Source</strong> to create one.'));
    }
    const grid = el('div', 'config-list-grid');
    list.forEach((src, i) => {
      const card = el('div', 'card card--clickable config-list-card');
      card.innerHTML = `
        <div class="config-list-card__name">${src.name || 'Source ' + (i + 1)}</div>
        <div class="config-list-card__meta">
          <span>${prettify(src.type || 'electric_heater')}</span>
          <span>Room: ${src.room || '—'}</span>
          <span>${fmt(src.max_power, ' W', '—')}</span>
        </div>
        <div class="config-landing-card__chevron">›</div>
      `;
      card.addEventListener('click', () => { setPanelHash(`#config/sources/${i}`); });
      grid.appendChild(card);
    });
    body.appendChild(grid);
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Heat sources — editor (sections driven by HVAC mode)
// ---------------------------------------------------------------------------

function renderSourceEditor(container, connection, hass, idxParam) {
  const navTimers = [];
  const page = {
    update() {},
    destroy() { navTimers.forEach((id) => clearTimeout(id)); },
  };

  const isNewInitial = idxParam === 'new';
  const { body } = configPageShell(container, {
    backLabel: 'HEAT SOURCES',
    backHash: '#config/sources',
    title: isNewInitial ? 'NEW HEAT SOURCE' : 'EDIT SOURCE',
    description: LANDING_CARDS.find((c) => c.hash === '#config/sources').desc,
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const allSources = (cfg && cfg.heat_sources) ? cfg.heat_sources.map((s) => ({ ...s })) : [];
    const roomNames = ((cfg && cfg.rooms) || []).map((r) => r.name).filter(Boolean);
    const enums = (cfg && cfg.enums) || {};
    const isNew = idxParam === 'new';
    const idx = isNew ? allSources.length : Number(idxParam);

    if (!isNew && (Number.isNaN(idx) || idx < 0 || idx >= allSources.length)) {
      body.innerHTML = '<div class="loading">Heat source not found.</div>';
      return;
    }

    const src = isNew
      ? { name: '', type: 'electric_heater', room: roomNames[0] || '', max_power: 2000, hvac_mode: 'heat_cool' }
      : { ...allSources[idx] };

    const titleEl = container.querySelector('.section-header');
    if (titleEl) titleEl.textContent = isNew ? 'NEW HEAT SOURCE' : `EDIT SOURCE: ${src.name || ''}`;

    body.innerHTML = '';

    // Dynamic body re-renders when type or HVAC mode changes (so the heating /
    // cooling sections appear and disappear with the selected mode).
    const dynamic = el('div');
    body.appendChild(dynamic);

    const HP_TYPES = ['heat_pump', 'ground_source_heat_pump'];
    function modeIncludes(part) {
      if (!HP_TYPES.includes(src.type)) return part === 'heat';
      const m = src.hvac_mode || 'heat_cool';
      return m === 'heat_cool' || m === part;
    }

    function renderFields() {
      dynamic.innerHTML = '';

      // ── Shared settings ──────────────────────────────────────────────────
      const sharedCard = sectionCard('Shared settings',
        'Identity, placement, and the entity the controller commands.');
      const typeField = selectField(src, 'type', 'Type',
        enums.source_types || ['electric_heater', 'heat_pump', 'ground_source_heat_pump',
          'gas_heater', 'oil_boiler', 'hydronic_radiator', 'hydronic_floor_heating',
          'electric_floor_heating', 'oil_radiator', 'pellet_stove',
          'electric_storage_heater', 'generic_thermostat'],
        { hint: 'Type of heat source.', onChange: () => renderFields() });
      const sharedFields = [
        textField(src, 'name', 'Name', { placeholder: 'living_room_heater', hint: 'Unique identifier.' }),
        typeField,
        selectField(src, 'room', 'Room', roomNames.length ? roomNames : [''], { hint: 'Room this source heats.' }),
        entitySelectorField(container, hass, src, 'heater_entity', 'Driven entity',
          HP_TYPES.includes(src.type) ? ['climate'] : ['switch', 'input_boolean', 'climate', 'number'],
          { hint: 'Entity the controller commands.' }),
      ];
      if (HP_TYPES.includes(src.type)) {
        sharedFields.push(selectField(src, 'hvac_mode', 'HVAC mode',
          enums.hvac_modes || ['heat', 'cool', 'heat_cool'],
          { def: 'heat_cool', hint: 'Determines which settings below apply.', onChange: () => renderFields() }));
      }
      sharedCard.appendChild(paramGrid(...sharedFields));
      const sharedAdv = advancedSubsection(sharedCard, 'Advanced');
      sharedAdv.appendChild(paramGrid(
        numberField(src, 'emitter_time_constant', 'Emitter time constant', { step: 30, unit: 's', min: 0, hint: '0 for electric; ~600 for hydronic radiators.' }),
      ));
      dynamic.appendChild(sharedCard);

      // ── Heating settings ─────────────────────────────────────────────────
      if (modeIncludes('heat')) {
        if (src.type === 'heat_pump') {
          const heatCard = sectionCard('Heating settings',
            'Enter the unit as it appears on the datasheet: the electrical power it draws '
            + 'and its COP. The heat output is derived (power input × COP).');
          // Virtual datasheet fields → stored as thermal max_power + cop_rated.
          const hp = {
            power_input: (src.max_power != null && src.cop_rated != null)
              ? Math.round(src.max_power / src.cop_rated) : 1300,
            cop: src.cop_rated != null ? src.cop_rated : 3.5,
          };
          const derived = el('span', 'config-derived');
          const setDerived = () => {
            derived.textContent = `Rated heat output ≈ ${src.max_power != null ? src.max_power : '—'} W (power input × COP).`;
          };
          const syncHeat = () => {
            const pin = Number(hp.power_input) || 0;
            const cop = Number(hp.cop) || 0;
            src.cop_rated = cop;
            src.max_power = Math.round(pin * cop);
            setDerived();
          };
          heatCard.appendChild(paramGrid(
            numberField(hp, 'power_input', 'Rated power input', { step: 50, unit: 'W', min: 0, onChange: syncHeat, hint: 'Electrical power the unit draws at full load (datasheet "power input").' }),
            numberField(hp, 'cop', 'Rated COP (heating)', { step: 0.1, min: 1, onChange: syncHeat, hint: 'Heating COP at the reference outdoor temperature.' }),
            numberField(src, 'min_power', 'Minimum heat output', { step: 100, unit: 'W', min: 0, hint: 'Lowest thermal output the unit modulates to (0 = none).' }),
          ));
          heatCard.appendChild(derived);
          // Seed stored values when missing/inconsistent; otherwise keep them exact.
          if (src.cop_rated == null || src.max_power == null) syncHeat(); else setDerived();
          const heatAdv = advancedSubsection(heatCard, 'Advanced');
          heatAdv.appendChild(paramGrid(
            numberField(src, 'cop_temp_ref', 'COP reference temp', { step: 1, unit: '°C', hint: 'Outdoor temp the rated COP is measured at (datasheet point, e.g. 7 °C).' }),
            numberField(src, 'max_temp_offset', 'Max temp offset', { step: 0.5, unit: '°C', min: 0, hint: 'Setpoint offset at full power.' }),
          ));
          dynamic.appendChild(heatCard);
        } else if (src.type === 'ground_source_heat_pump') {
          const heatCard = sectionCard('Heating settings',
            'Ground-source heat pumps have a near-constant COP year-round because '
            + 'the ground loop temperature barely changes with seasons.');
          const gshp = {
            power_input: (src.max_power != null && src.cop_rated != null)
              ? Math.round(src.max_power / src.cop_rated) : 1300,
            cop: src.cop_rated != null ? src.cop_rated : 4.5,
          };
          const derived = el('span', 'config-derived');
          const setDerived = () => {
            derived.textContent = `Rated heat output ≈ ${src.max_power != null ? src.max_power : '—'} W (power input × COP).`;
          };
          const syncHeat = () => {
            const pin = Number(gshp.power_input) || 0;
            const cop = Number(gshp.cop) || 0;
            src.cop_rated = cop;
            src.max_power = Math.round(pin * cop);
            setDerived();
          };
          heatCard.appendChild(paramGrid(
            numberField(gshp, 'power_input', 'Rated power input', { step: 50, unit: 'W', min: 0, onChange: syncHeat, hint: 'Electrical power the unit draws at full load.' }),
            numberField(gshp, 'cop', 'Rated COP (flat)', { step: 0.1, min: 1, onChange: syncHeat, hint: 'Heating COP — constant, not outdoor-temperature-dependent.' }),
            numberField(src, 'min_power', 'Minimum heat output', { step: 100, unit: 'W', min: 0, hint: 'Lowest thermal output the unit modulates to (0 = none).' }),
          ));
          heatCard.appendChild(derived);
          if (src.cop_rated == null || src.max_power == null) syncHeat(); else setDerived();
          const heatAdv = advancedSubsection(heatCard, 'Advanced');
          heatAdv.appendChild(paramGrid(
            numberField(src, 'max_temp_offset', 'Max temp offset', { step: 0.5, unit: '°C', min: 0, hint: 'Setpoint offset at full power.' }),
          ));
          dynamic.appendChild(heatCard);
        } else if (src.type === 'pellet_stove') {
          const heatCard = sectionCard('Heating settings',
            'Pellet stoves burn biomass and have a minimum firing level below which '
            + 'they shut off entirely. Heat output = rated power × efficiency × fraction.');
          heatCard.appendChild(paramGrid(
            numberField(src, 'max_power', 'Rated fuel power', { step: 100, unit: 'W', min: 0, hint: 'Nominal thermal input power at full load.' }),
            numberField(src, 'efficiency', 'Combustion efficiency', { step: 0.01, min: 0.5, max: 1, hint: 'Fraction of fuel energy delivered as heat (0.85–0.93 typical).' }),
            numberField(src, 'min_power_fraction', 'Min firing fraction', { step: 0.05, min: 0.1, max: 0.9, hint: 'Fraction of rated power below which the stove shuts off (default 0.30).' }),
          ));
          dynamic.appendChild(heatCard);
        } else if (src.type === 'electric_storage_heater') {
          const heatCard = sectionCard('Heating settings',
            'Storage heaters charge overnight (charge_power) and release heat passively '
            + 'during the day. The boost coil (max_power) provides real-time supplemental heat.');
          heatCard.appendChild(paramGrid(
            numberField(src, 'max_power', 'Boost power', { step: 100, unit: 'W', min: 0, hint: 'Real-time boost coil power (separate from stored heat).' }),
            numberField(src, 'charge_power', 'Charge power', { step: 100, unit: 'W', min: 0, hint: 'Electrical power drawn during overnight charging.' }),
            numberField(src, 'storage_capacity_kwh', 'Storage capacity', { step: 0.5, unit: 'kWh', min: 0, hint: 'Maximum storable heat energy (default 8 kWh).' }),
          ));
          dynamic.appendChild(heatCard);
        } else {
          const heatCard = sectionCard('Heating settings',
            'Thermal output = rated power × efficiency.');
          heatCard.appendChild(paramGrid(
            numberField(src, 'max_power', 'Rated power', { step: 100, unit: 'W', min: 0, hint: 'Thermal (or fuel) power at full output.' }),
            numberField(src, 'efficiency', 'Efficiency', { step: 0.05, min: 0, max: 1, hint: 'Fraction of input energy delivered as heat (1.0 for resistive heaters).' }),
          ));
          dynamic.appendChild(heatCard);
        }
      }

      // ── Cooling settings ─────────────────────────────────────────────────
      if (modeIncludes('cool')) {
        const coolCard = sectionCard('Cooling settings',
          'Cooling COP (EER) is max cooling power ÷ electrical power input. If your datasheet '
          + 'lists a max cooling power and a power input, divide them (e.g. 5000 W ÷ 1600 W ≈ 3.1).');
        const eff = () => (src.cooling_efficiency != null ? Number(src.cooling_efficiency) : 1);
        if (!modeIncludes('heat')) {
          // Cool-only: there is no heating section, so capture the electrical
          // input here. Stored as max_power with cop_rated = 1 so the model's
          // electrical input equals the entered value.
          const co = {
            // Recover electrical input: divide by cop_rated when it carries a
            // heating COP (switched from heat/cool); cool-only stores cop_rated=1.
            power_input: (src.max_power != null && src.cop_rated)
              ? Math.round(src.max_power / src.cop_rated)
              : (src.max_power != null ? Math.round(src.max_power) : 1300),
            cop: src.cooling_cop != null ? src.cooling_cop : 2.5,
          };
          const derived = el('span', 'config-derived');
          const setDerived = () => {
            const pin = Number(co.power_input) || 0;
            derived.textContent = `Max cooling power ≈ ${Math.round(pin * (Number(co.cop) || 0) * eff())} W (power input × EER).`;
          };
          const syncCool = () => {
            src.cop_rated = 1;
            src.max_power = Math.round(Number(co.power_input) || 0);
            src.cooling_cop = Number(co.cop) || 0;
            setDerived();
          };
          coolCard.appendChild(paramGrid(
            numberField(co, 'power_input', 'Rated power input', { step: 50, unit: 'W', min: 0, onChange: syncCool, hint: 'Electrical power drawn while cooling.' }),
            numberField(co, 'cop', 'Cooling COP (EER)', { step: 0.1, min: 0, onChange: syncCool, hint: 'Max cooling power ÷ power input.' }),
          ));
          coolCard.appendChild(derived);
          syncCool();
          const coolAdv = advancedSubsection(coolCard, 'Advanced');
          coolAdv.appendChild(paramGrid(
            numberField(src, 'cooling_efficiency', 'Cooling efficiency', { step: 0.05, min: 0, max: 1, onChange: setDerived, hint: 'Fraction of cooling capacity used (1.0 = full).' }),
          ));
          dynamic.appendChild(coolCard);
        } else {
          // Reversible unit: cooling capacity is derived from the same electrical
          // input as heating, so only the cooling COP (EER) is needed here.
          if (src.cooling_cop == null) src.cooling_cop = 2.5;
          const pinHeat = (src.max_power && src.cop_rated) ? src.max_power / src.cop_rated : 0;
          const derived = el('span', 'config-derived');
          const setDerived = () => {
            derived.textContent = pinHeat > 0
              ? `Max cooling power ≈ ${Math.round(pinHeat * (Number(src.cooling_cop) || 0) * eff())} W (heating power input × EER).`
              : '';
          };
          coolCard.appendChild(paramGrid(
            numberField(src, 'cooling_cop', 'Cooling COP (EER)', { step: 0.1, min: 0, onChange: setDerived, hint: 'Uses the heating power input above as the electrical input.' }),
          ));
          coolCard.appendChild(derived);
          setDerived();
          const coolAdv = advancedSubsection(coolCard, 'Advanced');
          coolAdv.appendChild(paramGrid(
            numberField(src, 'cooling_efficiency', 'Cooling efficiency', { step: 0.05, min: 0, max: 1, onChange: setDerived, hint: 'Fraction of cooling capacity used (1.0 = full).' }),
          ));
          dynamic.appendChild(coolCard);
        }
      }
    }

    renderFields();

    const actions = editorActionsBar({
      primaryLabel: isNew ? 'Create Source' : 'Save Changes',
      showDelete: !isNew,
      deleteLabel: 'Delete Source',
    });
    body.appendChild(actions);
    const statusEl = actions.querySelector('[data-role="status"]');

    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (!src.name || !String(src.name).trim()) { setStatus(statusEl, 'A name is required.', 'error'); return; }
      if (!src.room) { setStatus(statusEl, 'A room must be selected.', 'error'); return; }
      btn.disabled = true;
      setStatus(statusEl, 'Saving… (the model will restart)', 'running');
      try {
        const cleaned = cleanSource(src);
        const next = allSources.map((s) => ({ ...s }));
        if (isNew) next.push(cleaned); else next[idx] = cleaned;
        await hass.callService('heating_assistant', 'update_heat_sources', { heat_sources: next });
        setStatus(statusEl, 'Saved. Restarting model…', 'success');
        navTimers.push(schedulePanelNav('#config/sources', 800));
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
        btn.disabled = false;
      }
    });

    const delBtn = actions.querySelector('[data-role="delete"]');
    if (delBtn) {
      delBtn.addEventListener('click', async () => {
        if (!window.confirm(`Delete heat source "${src.name}"?`)) return;
        delBtn.disabled = true;
        setStatus(statusEl, 'Deleting…', 'running');
        try {
          const next = allSources.filter((_, i) => i !== idx).map((s) => ({ ...s }));
          await hass.callService('heating_assistant', 'update_heat_sources', { heat_sources: next });
          setStatus(statusEl, 'Deleted. Restarting model…', 'success');
          navTimers.push(schedulePanelNav('#config/sources', 800));
        } catch (err) {
          setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
          delBtn.disabled = false;
        }
      });
    }
  });

  return page;
}

function cleanSource(src) {
  const out = { ...src };
  out.name = String(out.name).trim();
  out.max_power = Number(out.max_power || 0);
  const HP_TYPES = ['heat_pump', 'ground_source_heat_pump'];
  // Drop keys irrelevant to the chosen type/mode so stale values don't linger.
  if (!HP_TYPES.includes(out.type)) {
    ['cop_rated', 'cop_temp_ref', 'min_power', 'max_temp_offset', 'hvac_mode',
      'cooling_cop', 'cooling_efficiency', 'heating_efficiency'].forEach((k) => delete out[k]);
  } else {
    delete out.efficiency;
    const m = out.hvac_mode || 'heat_cool';
    if (m === 'cool') {
      ['cop_temp_ref', 'min_power', 'max_temp_offset'].forEach((k) => delete out[k]);
    }
    if (m === 'heat') {
      ['cooling_cop', 'cooling_efficiency'].forEach((k) => delete out[k]);
    }
    // GSHP has no outdoor-temp COP correction, so cop_temp_ref is unused.
    if (out.type === 'ground_source_heat_pump') delete out.cop_temp_ref;
  }
  // Strip type-specific keys that don't belong to other types.
  if (out.type !== 'pellet_stove') delete out.min_power_fraction;
  if (out.type !== 'electric_storage_heater') {
    ['charge_power', 'storage_capacity_kwh', 'passive_discharge_rate'].forEach((k) => delete out[k]);
  }
  return out;
}

// ---------------------------------------------------------------------------
// System Parameters
// ---------------------------------------------------------------------------

function renderSystemParams(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    title: 'SYSTEM PARAMETERS',
    description: 'Data retention, history depth and other system-level settings that control how the integration stores and manages data.',
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const sp = (cfg && cfg.system_params) || {};
    const working = {
      identification_history_days: sp.identification_history_days,
    };

    body.innerHTML = '';
    const actions = actionsBar('Apply Changes');
    body.appendChild(actions);

    const histCard = sectionCard(
      'Identification history',
      'Controls how much past observation data the integration keeps on disk. '
      + 'Each day of operation produces one JSONL file; files older than the '
      + 'retention window are deleted automatically once per day. '
      + 'Longer retention means more data is available for system identification, '
      + 'at the cost of a small amount of extra storage (roughly 1–2 MB per day).',
    );
    histCard.appendChild(paramGrid(
      numberField(working, 'identification_history_days', 'History retention', {
        step: 1, unit: 'days', min: 7,
        hint: 'How many days of JSONL observation files to keep. Default: 90.',
      }),
    ));
    body.appendChild(histCard);

    const statusEl = actions.querySelector('[data-role="status"]');
    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      setStatus(statusEl, 'Applying…', 'running');
      try {
        const data = {};
        if (working.identification_history_days != null) {
          data.identification_history_days = Math.round(Number(working.identification_history_days));
        }
        await hass.callService('heating_assistant', 'update_system_params', data);
        setStatus(statusEl, 'Applied.', 'success');
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
      }
      btn.disabled = false;
    });
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Environment & Site
// ---------------------------------------------------------------------------

function renderSystem(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    title: 'ENVIRONMENT',
    description: 'Outdoor temperature, weather, solar irradiance and electricity-price sensors.',
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const sys = (cfg && cfg.system) || {};
    const working = { ...sys };

    body.innerHTML = '';
    const actions = actionsBar('Apply Changes');
    body.appendChild(actions);

    const envCard = sectionCard('Weather & energy sensors',
      'External signals the controller reads. Outdoor temperature is required for good control; '
      + 'weather and solar irradiance improve the forecast; the price sensor enables price-aware '
      + 'optimisation. Use Clear to disable any of them.');
    envCard.appendChild(paramGrid(
      entitySelectorField(container, hass, working, 'outdoor_temp_entity', 'Outdoor temperature', ['sensor'], { hint: 'Measured outdoor air temperature.' }),
      entitySelectorField(container, hass, working, 'weather_entity', 'Weather forecast', ['weather'], { hint: 'Weather entity for the outdoor forecast.' }),
      entitySelectorField(container, hass, working, 'solar_radiation_entity', 'Solar irradiance', ['sensor'], { hint: 'GHI in W/m² (optional).' }),
      entitySelectorField(container, hass, working, 'price_entity', 'Electricity price', ['sensor'], { hint: 'Hourly market price (optional).' }),
    ));
    body.appendChild(envCard);

    const statusEl = actions.querySelector('[data-role="status"]');
    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      setStatus(statusEl, 'Applying…', 'running');
      try {
        const data = {
          outdoor_temp_entity: working.outdoor_temp_entity || '',
          weather_entity: working.weather_entity || '',
          solar_radiation_entity: working.solar_radiation_entity || '',
          price_entity: working.price_entity || '',
        };
        await hass.callService('heating_assistant', 'update_system_config', data);
        setStatus(statusEl, 'Applied.', 'success');
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
      }
      btn.disabled = false;
    });
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Generic add/remove list editor used for windows and connections
// ---------------------------------------------------------------------------

function listEditor({ title, items, addLabel, emptyText, renderRow, newItem }) {
  const wrap = el('div', 'config-list-editor');
  const head = el('div', 'config-list-editor__head');
  head.innerHTML = `
    <span class="config-list-editor__title">${title}</span>
    <button class="btn btn--secondary btn--sm" type="button" data-role="add">${addLabel}</button>
  `;
  wrap.appendChild(head);
  const rowsWrap = el('div', 'config-list-editor__rows');
  wrap.appendChild(rowsWrap);

  function draw() {
    rowsWrap.innerHTML = '';
    if (items.length === 0) {
      rowsWrap.appendChild(el('div', 'config-empty', emptyText));
      return;
    }
    items.forEach((_, i) => {
      const rowCard = el('div', 'config-item-row');
      rowCard.appendChild(renderRow(items, i));
      const del = el('button', 'schedule-form__delete', '×');
      del.type = 'button';
      del.title = 'Remove';
      del.addEventListener('click', () => { items.splice(i, 1); draw(); });
      rowCard.appendChild(del);
      rowsWrap.appendChild(rowCard);
    });
  }

  head.querySelector('[data-role="add"]').addEventListener('click', () => {
    items.push(newItem());
    draw();
  });

  draw();
  return wrap;
}
