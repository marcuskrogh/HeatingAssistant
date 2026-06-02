const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function renderSchedules(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderScheduleDetail(container, slug, rooms, state, connection, hass);
  }
  return renderScheduleIndex(container, rooms, state, connection);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Robust lookup: tries slug, name, and case-insensitive normalised match. */
function getScheduleDataForRoom(roomSchedules, room) {
  if (roomSchedules[room.slug]) return roomSchedules[room.slug];
  if (roomSchedules[room.name]) return roomSchedules[room.name];
  const slug = room.slug.toLowerCase();
  for (const key of Object.keys(roomSchedules)) {
    if (key.toLowerCase().replace(/\s+/g, '_') === slug) return roomSchedules[key];
  }
  return null;
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

/** Returns { text, cls } for the mode label in a period row. */
function periodModeDisplay(p) {
  if (p.mode === 'off') return { text: 'OFF', cls: 'sched-row__mode--off' };
  if (p.setpoint != null) return { text: `${p.setpoint}°C`, cls: 'sched-row__mode--comfort' };
  return { text: 'COMFORT', cls: 'sched-row__mode--comfort' };
}

/** Renders a single period summary row element. */
function makePeriodRow(p, isActive) {
  const { text: modeText, cls: modeCls } = periodModeDisplay(p);
  const row = document.createElement('div');
  row.className = 'sched-row' + (isActive ? ' sched-row--active' : '');
  row.innerHTML = `
    ${isActive ? '<span class="sched-row__now-badge">NOW</span>' : ''}
    <span class="sched-row__name">${p.name || 'Period'}</span>
    <span class="sched-row__time">${p.start}–${p.end}</span>
    <span class="sched-row__mode ${modeCls}">${modeText}</span>
  `;
  return row;
}

// ---------------------------------------------------------------------------
// Index view — room schedule cards
// ---------------------------------------------------------------------------

function renderScheduleIndex(container, rooms, state, connection) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'SCHEDULES';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Configure time-of-day schedules for each room. Click a card to view, edit, or add periods.';
  container.appendChild(desc);

  const grid = document.createElement('div');
  grid.className = 'grid-rooms';
  container.appendChild(grid);

  function buildCards(st) {
    grid.innerHTML = '';
    const config = st[CONFIG_ENTITY]?.attributes || {};
    const roomSchedules = config.room_schedules || {};

    for (const room of rooms) {
      const schedData = getScheduleDataForRoom(roomSchedules, room);
      const periods = schedData?.periods || [];
      const enabled = schedData?.enabled ?? true;

      const activePeriod = findActivePeriod(periods);
      // All periods except the currently active one, for the summary list
      const otherPeriods = periods.filter((p) => p !== activePeriod);

      const card = document.createElement('div');
      card.className = 'card card--clickable sched-index-card';

      // ── Header: room name + enabled badge ────────────────────────────────
      const cardHeader = document.createElement('div');
      cardHeader.className = 'sched-index-card__header';
      cardHeader.innerHTML = `
        <span class="sched-index-card__name">${room.name}</span>
        <span class="sched-index-card__badge ${enabled ? 'sched-index-card__badge--on' : 'sched-index-card__badge--off'}">
          ${enabled ? 'ENABLED' : 'DISABLED'}
        </span>
      `;
      card.appendChild(cardHeader);

      // ── Currently active period (or placeholder) ──────────────────────────
      if (activePeriod) {
        card.appendChild(makePeriodRow(activePeriod, true));
      } else if (periods.length > 0) {
        const noActive = document.createElement('div');
        noActive.className = 'sched-index-card__no-active';

        const nextPeriod = findNextPeriod(periods);
        if (nextPeriod) {
          const { text: modeText, cls: modeCls } = periodModeDisplay(nextPeriod);
          noActive.innerHTML = `
            <span class="sched-index-card__next-label">NEXT →</span>
            <span class="sched-row__name">${nextPeriod.name || 'Period'}</span>
            <span class="sched-row__time">${nextPeriod.start}–${nextPeriod.end}</span>
            <span class="sched-row__mode ${modeCls}">${modeText}</span>
          `;
        } else {
          noActive.textContent = 'No period active today';
        }
        card.appendChild(noActive);
      } else {
        const empty = document.createElement('div');
        empty.className = 'sched-index-card__empty';
        empty.textContent = 'No periods configured — click to add';
        card.appendChild(empty);
      }

      // ── Summary list of other periods (up to 3) ───────────────────────────
      if (otherPeriods.length > 0) {
        const sep = document.createElement('div');
        sep.className = 'sched-index-card__sep';
        card.appendChild(sep);

        const list = document.createElement('div');
        list.className = 'sched-index-card__list';

        const preview = otherPeriods.slice(0, 3);
        const overflow = otherPeriods.length - preview.length;

        for (const p of preview) {
          list.appendChild(makePeriodRow(p, false));
        }

        if (overflow > 0) {
          const more = document.createElement('div');
          more.className = 'sched-index-card__overflow';
          more.textContent = `+${overflow} more…`;
          list.appendChild(more);
        }

        card.appendChild(list);
      }

      card.addEventListener('click', () => {
        window.location.hash = `#schedules/${room.slug}`;
      });
      grid.appendChild(card);
    }
  }

  buildCards(state);

  return {
    update(newState) { buildCards(newState); },
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

  // Back nav
  const nav = document.createElement('button');
  nav.className = 'nav-back';
  nav.innerHTML = '<span class="nav-back__arrow">←</span> SCHEDULES';
  nav.addEventListener('click', () => { window.location.hash = '#schedules'; });
  container.appendChild(nav);

  // Room title
  const roomHeader = document.createElement('div');
  roomHeader.className = 'room-header';
  roomHeader.innerHTML = `<h2 class="room-header__title">${room.name}</h2>`;
  container.appendChild(roomHeader);

  // Enable/disable toggle
  const toggleRow = document.createElement('div');
  toggleRow.className = 'schedule-detail__toggle';
  toggleRow.innerHTML = `
    <span class="schedule-detail__toggle-label">Schedule:</span>
    <button class="schedule-detail__toggle-btn" id="sched-toggle"></button>
    <span class="tuning-actions__status" id="sched-toggle-status"></span>
  `;
  container.appendChild(toggleRow);

  // Periods section header (title + Add button side by side)
  const periodsHeader = document.createElement('div');
  periodsHeader.className = 'sched-detail__section-header';
  periodsHeader.innerHTML = `
    <span class="sched-detail__section-title" id="sched-periods-title">PERIODS</span>
    <button class="btn btn--primary btn--sm" id="btn-add-period">+ Add Period</button>
  `;
  container.appendChild(periodsHeader);

  // Period form cards live here
  const periodsContainer = document.createElement('div');
  periodsContainer.id = 'periods-container';
  container.appendChild(periodsContainer);

  // Save row at the bottom
  const actionsRow = document.createElement('div');
  actionsRow.className = 'tuning-actions';
  actionsRow.style.marginTop = '20px';
  actionsRow.innerHTML = `
    <button class="btn btn--primary" id="btn-save-schedule">Save Changes</button>
    <span class="tuning-actions__status" id="sched-save-status"></span>
  `;
  container.appendChild(actionsRow);

  const toggleBtn = container.querySelector('#sched-toggle');
  const toggleStatus = container.querySelector('#sched-toggle-status');
  const periodsTitleEl = container.querySelector('#sched-periods-title');
  const btnAdd = container.querySelector('#btn-add-period');
  const btnSave = container.querySelector('#btn-save-schedule');
  const saveStatus = container.querySelector('#sched-save-status');

  let localPeriods = [];
  let dirty = false;
  // Tracks which period indices are currently expanded in the UI
  let expandedSet = new Set();

  function getScheduleData(st) {
    const config = st[CONFIG_ENTITY]?.attributes || {};
    const roomSchedules = config.room_schedules || {};
    return getScheduleDataForRoom(roomSchedules, room);
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
    expandedSet = new Set();
    dirty = false;
  }

  function renderPeriodForms() {
    const defaults = getDefaults(state);
    const activePeriod = findActivePeriod(localPeriods);

    periodsTitleEl.textContent = localPeriods.length > 0
      ? `PERIODS (${localPeriods.length})`
      : 'PERIODS';

    periodsContainer.innerHTML = '';

    if (localPeriods.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'sched-detail__empty';
      empty.innerHTML = `
        <p>No periods configured for this room.</p>
        <p>Click <strong>+ Add Period</strong> above to create a schedule.</p>
      `;
      periodsContainer.appendChild(empty);
      return;
    }

    for (let i = 0; i < localPeriods.length; i++) {
      const p = localPeriods[i];
      const isActive = (p === activePeriod);
      const isExpanded = expandedSet.has(i);
      const { text: modeText, cls: modeCls } = periodModeDisplay(p);

      const card = document.createElement('div');
      card.className = 'card schedule-form__period' +
        (isActive ? ' schedule-form__period--active' : '') +
        (isExpanded ? ' schedule-form__period--expanded' : '');

      // ── Collapsed header — always visible ──────────────────────────────────
      const cardHeader = document.createElement('div');
      cardHeader.className = 'schedule-form__period-header';
      cardHeader.innerHTML = `
        ${isActive ? '<span class="sched-detail__now-badge">NOW</span>' : ''}
        <span class="schedule-form__period-name">${p.name || 'Period'}</span>
        <span class="schedule-form__period-time">${p.start || '—'}–${p.end || '—'}</span>
        <span class="sched-row__mode ${modeCls}">${modeText}</span>
        <button class="schedule-form__delete" title="Delete period">×</button>
        <span class="schedule-form__expand-chevron">${isExpanded ? '▲' : '▼'}</span>
      `;
      card.appendChild(cardHeader);

      // ── Expandable body — hidden when collapsed ────────────────────────────
      const cardBody = document.createElement('div');
      cardBody.className = 'schedule-form__period-body';
      if (!isExpanded) cardBody.hidden = true;

      const modeOptions = `
        <option value="comfort"${p.mode !== 'off' ? ' selected' : ''}>Comfort</option>
        <option value="off"${p.mode === 'off' ? ' selected' : ''}>Off</option>
      `;

      let daysHtml = '';
      for (let d = 0; d < 7; d++) {
        const on = (p.days || []).includes(d);
        daysHtml += `<span class="schedule-form__day${on ? ' schedule-form__day--active' : ''}" data-day="${d}">${DAY_NAMES[d]}</span>`;
      }

      const isComfort = p.mode !== 'off';
      const paramsHtml = isComfort ? `
        <div class="schedule-form__period-row">
          <div class="form-group">
            <label class="form-label">Setpoint (°C)</label>
            <input class="form-input form-input--time" type="number" step="0.5" min="5" max="35"
              value="${p.setpoint ?? defaults.setpoint}" data-field="setpoint">
          </div>
          <div class="form-group">
            <label class="form-label">Comfort Offset (±°C)</label>
            <input class="form-input form-input--time" type="number" step="0.1" min="0.1" max="5"
              value="${p.comfort_offset ?? defaults.comfort_offset}" data-field="comfort_offset">
            <span class="form-hint">Band half-width</span>
          </div>
          <div class="form-group">
            <label class="form-label">Tracking Weight</label>
            <input class="form-input form-input--time" type="number" step="0.1" min="0" max="10"
              value="${p.tracking_weight ?? defaults.tracking_weight}" data-field="tracking_weight">
            <span class="form-hint">Setpoint tracking strength</span>
          </div>
          <div class="form-group">
            <label class="form-label">Energy Weight</label>
            <input class="form-input form-input--time" type="number" step="0.01" min="0" max="10"
              value="${p.energy_weight ?? defaults.energy_weight}" data-field="energy_weight">
            <span class="form-hint">Energy-use penalty</span>
          </div>
        </div>
      ` : `
        <div class="schedule-form__period-row">
          <div class="form-group">
            <label class="form-label">Frost Protection (°C)</label>
            <input class="form-input form-input--time" type="number" step="0.5" min="0" max="15"
              value="${p.frost_protection ?? 12}" data-field="frost_protection">
          </div>
        </div>
      `;

      cardBody.innerHTML = `
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

      card.appendChild(cardBody);
      periodsContainer.appendChild(card);

      // Toggle expansion on header click (except the delete button)
      cardHeader.addEventListener('click', (e) => {
        if (e.target.closest('.schedule-form__delete')) return;
        const willExpand = !expandedSet.has(i);
        if (willExpand) expandedSet.add(i); else expandedSet.delete(i);
        card.classList.toggle('schedule-form__period--expanded', willExpand);
        cardBody.hidden = !willExpand;
        cardHeader.querySelector('.schedule-form__expand-chevron').textContent = willExpand ? '▲' : '▼';
      });

      // Delete — rebuild expandedSet with shifted indices
      cardHeader.querySelector('.schedule-form__delete').addEventListener('click', (e) => {
        e.stopPropagation();
        const newSet = new Set();
        for (const idx of expandedSet) {
          if (idx < i) newSet.add(idx);
          else if (idx > i) newSet.add(idx - 1);
        }
        expandedSet = newSet;
        localPeriods.splice(i, 1);
        dirty = true;
        renderPeriodForms();
      });

      // Wire all [data-field] inputs/selects inside body
      cardBody.querySelectorAll('[data-field]').forEach((input) => {
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
            expandedSet.add(i); // keep this card open after re-render
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

      // Wire day toggles
      cardBody.querySelectorAll('.schedule-form__day').forEach((dayEl) => {
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
    }
  }

  // Toggle enable/disable
  toggleBtn.addEventListener('click', async () => {
    const schedData = getScheduleData(state);
    const currentEnabled = schedData?.enabled ?? true;
    toggleStatus.textContent = 'Saving…';
    toggleStatus.className = 'tuning-actions__status tuning-actions__status--running';
    try {
      await hass.callService('heating_assistant', 'set_schedule_enabled', {
        room_name: room.slug,
        enabled: !currentEnabled,
      });
      // Patch state optimistically for the enabled flag
      patchStateSchedule(state, room.slug, localPeriods, !currentEnabled);
      toggleStatus.textContent = '';
      toggleStatus.className = 'tuning-actions__status';
      renderToggle({ enabled: !currentEnabled });
    } catch (err) {
      toggleStatus.textContent = 'Error: ' + (err.message || err);
      toggleStatus.className = 'tuning-actions__status tuning-actions__status--error';
    }
  });

  // Add a blank period — auto-expand it
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
    expandedSet.add(localPeriods.length - 1); // expand the new card
    renderPeriodForms();
    const cards = periodsContainer.querySelectorAll('.schedule-form__period');
    if (cards.length > 0) cards[cards.length - 1].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });

  // Save all periods
  btnSave.addEventListener('click', async () => {
    saveStatus.textContent = 'Saving…';
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
          if (p.comfort_offset != null) out.comfort_offset = p.comfort_offset;
          if (p.tracking_weight != null) out.tracking_weight = p.tracking_weight;
          if (p.energy_weight != null) out.energy_weight = p.energy_weight;
        }
        return out;
      });
      await hass.callService('heating_assistant', 'update_room_schedule', {
        room_name: room.slug,
        periods,
      });

      // Patch the shared state object immediately — prevents any intermediate
      // state_changed events (from other entities) from wiping localPeriods
      // before the config entity's own state_changed arrives from the server.
      patchStateSchedule(state, room.slug, periods);

      // Re-sync localPeriods to the canonical saved data and re-render the
      // form right away so the user sees the saved state without waiting for
      // the server's state_changed event.
      localPeriods = periods.map((p) => ({ ...p, days: [...(p.days || [])] }));
      renderPeriodForms();

      dirty = false;
      saveStatus.textContent = 'Saved.';
      saveStatus.className = 'tuning-actions__status tuning-actions__status--success';
    } catch (err) {
      saveStatus.textContent = 'Error: ' + (err.message || err);
      saveStatus.className = 'tuning-actions__status tuning-actions__status--error';
    }
    btnSave.disabled = false;
  });

  // Initial render from current state
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
// Shared helper — optimistically patch the config entity in the state object
// ---------------------------------------------------------------------------

function patchStateSchedule(state, slug, periods, enabled) {
  const existingEntity = state[CONFIG_ENTITY] || {
    entity_id: CONFIG_ENTITY,
    state: 'ok',
    attributes: {},
  };
  const existingAttrs = existingEntity.attributes || {};
  const existingSchedules = existingAttrs.room_schedules || {};
  const resolvedEnabled = enabled !== undefined
    ? enabled
    : (existingSchedules[slug]?.enabled ?? true);

  state[CONFIG_ENTITY] = {
    ...existingEntity,
    attributes: {
      ...existingAttrs,
      room_schedules: {
        ...existingSchedules,
        [slug]: { enabled: resolvedEnabled, periods },
      },
    },
  };
}
