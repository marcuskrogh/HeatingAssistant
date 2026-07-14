import { findActivePeriod, findNextPeriod, periodModeDisplay, formatPeriodTime, serializeSchedulePeriod } from '../schedule-utils.js?v=96';
import { setPanelHash } from '../panel-hash.js?v=96';
import { setScheduleEnabled, updateRoomSchedule } from '../ha-services.js?v=96';
import { getScheduleDataForRoom, patchStateSchedule, CONFIG_ENTITY } from './schedules-shared.js?v=96';
import { renderExperimentsSection } from './schedules-experiments.js?v=96';
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function renderScheduleDetail(container, roomSlug, rooms, state, connection, hass) {
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
  nav.addEventListener('click', () => { setPanelHash('#schedules'); });
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

  // Comfort periods section header (title + Add button side by side)
  const periodsHeader = document.createElement('div');
  periodsHeader.className = 'sched-detail__section-header';
  periodsHeader.innerHTML = `
    <span class="sched-detail__section-title" id="sched-periods-title">COMFORT PERIODS</span>
    <button class="btn btn--primary btn--sm" id="btn-add-period">+ Add Period</button>
  `;
  container.appendChild(periodsHeader);

  const comfortDesc = document.createElement('p');
  comfortDesc.className = 'tuning-section__desc';
  comfortDesc.style.margin = '0 0 14px';
  comfortDesc.textContent = 'Daily comfort windows that set target temperature and operating mode for this room.';
  container.appendChild(comfortDesc);

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
  let scheduleLoadGen = 0;
  /** Periods last persisted successfully; held until WS confirms the same payload. */
  let savedScheduleSnapshot = null;
  // Tracks which period indices are currently expanded in the UI
  let expandedSet = new Set();

  function getScheduleFromWS(roomSchedules) {
    return getScheduleDataForRoom(roomSchedules, room);
  }

  function getScheduleFromState() {
    return getScheduleDataForRoom(state[CONFIG_ENTITY]?.attributes?.room_schedules, room);
  }

  function periodsMatch(a, b) {
    const left = a ?? [];
    const right = b ?? [];
    if (left.length !== right.length) return false;
    return JSON.stringify(left) === JSON.stringify(right);
  }

  /**
   * Prefer WebSocket data when authoritative; fall back to patched config-entity
   * state (or the last successful save) when WS is stale, empty, or errored.
   */
  function resolveScheduleData(roomSchedules) {
    const fromWs = getScheduleFromWS(roomSchedules);
    const wsPeriods = fromWs?.periods ?? [];

    if (savedScheduleSnapshot !== null) {
      const snapLen = savedScheduleSnapshot.length;
      const wsLen = wsPeriods.length;
      if (wsLen < snapLen || wsLen > snapLen) {
        const fromState = getScheduleFromState();
        const enabled = fromState?.enabled ?? fromWs?.enabled ?? true;
        return { enabled, periods: [...savedScheduleSnapshot] };
      }
      if (periodsMatch(wsPeriods, savedScheduleSnapshot)) {
        savedScheduleSnapshot = null;
      } else {
        // Same count but field ordering/extra keys differ — WS caught up.
        savedScheduleSnapshot = null;
      }
    }

    if (wsPeriods.length > 0) {
      return fromWs;
    }

    const fromState = getScheduleFromState();
    if ((fromState?.periods?.length ?? 0) > 0) {
      return fromState;
    }

    return fromWs ?? fromState ?? null;
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
      enabled: p.enabled !== false,
      recurring: p.recurring !== false,
      all_day: !!p.all_day,
    }));
    expandedSet = new Set();
    dirty = false;
  }

  function applySchedulePayload(roomSchedules) {
    const newData = resolveScheduleData(roomSchedules);
    renderToggle(newData);
    if (!dirty) {
      initLocalPeriods(newData);
      renderPeriodForms();
    }
  }

  function fetchSchedules() {
    const gen = ++scheduleLoadGen;
    return connection.getSchedules().then((roomSchedules) => {
      if (gen !== scheduleLoadGen) return;
      applySchedulePayload(roomSchedules);
    });
  }

  function renderPeriodForms() {
    const defaults = getDefaults(state);
    const activePeriod = findActivePeriod(localPeriods);
    const nextPeriod = findNextPeriod(localPeriods);

    periodsTitleEl.textContent = localPeriods.length > 0
      ? `COMFORT PERIODS (${localPeriods.length})`
      : 'COMFORT PERIODS';

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
      const isNext = (p === nextPeriod);
      const isExpanded = expandedSet.has(i);
      const periodEnabled = p.enabled !== false;
      const { text: modeText, cls: modeCls } = periodModeDisplay(p);

      const card = document.createElement('div');
      card.className = 'card schedule-form__period' +
        (isActive ? ' schedule-form__period--active' : '') +
        (isNext ? ' schedule-form__period--next' : '') +
        (isExpanded ? ' schedule-form__period--expanded' : '') +
        (!periodEnabled ? ' schedule-form__period--disabled' : '');

      // ── Collapsed header — always visible ──────────────────────────────────
      const cardHeader = document.createElement('div');
      cardHeader.className = 'schedule-form__period-header';
      cardHeader.innerHTML = `
        ${isActive ? '<span class="sched-detail__now-badge">NOW</span>' : ''}
        ${isNext ? '<span class="sched-detail__next-badge">NEXT</span>' : ''}
        <button type="button" class="sched-period-toggle ${periodEnabled ? 'sched-period-toggle--on' : 'sched-period-toggle--off'}" data-action="toggle-enabled" title="${periodEnabled ? 'Disable period' : 'Enable period'}">${periodEnabled ? 'ON' : 'OFF'}</button>
        <span class="schedule-form__period-name">${p.name || 'Period'}</span>
        <span class="schedule-form__period-time">${formatPeriodTime(p)}</span>
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
      const isRecurring = p.recurring !== false;
      const isAllDay = !!p.all_day;
      const today = new Date();
      const pad = (n) => String(n).padStart(2, '0');
      const defaultDate = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;

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
            <label class="form-label">Mode</label>
            <select class="schedule-form__mode-select" data-field="mode">${modeOptions}</select>
          </div>
        </div>
        <div class="schedule-form__flags">
          <label class="schedule-form__flag">
            <input type="checkbox" data-field="all_day"${isAllDay ? ' checked' : ''}>
            <span>All day</span>
          </label>
          <label class="schedule-form__flag">
            <input type="checkbox" data-field="recurring"${isRecurring ? ' checked' : ''}>
            <span>Recurring weekly</span>
          </label>
        </div>
        <div class="schedule-form__period-row schedule-form__time-row"${isAllDay ? ' hidden' : ''}>
          <div class="form-group">
            <label class="form-label">Start</label>
            <input class="form-input form-input--time" type="time" value="${p.start || '08:00'}" data-field="start">
          </div>
          <div class="form-group">
            <label class="form-label">End</label>
            <input class="form-input form-input--time" type="time" value="${p.end || '22:00'}" data-field="end">
          </div>
        </div>
        <div class="schedule-form__date-row"${isRecurring ? ' hidden' : ''}>
          <div class="form-group">
            <label class="form-label">Start date</label>
            <input class="form-input" type="date" value="${p.start_date || defaultDate}" data-field="start_date">
          </div>
          <div class="form-group">
            <label class="form-label">End date</label>
            <input class="form-input" type="date" value="${p.end_date || defaultDate}" data-field="end_date">
          </div>
        </div>
        <div class="schedule-form__days" data-period="${i}"${isRecurring ? '' : ' hidden'}>${daysHtml}</div>
        ${paramsHtml}
      `;

      card.appendChild(cardBody);
      periodsContainer.appendChild(card);

      // Toggle expansion on header click (except action buttons)
      cardHeader.addEventListener('click', (e) => {
        if (e.target.closest('.schedule-form__delete, [data-action="toggle-enabled"]')) return;
        const willExpand = !expandedSet.has(i);
        if (willExpand) expandedSet.add(i); else expandedSet.delete(i);
        card.classList.toggle('schedule-form__period--expanded', willExpand);
        cardBody.hidden = !willExpand;
        cardHeader.querySelector('.schedule-form__expand-chevron').textContent = willExpand ? '▲' : '▼';
      });

      // Per-period enable toggle
      cardHeader.querySelector('[data-action="toggle-enabled"]').addEventListener('click', (e) => {
        e.stopPropagation();
        localPeriods[i].enabled = !(localPeriods[i].enabled !== false);
        dirty = true;
        renderPeriodForms();
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
        const eventName = input.type === 'checkbox' ? 'change' : 'change';
        input.addEventListener(eventName, () => {
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
            expandedSet.add(i);
            renderPeriodForms();
          } else if (field === 'all_day') {
            localPeriods[i].all_day = input.checked;
            if (input.checked) {
              localPeriods[i].start = '00:00';
              localPeriods[i].end = '23:59';
            }
            dirty = true;
            expandedSet.add(i);
            renderPeriodForms();
          } else if (field === 'recurring') {
            localPeriods[i].recurring = input.checked;
            if (!input.checked) {
              const d = new Date();
              const padN = (n) => String(n).padStart(2, '0');
              const iso = `${d.getFullYear()}-${padN(d.getMonth() + 1)}-${padN(d.getDate())}`;
              localPeriods[i].start_date = localPeriods[i].start_date || iso;
              localPeriods[i].end_date = localPeriods[i].end_date || iso;
            }
            dirty = true;
            expandedSet.add(i);
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
    const roomSchedules = await connection.getSchedules();
    const schedData = getScheduleFromWS(roomSchedules);
    const currentEnabled = schedData?.enabled ?? true;
    toggleStatus.textContent = 'Saving…';
    toggleStatus.className = 'tuning-actions__status tuning-actions__status--running';
    try {
      await setScheduleEnabled(hass, room.slug, !currentEnabled);
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
      enabled: true,
      recurring: true,
      all_day: false,
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
      const periods = localPeriods.map((p) => serializeSchedulePeriod(p, defaults));
      await updateRoomSchedule(hass, room.slug, periods);

      // Hold the saved payload until WS confirms it — prevents stale/empty
      // getSchedules() responses from wiping the form after save.
      savedScheduleSnapshot = periods.map((p) => ({ ...p, days: [...(p.days || [])] }));

      // Invalidate in-flight schedule reloads started before this save.
      scheduleLoadGen++;

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

  // Initial render — fetch from WebSocket to get persisted schedule data
  fetchSchedules();

  // Add experiment section
  const expSection = renderExperimentsSection(container, room, connection, hass);

  return {
    update(newState) {
      state = newState;
      fetchSchedules();
    },
    destroy() { expSection.destroy(); },
  };
}
