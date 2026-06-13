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

// ---------------------------------------------------------------------------
// Small DOM builders shared by every sub-page
// ---------------------------------------------------------------------------

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}

function backNav(label, hash) {
  const nav = el('button', 'nav-back');
  nav.innerHTML = `<span class="nav-back__arrow">←</span> ${label}`;
  nav.addEventListener('click', () => { window.location.hash = hash; });
  return nav;
}

function sectionCard(title, desc) {
  const card = el('div', 'card config-section');
  if (title) card.appendChild(el('div', 'config-section__title', title));
  if (desc) card.appendChild(el('p', 'config-section__desc', desc));
  return card;
}

function actionsBar(primaryLabel) {
  const row = el('div', 'tuning-actions');
  row.innerHTML = `
    <button class="btn btn--primary tuning-actions__btn" data-role="save">${primaryLabel}</button>
    <span class="tuning-actions__status" data-role="status"></span>
  `;
  return row;
}

function setStatus(statusEl, text, type = '') {
  statusEl.textContent = text;
  statusEl.className = 'tuning-actions__status';
  if (type) statusEl.classList.add(`tuning-actions__status--${type}`);
}

// A labelled numeric field bound to obj[key]. Empty input deletes the key so
// the backend default applies.
function numberField(obj, key, label, { step = 1, unit = '', hint = '', min, max } = {}) {
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
    if (input.value === '') { delete obj[key]; return; }
    obj[key] = Number(input.value);
  });
  return group;
}

