import { entityValue, formatNumber } from '../utils.js';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

const PARAM_DEFS = [
  { key: 'update_interval', label: 'Sample Interval', unit: 's', hint: 'Re-planning cadence (60\u20133600)', step: 30, min: 60, max: 3600, parse: parseInt },
  { key: 'comfort_offset', label: 'Comfort Offset', unit: '\u00b0C', hint: 'Symmetric band around setpoint', step: 0.1, min: 0.1, max: 5.0, parse: parseFloat },
  { key: 'horizon', label: 'Prediction Horizon', unit: 'steps', hint: 'Control intervals planned ahead', step: 1, min: 1, max: 480, parse: parseInt },
  { key: 'tracking_weight', label: 'Tracking Weight', unit: '', hint: 'Setpoint tracking strength (0 = band only)', step: 0.1, min: 0, max: 10, parse: parseFloat },
  { key: 'energy_weight', label: 'Energy Weight', unit: '', hint: 'Energy-use penalty', step: 0.01, min: 0, max: 10, parse: parseFloat },
  { key: 'energy_price_weight', label: 'Price Sensitivity', unit: '', hint: 'Electricity price cost scaling', step: 0.1, min: 0, max: 10000, parse: parseFloat },
  { key: 'smoothing_weight', label: 'Output Smoothing', unit: '', hint: 'Penalises rapid output changes', step: 0.05, min: 0, max: 10, parse: parseFloat },
  { key: 'soft_constraint_weight', label: 'Comfort Band Penalty (quadratic)', unit: '', hint: 'Quadratic penalty for leaving comfort zone', step: 1, min: 0, max: 10000, parse: parseFloat },
  { key: 'soft_constraint_linear_weight', label: 'Comfort Band Penalty (linear)', unit: '', hint: 'Linear penalty for comfort-band violations (0 = disabled)', step: 1, min: 0, max: 1000000, parse: parseFloat },
  { key: 'terminal_weight', label: 'Terminal Weight', unit: '', hint: 'End-of-horizon constraint', step: 1, min: 1, max: 10000, parse: parseFloat },
];

const DEFAULTS = {
  update_interval: 900,
  comfort_offset: 2.0,
  horizon: 100,
  tracking_weight: 0.0,
  energy_weight: 0.01,
  energy_price_weight: 1.0,
  smoothing_weight: 0.1,
  soft_constraint_weight: 10.0,
  soft_constraint_linear_weight: 0.0,
  terminal_weight: 100.0,
};

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function renderControllerTuning(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderScheduleDetail(container, slug, rooms, state, connection, hass);
  }
  return renderTuningIndex(container, rooms, state, connection, hass);
}

// ---------------------------------------------------------------------------
// Index view — MPC params + room schedule tiles
// ---------------------------------------------------------------------------

