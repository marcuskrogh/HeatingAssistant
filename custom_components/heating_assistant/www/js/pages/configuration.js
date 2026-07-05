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

import { setPanelHash } from '../panel-hash.js?v=92';
import {
  updateHeatSources,
  updateRooms,
  updateSystemConfig,
  updateSystemParams,
  updateUiSettings,
} from '../ha-services.js?v=92';
import { ICONS } from '../config/config-icons.js?v=92';
import {
  ROOM_SIZE_PRESETS,
  HOUSE_AGE_PRESETS,
  nearestPreset,
} from '../config/config-presets.js?v=92';
import {
  el,
  schedulePanelNav,
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
  entitySelectorField,
  listEditor,
} from '../config/config-ui.js?v=92';

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
        await updateUiSettings(hass, data);
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
        await updateRooms(hass, payload);
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
          await updateRooms(hass, { rooms: next });
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
        await updateHeatSources(hass, { heat_sources: next });
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
          await updateHeatSources(hass, { heat_sources: next });
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
        await updateSystemParams(hass, data);
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
        await updateSystemConfig(hass, data);
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