function textField(obj, key, label, { hint = '', placeholder = '' } = {}) {
  const group = el('div', 'form-group');
  group.innerHTML = `
    <label class="form-label">${label}</label>
    <input class="form-input" type="text" placeholder="${placeholder}"
      value="${obj[key] != null ? String(obj[key]).replace(/"/g, '&quot;') : ''}">
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

function selectField(obj, key, label, options, { hint = '', def } = {}) {
  const group = el('div', 'form-group');
  const current = obj[key] != null ? obj[key] : def;
  const opts = options.map((o) => {
    const value = typeof o === 'object' ? o.value : o;
    const text = typeof o === 'object' ? o.label : prettify(o);
    const sel = String(value) === String(current) ? ' selected' : '';
    return `<option value="${value}"${sel}>${text}</option>`;
  }).join('');
  group.innerHTML = `
    <label class="form-label">${label}</label>
    <select class="form-input config-select">${opts}</select>
    <span class="form-hint">${hint}</span>
  `;
  const select = group.querySelector('select');
  select.addEventListener('change', () => { obj[key] = select.value; });
  return group;
}

// Entity picker: free-text input backed by a datalist of matching entity ids.
function entityField(hass, obj, key, label, domains, { hint = '' } = {}) {
  const group = el('div', 'form-group');
  const listId = `dl-${key}-${Math.random().toString(36).slice(2, 8)}`;
  const ids = Object.keys(hass?.states || {})
    .filter((id) => domains.some((d) => id.startsWith(d + '.')))
    .sort();
  const optionsHtml = ids.map((id) => {
    const name = hass.states[id]?.attributes?.friendly_name;
    return `<option value="${id}">${name ? name : id}</option>`;
  }).join('');
  group.innerHTML = `
    <label class="form-label">${label}</label>
    <input class="form-input" type="text" list="${listId}" placeholder="(none)"
      value="${obj[key] != null ? String(obj[key]).replace(/"/g, '&quot;') : ''}">
    <datalist id="${listId}">${optionsHtml}</datalist>
    <span class="form-hint">${hint || `Pick a ${domains.join(' / ')} entity, or leave blank.`}</span>
  `;
  const input = group.querySelector('input');
  input.addEventListener('change', () => {
    const v = input.value.trim();
    if (v === '') { delete obj[key]; return; }
    obj[key] = v;
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

// ---------------------------------------------------------------------------
// Entry point / router
// ---------------------------------------------------------------------------

export function renderConfiguration(container, rooms, state, connection, hass, slug) {
  const parts = (slug || '').split('/').filter(Boolean);
  const page = parts[0] || '';

  if (page === 'display') return renderDisplay(container, connection, hass);
  if (page === 'system') return renderSystem(container, connection, hass);
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
    icon: '📈',
    title: 'Display & Plots',
    desc: 'How much history and forecast the room charts show. Decoupled from the controller horizon.',
  },
  {
    hash: '#config/rooms',
    icon: '🏠',
    title: 'Rooms',
    desc: 'Thermal model, comfort setpoints, sensors, windows and inter-room connections for each room.',
  },
  {
    hash: '#config/sources',
    icon: '🔥',
    title: 'Heat Sources',
    desc: 'Electric heaters and heat pumps: capacity, efficiency, COP and the entity each one drives.',
  },
  {
    hash: '#config/system',
    icon: '🌤️',
    title: 'Environment & Site',
    desc: 'Outdoor temperature, weather, solar irradiance and electricity-price sensors, plus site location.',
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
    card.addEventListener('click', () => { window.location.hash = c.hash; });
    grid.appendChild(card);
  }
  container.appendChild(grid);
  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Display settings
// ---------------------------------------------------------------------------

function renderDisplay(container, connection, hass) {
  container.innerHTML = '';
  container.appendChild(backNav('CONFIGURATION', '#config'));
  container.appendChild(el('div', 'section-header', 'DISPLAY & PLOTS'));

  const body = el('div');
  body.appendChild(loadingNode());
  container.appendChild(body);

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
        step: 1, unit: 'h', min: 1, max: 168,
        hint: 'How far back the measured history is drawn.',
      }),
      numberField(working, 'plot_forecast_hours', 'Forecast horizon', {
        step: 1, unit: 'h', min: 0, max: 168,
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
  container.innerHTML = '';
  container.appendChild(backNav('CONFIGURATION', '#config'));

  const header = el('div', 'sched-detail__section-header');
  header.innerHTML = `
    <span class="section-header" style="margin:0;border:0;padding:0;">ROOMS</span>
    <button class="btn btn--primary btn--sm" data-role="add">+ Add Room</button>
  `;
  container.appendChild(header);

  const body = el('div');
  body.appendChild(loadingNode());
  container.appendChild(body);

  header.querySelector('[data-role="add"]').addEventListener('click', () => {
    window.location.hash = '#config/rooms/new';
  });

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
      const sources = (cfg.heat_sources || []).filter((s) => s.room === room.name).length;
      card.innerHTML = `
        <div class="config-list-card__name">${room.name || 'Room ' + (i + 1)}</div>
        <div class="config-list-card__meta">
          <span>Setpoint ${fmt(room.setpoint, '°C', 22)}</span>
          <span>R ${fmt(room.r_external, 'K/W', 0.05)}</span>
          <span>${(room.windows || []).length} window(s)</span>
          <span>${sources} heater(s)</span>
        </div>
        <div class="config-landing-card__chevron">›</div>
      `;
      card.addEventListener('click', () => { window.location.hash = `#config/rooms/${i}`; });
      grid.appendChild(card);
    });
    body.appendChild(grid);
  });

  return { update() {}, destroy() {} };
}

function fmt(v, unit, def) {
  const value = v != null ? v : def;
  return value != null ? `${value}${unit}` : '—';
}

// ---------------------------------------------------------------------------
// Rooms — editor
// ---------------------------------------------------------------------------