function renderTuningIndex(container, rooms, state, connection, hass) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'CONTROLLER TUNING';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Configure MPC controller parameters. These determine how the controller balances comfort, energy use, and responsiveness. Changes take effect immediately.';
  container.appendChild(desc);

  // --- Parameter form ---
  const formSection = document.createElement('div');
  formSection.className = 'card tuning-section';

  const grid = document.createElement('div');
  grid.className = 'tuning-params-grid tuning-params-grid--wide';

  const inputs = {};
  for (const def of PARAM_DEFS) {
    const group = document.createElement('div');
    group.className = 'form-group';
    group.innerHTML = `
      <label class="form-label" for="ctrl-${def.key}">${def.label}</label>
      <input class="form-input" type="number" id="ctrl-${def.key}"
        step="${def.step}" min="${def.min}" max="${def.max}"
        value="${DEFAULTS[def.key]}">
      <span class="form-hint">${def.unit ? def.unit + ' \u2014 ' : ''}${def.hint}</span>
    `;
    grid.appendChild(group);
    inputs[def.key] = group.querySelector('input');
  }

  formSection.appendChild(grid);

  const actionsRow = document.createElement('div');
  actionsRow.className = 'tuning-actions';
  actionsRow.style.marginTop = '20px';
  actionsRow.innerHTML = `
    <button class="btn btn--primary" id="btn-apply-ctrl">Apply Changes</button>
    <button class="btn btn--ghost" id="btn-reset-ctrl">Reset to Defaults</button>
    <span class="tuning-actions__status" id="ctrl-status"></span>
  `;
  formSection.appendChild(actionsRow);
  container.appendChild(formSection);

  // --- Room Schedules section ---
  const schedHeader = document.createElement('div');
  schedHeader.className = 'section-header';
  schedHeader.style.marginTop = '32px';
  schedHeader.textContent = 'ROOM SCHEDULES';
  container.appendChild(schedHeader);

  const schedGrid = document.createElement('div');
  schedGrid.className = 'grid-rooms';
  container.appendChild(schedGrid);

  // --- Wire up controller params ---
  const btnApply = container.querySelector('#btn-apply-ctrl');
  const btnReset = container.querySelector('#btn-reset-ctrl');
  const statusEl = container.querySelector('#ctrl-status');

  function setStatus(text, type = '') {
    statusEl.textContent = text;
    statusEl.className = 'tuning-actions__status';
    if (type) statusEl.classList.add(`tuning-actions__status--${type}`);
  }

  function populateFromState(st) {
    const config = st[CONFIG_ENTITY]?.attributes || {};
    for (const def of PARAM_DEFS) {
      if (config[def.key] != null) {
        inputs[def.key].value = config[def.key];
      }
    }
  }

  function buildScheduleTiles(st) {
    schedGrid.innerHTML = '';
    const config = st[CONFIG_ENTITY]?.attributes || {};
    const roomSchedules = config.room_schedules || {};

    for (const room of rooms) {
      const tile = document.createElement('div');
      tile.className = 'card card--clickable room-tile';

      const schedData = roomSchedules[room.slug] || roomSchedules[room.name] || null;
      const periods = schedData?.periods || [];
      const enabled = schedData?.enabled ?? true;

      const activePeriod = findActivePeriod(periods);
      const activeLabel = activePeriod
        ? (activePeriod.mode === 'off' ? 'OFF' : (activePeriod.setpoint != null ? `${activePeriod.setpoint}\u00b0C` : 'COMFORT'))
        : 'No active period';
      const statusClass = enabled ? 'room-tile__status--active' : 'room-tile__status--idle';

      const periodCount = periods.length;
      const nextPeriod = findNextPeriod(periods);
      const nextLabel = nextPeriod ? `Next: ${nextPeriod.start}` : '';

      tile.innerHTML = `
        <span class="room-tile__name">${room.name}</span>
        <div class="room-tile__row">
          <span class="room-tile__temp">${activeLabel}</span>
          <span class="room-tile__status ${statusClass}"></span>
        </div>
        <div class="room-tile__row">
          <span class="room-tile__power">${periodCount} period${periodCount !== 1 ? 's' : ''}</span>
          <span class="room-tile__setpoint">${nextLabel}</span>
        </div>
      `;
      tile.addEventListener('click', () => {
        window.location.hash = `#tuning/${room.slug}`;
      });
      schedGrid.appendChild(tile);
    }
  }

  function findActivePeriod(periods) {
    if (!periods.length) return null;
    const now = new Date();
    const day = (now.getDay() + 6) % 7;
    const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    for (const p of periods) {
      const days = p.days || [0, 1, 2, 3, 4, 5, 6];
      if (!days.includes(day)) continue;
      if (hhmm >= p.start && hhmm < p.end) return p;
    }
    return null;
  }

  function findNextPeriod(periods) {
    if (!periods.length) return null;
    const now = new Date();
    const day = (now.getDay() + 6) % 7;
    const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    let best = null;
    for (const p of periods) {
      const days = p.days || [0, 1, 2, 3, 4, 5, 6];
      if (!days.includes(day)) continue;
      if (p.start > hhmm) {
        if (!best || p.start < best.start) best = p;
      }
    }
    return best;
  }

  btnApply.addEventListener('click', async () => {
    setStatus('Applying\u2026', 'running');
    btnApply.disabled = true;
    try {
      const data = {};
      for (const def of PARAM_DEFS) {
        data[def.key] = def.parse(inputs[def.key].value);
      }
      await hass.callService('heating_assistant', 'update_controller_tuning', data);
      setStatus('Applied successfully.', 'success');
    } catch (err) {
      setStatus('Error: ' + (err.message || err), 'error');
    }
    btnApply.disabled = false;
  });

  btnReset.addEventListener('click', () => {
    for (const def of PARAM_DEFS) {
      inputs[def.key].value = DEFAULTS[def.key];
    }
    setStatus('Reset to defaults.', '');
  });

  populateFromState(state);
  buildScheduleTiles(state);

  return {
    update(newState) {
      const focused = document.activeElement;
      const isEditing = Object.values(inputs).some((inp) => inp === focused);
      if (!isEditing) {
        populateFromState(newState);
      }
      buildScheduleTiles(newState);
    },
    destroy() {},
  };
}

// ---------------------------------------------------------------------------
// Detail view — per-room schedule display
// ---------------------------------------------------------------------------

