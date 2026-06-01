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
const MAX_VISIBLE_PERIODS = 3;

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
      tile.className = 'card card--clickable schedule-tile';

      const schedData = roomSchedules[room.slug] || roomSchedules[room.name] || null;
      const periods = schedData?.periods || [];

      let periodsHtml = '';
      const visible = periods.slice(0, MAX_VISIBLE_PERIODS);
      for (const p of visible) {
        const modeClass = p.mode === 'off' ? 'schedule-tile__period-mode--off' : 'schedule-tile__period-mode--comfort';
        const modeLabel = p.mode === 'off' ? 'OFF' : (p.setpoint != null ? `${p.setpoint}\u00b0C` : 'COMFORT');
        periodsHtml += `
          <div class="schedule-tile__period">
            <span class="schedule-tile__period-name">${p.name}</span>
            <span class="schedule-tile__period-time">${p.start}\u2013${p.end}</span>
            <span class="schedule-tile__period-mode ${modeClass}">${modeLabel}</span>
          </div>
        `;
      }
      if (periods.length > MAX_VISIBLE_PERIODS) {
        periodsHtml += `<span class="schedule-tile__more">+${periods.length - MAX_VISIBLE_PERIODS} more</span>`;
      }
      if (periods.length === 0) {
        periodsHtml = '<span class="schedule-tile__more">No schedules configured</span>';
      }

      tile.innerHTML = `
        <span class="schedule-tile__name">${room.name}</span>
        <div class="schedule-tile__periods">${periodsHtml}</div>
      `;
      tile.addEventListener('click', () => {
        window.location.hash = `#tuning/${room.slug}`;
      });
      schedGrid.appendChild(tile);
    }
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

  const toggleBtn = container.querySelector('#sched-toggle');
  const toggleStatus = container.querySelector('#sched-toggle-status');

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

  function renderPeriods(schedData) {
    periodsContainer.innerHTML = '';
    const periods = schedData?.periods || [];

    if (periods.length === 0) {
      periodsContainer.innerHTML = '<p class="tuning-section__desc">No schedules configured for this room. Add schedules via the integration configuration.</p>';
      return;
    }

    for (const p of periods) {
      const card = document.createElement('div');
      card.className = 'card schedule-detail__period-card';

      const modeText = p.mode === 'off'
        ? `OFF (frost protection: ${p.frost_protection}\u00b0C)`
        : (p.setpoint != null ? `Comfort \u2014 ${p.setpoint}\u00b0C` : 'Comfort');

      let daysHtml = '';
      for (let d = 0; d < 7; d++) {
        const active = (p.days || []).includes(d);
        daysHtml += `<span class="schedule-detail__day${active ? ' schedule-detail__day--active' : ''}">${DAY_NAMES[d]}</span>`;
      }

      card.innerHTML = `
        <div class="schedule-detail__period-header">
          <span class="schedule-detail__period-name">${p.name}</span>
          <span class="schedule-detail__period-time">${p.start} \u2013 ${p.end}</span>
        </div>
        <div class="schedule-detail__days">${daysHtml}</div>
        <div class="schedule-detail__period-info">${modeText}</div>
      `;
      periodsContainer.appendChild(card);
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

  const schedData = getScheduleData(state);
  renderToggle(schedData);
  renderPeriods(schedData);

  return {
    update(newState) {
      state = newState;
      const newData = getScheduleData(newState);
      renderToggle(newData);
      renderPeriods(newData);
    },
    destroy() {},
  };
}
