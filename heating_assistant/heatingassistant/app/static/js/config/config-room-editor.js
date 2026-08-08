import { setPanelHash } from '../panel-hash.js?v=116';
import {
  updateHeatSources,
  updateRooms,
  updateSystemConfig,
  updateSystemParams,
  updateUiSettings,
} from '../ha-services.js?v=116';
import { ICONS } from './config-icons.js?v=116';
import { LANDING_CARDS } from './config-landing.js?v=116';
import {
  ROOM_SIZE_PRESETS,
  HOUSE_AGE_PRESETS,
  nearestPreset,
} from './config-presets.js?v=116';
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
} from './config-ui.js?v=116';

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
      {
        multiple: true,
        hint: 'Averaged when more than one is selected. Under Ingress, type the full HA entity ID '
          + '(e.g. sensor.living_room_temperature) — the App bridges it over MQTT.',
        emptyText: 'No temperature sensors — add at least one.',
      }));
    const sensorAdv = advancedSubsection(sensorCard, 'Window / contact sensors');
    sensorAdv.appendChild(entitySelectorField(container, hass, room, 'window_sensors',
      'Window sensors', ['binary_sensor'],
      {
        multiple: true,
        hint: 'Heating pauses for this room while any of these report open. Type a full '
          + 'binary_sensor.* entity ID when the list is incomplete.',
        emptyText: 'No window sensors.',
      }));
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

export { renderRoomEditor, cleanRoom };