function renderScheduleDetail(container, roomSlug, rooms, state, connection, hass) {
  const room = rooms.find((r) => r.slug === roomSlug);
  if (!room) {
    container.innerHTML = `<div class="loading">Room not found: ${roomSlug}</div>`;
    return { update() {}, destroy() {} };
  }

  container.innerHTML = '';

  const nav = document.createElement('button');
  nav.className = 'nav-back';
  nav.innerHTML = '<span class="nav-back__arrow">\u2190</span> TUNING';
  nav.addEventListener('click', () => { window.location.hash = '#tuning'; });
  container.appendChild(nav);

  const header = document.createElement('div');
  header.className = 'room-header';
  header.innerHTML = `<h2 class="room-header__title">${room.name}</h2>`;
  container.appendChild(header);

  // Schedule toggle
  const toggleRow = document.createElement('div');
  toggleRow.className = 'schedule-detail__toggle';
  toggleRow.innerHTML = `
    <span class="schedule-detail__toggle-label">Schedule:</span>
    <button class="schedule-detail__toggle-btn" id="sched-toggle"></button>
    <span class="tuning-actions__status" id="sched-toggle-status"></span>
  `;
  container.appendChild(toggleRow);

  const periodsContainer = document.createElement('div');
  container.appendChild(periodsContainer);

  const actionsRow = document.createElement('div');
  actionsRow.className = 'tuning-actions';
  actionsRow.style.marginTop = '16px';
  actionsRow.innerHTML = `
    <button class="btn btn--primary" id="btn-save-schedule">Save Changes</button>
    <button class="btn btn--secondary" id="btn-add-period">Add Period</button>
    <span class="tuning-actions__status" id="sched-save-status"></span>
  `;
  container.appendChild(actionsRow);

  const toggleBtn = container.querySelector('#sched-toggle');
  const toggleStatus = container.querySelector('#sched-toggle-status');
  const btnSave = container.querySelector('#btn-save-schedule');
  const btnAdd = container.querySelector('#btn-add-period');
  const saveStatus = container.querySelector('#sched-save-status');

  let localPeriods = [];
  let dirty = false;

  function getScheduleData(st) {
    const config = st[CONFIG_ENTITY]?.attributes || {};
    const roomSchedules = config.room_schedules || {};
    return roomSchedules[room.slug] || roomSchedules[room.name] || null;
  }

  function renderToggle(schedData) {
    const enabled = schedData?.enabled ?? true;
    toggleBtn.textContent = enabled ? 'ENABLED' : 'DISABLED';
    toggleBtn.className = 'schedule-detail__toggle-btn ' +
      (enabled ? 'schedule-detail__toggle-btn--active' : 'schedule-detail__toggle-btn--inactive');
  }

  function initLocalPeriods(schedData) {
    const periods = schedData?.periods || [];
    localPeriods = periods.map((p) => ({ ...p, days: [...(p.days || [0, 1, 2, 3, 4, 5, 6])] }));
    dirty = false;
  }

  function renderPeriodForms() {
    periodsContainer.innerHTML = '';

    if (localPeriods.length === 0) {
      periodsContainer.innerHTML = '<p class="tuning-section__desc">No schedules configured. Click "Add Period" to create one.</p>';
      return;
    }

    for (let i = 0; i < localPeriods.length; i++) {
      const p = localPeriods[i];
      const card = document.createElement('div');
      card.className = 'card schedule-form__period';

      const modeOptions = `
        <option value="comfort"${p.mode !== 'off' ? ' selected' : ''}>Comfort</option>
        <option value="off"${p.mode === 'off' ? ' selected' : ''}>Off</option>
      `;

      let daysHtml = '';
      for (let d = 0; d < 7; d++) {
        const active = (p.days || []).includes(d);
        daysHtml += `<span class="schedule-form__day${active ? ' schedule-form__day--active' : ''}" data-day="${d}">${DAY_NAMES[d]}</span>`;
      }

      const setpointRow = p.mode !== 'off'
        ? `<div class="form-group"><label class="form-label">Setpoint</label><input class="form-input form-input--time" type="number" step="0.5" min="5" max="35" value="${p.setpoint ?? 21}" data-field="setpoint"></div>`
        : `<div class="form-group"><label class="form-label">Frost Protection</label><input class="form-input form-input--time" type="number" step="0.5" min="0" max="15" value="${p.frost_protection ?? 5}" data-field="frost_protection"></div>`;

      card.innerHTML = `
        <button class="schedule-form__delete" data-idx="${i}" title="Delete period">\u00d7</button>
        <div class="schedule-form__period-row">
          <div class="form-group">
            <label class="form-label">Name</label>
            <input class="form-input form-input--name" type="text" value="${p.name || ''}" data-field="name">
          </div>
          <div class="form-group">
            <label class="form-label">Start</label>
            <input class="form-input form-input--time" type="time" value="${p.start || '08:00'}" data-field="start">
          </div>
          <div class="form-group">
            <label class="form-label">End</label>
            <input class="form-input form-input--time" type="time" value="${p.end || '22:00'}" data-field="end">
          </div>
          <div class="form-group">
            <label class="form-label">Mode</label>
            <select class="schedule-form__mode-select" data-field="mode">${modeOptions}</select>
          </div>
          ${setpointRow}
        </div>
        <div class="schedule-form__days" data-period="${i}">${daysHtml}</div>
      `;
      periodsContainer.appendChild(card);

      // Wire inputs
      card.querySelectorAll('[data-field]').forEach((input) => {
        const field = input.dataset.field;
        input.addEventListener('change', () => {
          if (field === 'mode') {
            localPeriods[i].mode = input.value;
            if (input.value === 'off') {
              delete localPeriods[i].setpoint;
              localPeriods[i].frost_protection = localPeriods[i].frost_protection ?? 5;
            } else {
              delete localPeriods[i].frost_protection;
              localPeriods[i].setpoint = localPeriods[i].setpoint ?? 21;
            }
            dirty = true;
            renderPeriodForms();
          } else if (field === 'setpoint' || field === 'frost_protection') {
            localPeriods[i][field] = parseFloat(input.value);
            dirty = true;
          } else {
            localPeriods[i][field] = input.value;
            dirty = true;
          }
        });
      });

      // Wire days
      card.querySelectorAll('.schedule-form__day').forEach((dayEl) => {
        dayEl.addEventListener('click', () => {
          const d = parseInt(dayEl.dataset.day, 10);
          const idx = localPeriods[i].days.indexOf(d);
          if (idx >= 0) {
            localPeriods[i].days.splice(idx, 1);
            dayEl.classList.remove('schedule-form__day--active');
          } else {
            localPeriods[i].days.push(d);
            localPeriods[i].days.sort();
            dayEl.classList.add('schedule-form__day--active');
          }
          dirty = true;
        });
      });

      // Wire delete
      card.querySelector('.schedule-form__delete').addEventListener('click', () => {
        localPeriods.splice(i, 1);
        dirty = true;
        renderPeriodForms();
      });
    }
  }

  toggleBtn.addEventListener('click', async () => {
    const schedData = getScheduleData(state);
    const currentEnabled = schedData?.enabled ?? true;
    toggleStatus.textContent = 'Saving\u2026';
    toggleStatus.className = 'tuning-actions__status tuning-actions__status--running';
    try {
      await hass.callService('heating_assistant', 'set_schedule_enabled', {
        room_name: room.slug,
        enabled: !currentEnabled,
      });
      toggleStatus.textContent = '';
      toggleStatus.className = 'tuning-actions__status';
    } catch (err) {
      toggleStatus.textContent = 'Error: ' + (err.message || err);
      toggleStatus.className = 'tuning-actions__status tuning-actions__status--error';
    }
  });

  btnAdd.addEventListener('click', () => {
    localPeriods.push({
      name: `Period ${localPeriods.length + 1}`,
      start: '08:00',
      end: '22:00',
      mode: 'comfort',
      setpoint: 21,
      days: [0, 1, 2, 3, 4, 5, 6],
    });
    dirty = true;
    renderPeriodForms();
  });

  btnSave.addEventListener('click', async () => {
    saveStatus.textContent = 'Saving\u2026';
    saveStatus.className = 'tuning-actions__status tuning-actions__status--running';
    btnSave.disabled = true;
    try {
      const periods = localPeriods.map((p) => {
        const out = { name: p.name, start: p.start, end: p.end, mode: p.mode, days: p.days };
        if (p.mode === 'off') {
          out.frost_protection = p.frost_protection ?? 5;
        } else {
          out.setpoint = p.setpoint ?? 21;
        }
        return out;
      });
      await hass.callService('heating_assistant', 'update_room_schedule', {
        room_name: room.slug,
        periods,
      });
      dirty = false;
      saveStatus.textContent = 'Saved.';
      saveStatus.className = 'tuning-actions__status tuning-actions__status--success';
    } catch (err) {
      saveStatus.textContent = 'Error: ' + (err.message || err);
      saveStatus.className = 'tuning-actions__status tuning-actions__status--error';
    }
    btnSave.disabled = false;
  });

  const schedData = getScheduleData(state);
  renderToggle(schedData);
  initLocalPeriods(schedData);
  renderPeriodForms();

  return {
    update(newState) {
      state = newState;
      const newData = getScheduleData(newState);
      renderToggle(newData);
      if (!dirty) {
        initLocalPeriods(newData);
        renderPeriodForms();
      }
    },
    destroy() {},
  };
}