function renderRoomEditor(container, connection, hass, idxParam) {
  container.innerHTML = '';
  container.appendChild(backNav('ROOMS', '#config/rooms'));
  const body = el('div');
  body.appendChild(loadingNode());
  container.appendChild(body);

  connection.getModelConfig().then((cfg) => {
    const allRooms = (cfg && cfg.rooms) ? cfg.rooms.map((r) => ({ ...r })) : [];
    const enums = (cfg && cfg.enums) || {};
    const isNew = idxParam === 'new';
    const idx = isNew ? allRooms.length : Number(idxParam);

    if (!isNew && (Number.isNaN(idx) || idx < 0 || idx >= allRooms.length)) {
      body.innerHTML = '<div class="loading">Room not found.</div>';
      return;
    }

    // Working copy — preserves unknown keys (schedule, etc.) via spread.
    const room = isNew
      ? { name: '', setpoint: 22, comfort_offset: 2.0, windows: [], connections: [] }
      : { ...allRooms[idx] };
    room.windows = room.windows ? room.windows.map((w) => ({ ...w })) : [];
    room.connections = room.connections ? room.connections.map((c) => ({ ...c })) : [];

    body.innerHTML = '';
    body.appendChild(el('div', 'section-header', isNew ? 'NEW ROOM' : `EDIT ROOM: ${room.name || ''}`));

    // --- Identity & comfort -------------------------------------------------
    const idCard = sectionCard('Identity & comfort',
      'The room name is the unique key used across the model and dashboard. Setpoint is the '
      + 'target temperature; the comfort band is ± the comfort offset around it.');
    idCard.appendChild(paramGrid(
      textField(room, 'name', 'Room name', { placeholder: 'living_room', hint: 'Unique identifier. Changing it on an existing room re-creates its entities.' }),
      numberField(room, 'setpoint', 'Setpoint', { step: 0.5, unit: '°C', min: 5, max: 35, hint: 'Target temperature.' }),
      numberField(room, 'comfort_offset', 'Comfort offset', { step: 0.1, unit: '±°C', min: 0.1, max: 5, hint: 'Half-width of the comfort band.' }),
    ));
    body.appendChild(idCard);

    // --- Sensors ------------------------------------------------------------
    const sensorCard = sectionCard('Sensors',
      'Which Home Assistant entities measure this room. The temperature sensor drives the '
      + 'model; window sensors pause heating while a window is open.');
    sensorCard.appendChild(paramGrid(
      entityField(hass, room, 'temp_sensor', 'Temperature sensor', ['sensor'], { hint: 'Primary indoor temperature sensor.' }),
    ));
    sensorCard.appendChild(listEditor({
      title: 'Window / contact sensors',
      items: ensureArray(room, 'window_sensors'),
      addLabel: '+ Add window sensor',
      emptyText: 'No window sensors. Heating ignores open windows for this room.',
      renderRow: (arr, i) => {
        const listId = `dl-ws-${Math.random().toString(36).slice(2, 8)}`;
        const ids = Object.keys(hass?.states || {})
          .filter((id) => id.startsWith('binary_sensor.')).sort();
        const group = el('div', 'config-row form-group');
        group.innerHTML = `
          <input class="form-input" type="text" list="${listId}" placeholder="binary_sensor.…"
            value="${arr[i] != null ? String(arr[i]).replace(/"/g, '&quot;') : ''}">
          <datalist id="${listId}">${ids.map((id) => `<option value="${id}"></option>`).join('')}</datalist>
        `;
        const input = group.querySelector('input');
        input.addEventListener('change', () => { arr[i] = input.value.trim(); });
        return group;
      },
      newItem: () => '',
    }));
    body.appendChild(sensorCard);

    // --- Thermal model ------------------------------------------------------
    const thermCard = sectionCard('Thermal model',
      'Bulk thermal behaviour. Thermal mass is the heat capacity (higher = slower to heat/cool). '
      + 'R-external is the resistance to outdoors (higher = better insulated). These are refined '
      + 'automatically by system identification, but good starting values help.');
    thermCard.appendChild(paramGrid(
      numberField(room, 'thermal_mass', 'Thermal mass', { step: 100000, unit: 'J/K', min: 1000, hint: '~5,000,000 for a typical room.' }),
      numberField(room, 'r_external', 'External resistance', { step: 0.005, unit: 'K/W', min: 0.0001, hint: '~0.05 typical; higher = better insulated.' }),
      selectField(room, 'floor_type', 'Floor type', enums.floor_types || ['none'], { def: 'none', hint: 'Slab / underfloor heating coupling.' }),
    ));
    // Envelope tightness preset → fills infiltration_fraction.
    const tightnessRow = el('div', 'tuning-params-grid tuning-params-grid--wide');
    const infilField = numberField(room, 'infiltration_fraction', 'Infiltration fraction', {
      step: 0.05, min: 0, max: 0.95,
      hint: 'Share of envelope loss driven by air leakage (0–0.95).',
    });
    const tightMap = enums.envelope_tightness_map || {};
    const tightSelect = selectField(
      { _t: '' }, '_t', 'Envelope tightness preset',
      [{ value: '', label: '(custom)' }].concat((enums.envelope_tightness || []).map((k) => ({ value: k, label: prettify(k) }))),
      { hint: 'Pick a preset to fill the infiltration fraction.' },
    );
    tightSelect.querySelector('select').addEventListener('change', (ev) => {
      const v = ev.target.value;
      if (v && tightMap[v] != null) {
        room.infiltration_fraction = tightMap[v];
        const inp = infilField.querySelector('input');
        if (inp) inp.value = tightMap[v];
      }
    });
    tightnessRow.appendChild(tightSelect);
    tightnessRow.appendChild(infilField);
    thermCard.appendChild(tightnessRow);
    body.appendChild(thermCard);

    // --- Solar --------------------------------------------------------------
    const solarCard = sectionCard('Solar gain',
      'How much sun this room collects. Use the exposure preset for a quick estimate, or '
      + 'enumerate individual windows below for higher fidelity (windows take precedence).');
    solarCard.appendChild(paramGrid(
      selectField(room, 'solar_exposure', 'Solar exposure', enums.solar_exposures || ['none'], { def: 'none', hint: 'Coarse glazing/aperture preset.' }),
      numberField(room, 'solar_facing', 'Solar facing', { step: 5, unit: '°', min: 0, max: 360, hint: 'Direction the glazing faces (0=N, 90=E, 180=S, 270=W).' }),
    ));
    solarCard.appendChild(listEditor({
      title: 'Windows',
      items: room.windows,
      addLabel: '+ Add window',
      emptyText: 'No individual windows. The exposure preset above is used instead.',
      renderRow: (arr, i) => {
        const w = arr[i];
        const row = el('div', 'config-row tuning-params-grid tuning-params-grid--wide');
        row.appendChild(numberField(w, 'area', 'Area', { step: 0.5, unit: 'm²', min: 0, hint: 'Glazed area.' }));
        row.appendChild(numberField(w, 'orientation', 'Orientation', { step: 5, unit: '°', min: 0, max: 360, hint: '0=N, 90=E, 180=S, 270=W.' }));
        row.appendChild(numberField(w, 'tilt', 'Tilt', { step: 5, unit: '°', min: 0, max: 90, hint: '90 = vertical.' }));
        return row;
      },
      newItem: () => ({ area: 1.0, orientation: 180, tilt: 90 }),
    }));
    body.appendChild(solarCard);

    // --- Connections --------------------------------------------------------
    const otherRooms = allRooms.map((r) => r.name).filter((n) => n && n !== room.name);
    const connCard = sectionCard('Inter-room connections',
      'Thermal links to adjacent rooms (through internal walls/doors). R-value is the '
      + 'resistance of the link (lower = stronger coupling).');
    connCard.appendChild(listEditor({
      title: 'Connections',
      items: room.connections,
      addLabel: '+ Add connection',
      emptyText: 'No inter-room connections configured.',
      renderRow: (arr, i) => {
        const c = arr[i];
        const row = el('div', 'config-row tuning-params-grid tuning-params-grid--wide');
        row.appendChild(selectField(c, 'room', 'Connected room',
          otherRooms.length ? otherRooms : [''], { hint: 'Adjacent room.' }));
        row.appendChild(numberField(c, 'r_value', 'R-value', { step: 0.05, unit: 'K/W', min: 0.0001, hint: 'Lower = stronger coupling.' }));
        return row;
      },
      newItem: () => ({ room: otherRooms[0] || '', r_value: 0.2 }),
    }));
    body.appendChild(connCard);

    // --- Advanced envelope --------------------------------------------------
    const advCard = sectionCard('Advanced envelope (optional)',
      'Fine envelope corrections. Leave blank to use sensible defaults; these are normally '
      + 'identified from data.');
    advCard.appendChild(paramGrid(
      selectField(room, 'facade_colour', 'Facade colour', enums.facade_colours || ['medium'], { def: 'medium', hint: 'Solar absorptance of the opaque facade.' }),
      numberField(room, 'facade_solar_share', 'Facade solar share', { step: 0.05, min: 0, max: 1, hint: 'Sol-air share on the wall node (0 = off).' }),
      numberField(room, 'sky_radiative_ua', 'Sky radiative UA', { step: 0.5, unit: 'W/K', min: 0, hint: 'Clear-night radiative cooling (0 = off).' }),
      numberField(room, 'thermal_bridge_psi_l', 'Thermal bridge', { step: 0.5, unit: 'W/K', min: 0, hint: 'Linear thermal-bridge correction (0 = off).' }),
      numberField(room, 'c_air_fraction', 'Air-mass fraction', { step: 0.01, min: 0, max: 1, hint: 'Fast air node share of thermal mass (~0.05).' }),
      numberField(room, 'r_aw_fraction', 'Air↔wall film fraction', { step: 0.01, min: 0, max: 1, hint: 'Internal film share of the conductive path (~0.05).' }),
    ));
    body.appendChild(advCard);

    // --- Save / delete ------------------------------------------------------
    const actions = el('div', 'tuning-actions');
    actions.style.marginTop = '20px';
    actions.innerHTML = `
      <button class="btn btn--primary" data-role="save">${isNew ? 'Create Room' : 'Save Changes'}</button>
      ${isNew ? '' : '<button class="btn btn--ghost" data-role="delete">Delete Room</button>'}
      <span class="tuning-actions__status" data-role="status"></span>
    `;
    body.appendChild(actions);
    const statusEl = actions.querySelector('[data-role="status"]');

    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (!room.name || !String(room.name).trim()) {
        setStatus(statusEl, 'A room name is required.', 'error');
        return;
      }
      btn.disabled = true;
      setStatus(statusEl, 'Saving… (the model will restart)', 'running');
      try {
        const cleaned = cleanRoom(room);
        const next = allRooms.map((r) => ({ ...r }));
        if (isNew) next.push(cleaned); else next[idx] = cleaned;
        await hass.callService('heating_assistant', 'update_rooms', { rooms: next });
        setStatus(statusEl, 'Saved. Restarting model…', 'success');
        setTimeout(() => { window.location.hash = '#config/rooms'; }, 800);
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
          setTimeout(() => { window.location.hash = '#config/rooms'; }, 800);
        } catch (err) {
          setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
          delBtn.disabled = false;
        }
      });
    }
  });

  return { update() {}, destroy() {} };
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
  container.innerHTML = '';
  container.appendChild(backNav('CONFIGURATION', '#config'));

  const header = el('div', 'sched-detail__section-header');
  header.innerHTML = `
    <span class="section-header" style="margin:0;border:0;padding:0;">HEAT SOURCES</span>
    <button class="btn btn--primary btn--sm" data-role="add">+ Add Heat Source</button>
  `;
  container.appendChild(header);

  const body = el('div');
  body.appendChild(loadingNode());
  container.appendChild(body);

  header.querySelector('[data-role="add"]').addEventListener('click', () => {
    window.location.hash = '#config/sources/new';
  });

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
      card.addEventListener('click', () => { window.location.hash = `#config/sources/${i}`; });
      grid.appendChild(card);
    });
    body.appendChild(grid);
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Heat sources — editor
// ---------------------------------------------------------------------------

