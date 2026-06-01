import { formatNumber } from '../utils.js';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function renderSchedules(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderScheduleDetail(container, slug, rooms, state, connection, hass);
  }
  return renderScheduleIndex(container, rooms, state, connection);
}

// ---------------------------------------------------------------------------
// Index view — room schedule tiles
// ---------------------------------------------------------------------------

function renderScheduleIndex(container, rooms, state, connection) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'SCHEDULES';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Configure time-of-day schedules for each room. Schedules control setpoints, comfort bands, and heating mode throughout the day.';
  container.appendChild(desc);

  const grid = document.createElement('div');
  grid.className = 'grid-rooms';
  container.appendChild(grid);

  function buildTiles(st) {
    grid.innerHTML = '';
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
        window.location.hash = `#schedules/${room.slug}`;
      });
      grid.appendChild(tile);
    }
  }

  buildTiles(state);

  return {
    update(newState) {
      buildTiles(newState);
    },
    destroy() {},
  };
}

// ---------------------------------------------------------------------------
// Detail view — per-room schedule CRUD
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
  nav.innerHTML = '<span class="nav-back__arrow">\u2190</span> SCHEDULES';
  nav.addEventListener('click', () => { window.location.hash = '#schedules'; });
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

  function getDefaults(st) {
    const config = st[CONFIG_ENTITY]?.attributes || {};
    return {
      setpoint: config.room_setpoints?.[room.slug] ?? 21,
      comfort_offset: config.room_comfort_offsets?.[room.slug] ?? config.comfort_offset ?? 2.0,
      tracking_weight: config.tracking_weight ?? 0.0,
      energy_weight: config.energy_weight ?? 0.01,
    };
  }

  function renderToggle(schedData) {
    const enabled = schedData?.enabled ?? true;
    toggleBtn.textContent = enabled ? 'ENABLED' : 'DISABLED';
    toggleBtn.className = 'schedule-detail__toggle-btn ' +
      (enabled ? 'schedule-detail__toggle-btn--active' : 'schedule-detail__toggle-btn--inactive');
  }

  function initLocalPeriods(schedData) {
    const periods = schedData?.periods || [];
    localPeriods = periods.map((p) => ({
      ...p,
      days: [...(p.days || [0, 1, 2, 3, 4, 5, 6])],
    }));
    dirty = false;
  }

  function renderPeriodForms() {
    periodsContainer.innerHTML = '';
    const defaults = getDefaults(state);

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

      const isComfort = p.mode !== 'off';
      let paramsHtml = '';
      if (isComfort) {
        paramsHtml = `
          <div class="schedule-form__period-row">
            <div class="form-group">
              <label class="form-label">Setpoint (\u00b0C)</label>
              <input class="form-input form-input--time" type="number" step="0.5" min="5" max="35" value="${p.setpoint ?? defaults.setpoint}" data-field="setpoint">
            </div>
            <div class="form-group">
              <label class="form-label">Comfort Offset (\u00b1\u00b0C)</label>
              <input class="form-input form-input--time" type="number" step="0.1" min="0.1" max="5" value="${p.comfort_offset ?? defaults.comfort_offset}" data-field="comfort_offset">
              <span class="form-hint">Band half-width</span>
            </div>
            <div class="form-group">
              <label class="form-label">Tracking Weight</label>
              <input class="form-input form-input--time" type="number" step="0.1" min="0" max="10" value="${p.tracking_weight ?? defaults.tracking_weight}" data-field="tracking_weight">
              <span class="form-hint">Setpoint tracking strength</span>
            </div>
            <div class="form-group">
              <label class="form-label">Energy Weight</label>
              <input class="form-input form-input--time" type="number" step="0.01" min="0" max="10" value="${p.energy_weight ?? defaults.energy_weight}" data-field="energy_weight">
              <span class="form-hint">Energy-use penalty</span>
            </div>
          </div>
        `;
      } else {
        paramsHtml = `
          <div class="schedule-form__period-row">
            <div class="form-group">
              <label class="form-label">Frost Protection (\u00b0C)</label>
              <input class="form-input form-input--time" type="number" step="0.5" min="0" max="15" value="${p.frost_protection ?? 12}" data-field="frost_protection">
            </div>
          </div>
        `;
      }

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
        </div>
        <div class="schedule-form__days" data-period="${i}">${daysHtml}</div>
        ${paramsHtml}
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
              delete localPeriods[i].comfort_offset;
              delete localPeriods[i].tracking_weight;
              delete localPeriods[i].energy_weight;
              localPeriods[i].frost_protection = localPeriods[i].frost_protection ?? 12;
            } else {
              delete localPeriods[i].frost_protection;
              localPeriods[i].setpoint = localPeriods[i].setpoint ?? defaults.setpoint;
              localPeriods[i].comfort_offset = localPeriods[i].comfort_offset ?? defaults.comfort_offset;
              localPeriods[i].tracking_weight = localPeriods[i].tracking_weight ?? defaults.tracking_weight;
              localPeriods[i].energy_weight = localPeriods[i].energy_weight ?? defaults.energy_weight;
            }
            dirty = true;
            renderPeriodForms();
          } else if (['setpoint', 'frost_protection', 'comfort_offset', 'tracking_weight', 'energy_weight'].includes(field)) {
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
    const defaults = getDefaults(state);
    localPeriods.push({
      name: `Period ${localPeriods.length + 1}`,
      start: '08:00',
      end: '22:00',
      mode: 'comfort',
      setpoint: defaults.setpoint,
      comfort_offset: defaults.comfort_offset,
      tracking_weight: defaults.tracking_weight,
      energy_weight: defaults.energy_weight,
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
      const defaults = getDefaults(state);
      const periods = localPeriods.map((p) => {
        const out = { name: p.name, start: p.start, end: p.end, mode: p.mode, days: p.days };
        if (p.mode === 'off') {
          out.frost_protection = p.frost_protection ?? 12;
        } else {
          out.setpoint = p.setpoint ?? defaults.setpoint;
          if (p.comfort_offset != null && p.comfort_offset !== defaults.comfort_offset) {
            out.comfort_offset = p.comfort_offset;
          }
          if (p.tracking_weight != null && p.tracking_weight !== defaults.tracking_weight) {
            out.tracking_weight = p.tracking_weight;
          }
          if (p.energy_weight != null && p.energy_weight !== defaults.energy_weight) {
            out.energy_weight = p.energy_weight;
          }
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