function renderSourceEditor(container, connection, hass, idxParam) {
  container.innerHTML = '';
  container.appendChild(backNav('HEAT SOURCES', '#config/sources'));
  const body = el('div');
  body.appendChild(loadingNode());
  container.appendChild(body);

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
      ? { name: '', type: 'electric_heater', room: roomNames[0] || '', max_power: 2000 }
      : { ...allSources[idx] };

    body.innerHTML = '';
    body.appendChild(el('div', 'section-header', isNew ? 'NEW HEAT SOURCE' : `EDIT SOURCE: ${src.name || ''}`));

    // Render the dynamic body (re-rendered when the type changes).
    const dynamic = el('div');
    body.appendChild(dynamic);

    function renderFields() {
      dynamic.innerHTML = '';

      const idCard = sectionCard('Identity & placement',
        'A unique name, the kind of source, and which room it heats. The driven entity is what '
        + 'the controller switches or sets to deliver heat.');
      idCard.appendChild(paramGrid(
        textField(src, 'name', 'Name', { placeholder: 'living_room_heater', hint: 'Unique identifier.' }),
        selectFieldRerender(src, 'type', 'Type', enums.source_types || ['electric_heater', 'heat_pump'], renderFields, { hint: 'Electric heater or heat pump.' }),
        selectField(src, 'room', 'Room', roomNames.length ? roomNames : [''], { hint: 'Room this source heats.' }),
        entityField(hass, src, 'heater_entity', 'Driven entity',
          src.type === 'heat_pump' ? ['climate'] : ['switch', 'input_boolean', 'climate', 'number'],
          { hint: 'Entity the controller commands.' }),
      ));
      dynamic.appendChild(idCard);

      const capCard = sectionCard('Capacity',
        'Thermal output the source can deliver.');
      const capFields = [
        numberField(src, 'max_power', 'Max power', { step: 100, unit: 'W', min: 0, hint: 'Maximum thermal output.' }),
      ];
      if (src.type === 'heat_pump') {
        capFields.push(numberField(src, 'min_power', 'Min power', { step: 100, unit: 'W', min: 0, hint: 'Minimum modulating output (0 = none).' }));
      }
      capCard.appendChild(paramGrid(...capFields));
      dynamic.appendChild(capCard);

      if (src.type === 'heat_pump') {
        const hpCard = sectionCard('Heat-pump performance',
          'Coefficient of performance and operating mode. COP is referenced to an outdoor '
          + 'temperature; cooling values apply when the unit can cool.');
        hpCard.appendChild(paramGrid(
          numberField(src, 'cop_rated', 'Rated COP', { step: 0.1, min: 1, hint: 'Heating COP at the reference temperature.' }),
          numberField(src, 'cop_temp_ref', 'COP reference temp', { step: 1, unit: '°C', hint: 'Outdoor temp at which rated COP applies.' }),
          numberField(src, 'max_temp_offset', 'Max temp offset', { step: 0.5, unit: '°C', min: 0, hint: 'Setpoint offset at full power.' }),
          selectField(src, 'hvac_mode', 'HVAC mode', enums.hvac_modes || ['heat', 'cool', 'heat_cool'], { def: 'heat_cool', hint: 'Heating, cooling, or both.' }),
          numberField(src, 'cooling_cop', 'Cooling COP', { step: 0.1, min: 0, hint: 'Cooling efficiency (EER).' }),
          numberField(src, 'cooling_efficiency', 'Cooling efficiency', { step: 0.05, min: 0, max: 1, hint: 'Fraction of cooling capacity used.' }),
        ));
        dynamic.appendChild(hpCard);
      } else {
        const elCard = sectionCard('Electric heater',
          'Conversion efficiency of electrical input to heat (1.0 for resistive heaters).');
        elCard.appendChild(paramGrid(
          numberField(src, 'efficiency', 'Efficiency', { step: 0.05, min: 0, max: 1, hint: '1.0 for resistive heaters.' }),
        ));
        dynamic.appendChild(elCard);
      }

      const advCard = sectionCard('Advanced (optional)',
        'Emitter lag captures hydronic/radiator thermal inertia between command and delivered heat.');
      advCard.appendChild(paramGrid(
        numberField(src, 'emitter_time_constant', 'Emitter time constant', { step: 30, unit: 's', min: 0, hint: '0 for electric; ~600 for hydronic radiators.' }),
      ));
      dynamic.appendChild(advCard);
    }

    renderFields();

    const actions = el('div', 'tuning-actions');
    actions.style.marginTop = '20px';
    actions.innerHTML = `
      <button class="btn btn--primary" data-role="save">${isNew ? 'Create Source' : 'Save Changes'}</button>
      ${isNew ? '' : '<button class="btn btn--ghost" data-role="delete">Delete Source</button>'}
      <span class="tuning-actions__status" data-role="status"></span>
    `;
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
        setTimeout(() => { window.location.hash = '#config/sources'; }, 800);
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
          setTimeout(() => { window.location.hash = '#config/sources'; }, 800);
        } catch (err) {
          setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
          delBtn.disabled = false;
        }
      });
    }
  });

  return { update() {}, destroy() {} };
}

// Select that triggers a re-render callback after updating the model (used so
// the heat-source form can swap fields when the type changes).
function selectFieldRerender(obj, key, label, options, onChange, opts = {}) {
  const group = selectField(obj, key, label, options, opts);
  group.querySelector('select').addEventListener('change', () => onChange());
  return group;
}

function cleanSource(src) {
  const out = { ...src };
  out.name = String(out.name).trim();
  out.max_power = Number(out.max_power || 0);
  // Drop heat-pump-only keys for electric heaters (and vice-versa) so values
  // from a previous type don't linger.
  if (out.type !== 'heat_pump') {
    ['cop_rated', 'cop_temp_ref', 'min_power', 'max_temp_offset', 'hvac_mode',
      'cooling_cop', 'cooling_efficiency', 'heating_efficiency'].forEach((k) => delete out[k]);
  } else {
    delete out.efficiency;
  }
  return out;
}

// ---------------------------------------------------------------------------
// System / environment
// ---------------------------------------------------------------------------

function renderSystem(container, connection, hass) {
  container.innerHTML = '';
  container.appendChild(backNav('CONFIGURATION', '#config'));
  container.appendChild(el('div', 'section-header', 'ENVIRONMENT & SITE'));

  const body = el('div');
  body.appendChild(loadingNode());
  container.appendChild(body);

  connection.getModelConfig().then((cfg) => {
    const sys = (cfg && cfg.system) || {};
    const working = { ...sys };

    body.innerHTML = '';
    const actions = actionsBar('Apply Changes');
    body.appendChild(actions);

    const envCard = sectionCard('Weather & energy sensors',
      'External signals the controller reads. Outdoor temperature is required for good control; '
      + 'weather and solar irradiance improve the forecast; the price sensor enables price-aware '
      + 'optimisation. Leave any field blank to disable it.');
    envCard.appendChild(paramGrid(
      entityField(hass, working, 'outdoor_temp_entity', 'Outdoor temperature', ['sensor'], { hint: 'Measured outdoor air temperature.' }),
      entityField(hass, working, 'weather_entity', 'Weather forecast', ['weather'], { hint: 'Weather entity for the outdoor forecast.' }),
      entityField(hass, working, 'solar_radiation_entity', 'Solar irradiance', ['sensor'], { hint: 'GHI in W/m² (optional).' }),
      entityField(hass, working, 'price_entity', 'Electricity price', ['sensor'], { hint: 'Hourly market price (optional).' }),
    ));
    body.appendChild(envCard);

    const siteCard = sectionCard('Site location',
      'Latitude and longitude drive the solar-position model. Defaults to your Home Assistant '
      + 'location.');
    siteCard.appendChild(paramGrid(
      numberField(working, 'latitude', 'Latitude', { step: 0.0001, min: -90, max: 90, unit: '°' }),
      numberField(working, 'longitude', 'Longitude', { step: 0.0001, min: -180, max: 180, unit: '°' }),
    ));
    body.appendChild(siteCard);

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
        if (working.latitude != null && working.latitude !== '') data.latitude = Number(working.latitude);
        if (working.longitude != null && working.longitude !== '') data.longitude = Number(working.longitude);
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
// Generic add/remove list editor used for windows, connections, sensors
// ---------------------------------------------------------------------------

function ensureArray(obj, key) {
  if (!Array.isArray(obj[key])) obj[key] = [];
  return obj[key];
}

function listEditor({ title, items, addLabel, emptyText, renderRow, newItem }) {
  const wrap = el('div', 'config-list-editor');
  const head = el('div', 'config-list-editor__head');
  head.innerHTML = `
    <span class="config-list-editor__title">${title}</span>
    <button class="btn btn--secondary btn--sm" data-role="add">${addLabel}</button>
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
