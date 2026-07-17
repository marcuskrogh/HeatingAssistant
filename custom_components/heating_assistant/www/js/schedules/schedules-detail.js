import {
  activeOverrideFields,
  findActivePeriod,
  findNextPeriod,
  formatPeriodPreview,
  hasOverride,
  isPeriodInactive,
  normalizePeriodForEditor,
  OVERRIDE_META,
  overrideBaseline,
  SCHEDULE_TYPE_CONTINUOUS,
  SCHEDULE_TYPE_DATE_RANGE,
  SCHEDULE_TYPE_WEEKLY,
  serializeSchedulePeriod,
} from '../schedule-utils.js?v=107';
import { setPanelHash } from '../panel-hash.js?v=107';
import { setScheduleEnabled, updateRoomSchedule } from '../ha-services.js?v=107';
import { getScheduleDataForRoom, patchStateSchedule, periodsMatch, resolveRoomScheduleData, CONFIG_ENTITY } from './schedules-shared.js?v=107';
import { renderExperimentsSection } from './schedules-experiments.js?v=107';
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

  function resolveScheduleData(roomSchedules) {
    const resolved = resolveRoomScheduleData(
      room,
      roomSchedules,
      state,
      savedScheduleSnapshot,
    );
    if (savedScheduleSnapshot !== null) {
      const wsPeriods = getScheduleFromWS(roomSchedules)?.periods ?? [];
      if (periodsMatch(wsPeriods, savedScheduleSnapshot)) {
        savedScheduleSnapshot = null;
      }
    }
    return resolved;
  }

  function getDefaults(st) {
    const config = st[CONFIG_ENTITY]?.attributes || {};
    return {
      setpoint: config.room_setpoints?.[room.slug] ?? 21,
      comfort_offset: config.room_comfort_offsets?.[room.slug] ?? config.comfort_offset ?? 2.0,
      tracking_weight: 1.0,
      energy_weight: 1.0,
      frost_protection: 12,
    };
  }

  function escapeAttr(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function ensureWhenState(period) {
    const normalized = normalizePeriodForEditor(period);
    period._whenByType = normalized._whenByType;
    period.schedule_type = normalized.schedule_type;
    return period._whenByType;
  }

  function typeLabel(type) {
    if (type === SCHEDULE_TYPE_DATE_RANGE) return 'Date range';
    if (type === SCHEDULE_TYPE_CONTINUOUS) return 'Continuous span';
    return 'Weekly recurring';
  }

  function segmentedHtml(field, value, options, extraClass = '') {
    return `<div class="schedule-form__segmented ${extraClass}" data-segmented-field="${field}">
      ${options.map((opt) => `
        <button type="button"
          class="schedule-form__segment${value === opt.value ? ' schedule-form__segment--active' : ''}"
          data-value="${opt.value}">${opt.label}</button>
      `).join('')}
    </div>`;
  }

  function overrideInputHtml(field, period, defaults) {
    const meta = OVERRIDE_META[field];
    const value = hasOverride(period, field) ? period[field] : overrideBaseline(field, defaults);
    const unit = meta.unit ? `<span class="form-hint">${meta.unit}</span>` : '';
    return `
      <div class="schedule-form__override" data-override="${field}">
        <div class="schedule-form__override-main">
          <label class="form-label">${meta.label}</label>
          <div class="schedule-form__override-control">
            <input class="form-input form-input--time" type="number" step="${meta.step}" min="${meta.min}" max="${meta.max}"
              value="${escapeAttr(value)}" data-field="${field}">
            <button type="button" class="schedule-form__override-remove" data-remove-override="${field}" title="Return to inherit">Remove</button>
          </div>
          <span class="form-hint">${meta.hint}${unit ? ` - ${unit}` : ''}</span>
        </div>
      </div>
    `;
  }

  function overridesHtml(period, defaults) {
    const modeFields = activeOverrideFields(period.mode || 'comfort');
    const shownFields = modeFields.filter((field) => hasOverride(period, field));
    const addable = modeFields.filter((field) => !hasOverride(period, field));
    const rows = shownFields.map((field) => overrideInputHtml(field, period, defaults)).join('');
    const empty = rows ? '' : '<p class="schedule-form__section-empty">No overrides. This period inherits room/default values.</p>';
    const picker = addable.length > 0 ? `
      <div class="schedule-form__override-picker">
        <select class="schedule-form__mode-select" data-action="override-picker" aria-label="Override to add">
          ${addable.map((field) => `<option value="${field}">${OVERRIDE_META[field].label}</option>`).join('')}
        </select>
        <button type="button" class="btn btn--sm" data-action="add-override">Add override</button>
      </div>
    ` : '<p class="schedule-form__section-empty">All overrides for this mode are shown.</p>';
    return `${empty}${rows}${picker}`;
  }

  function renderToggle(schedData) {
    const enabled = schedData?.enabled ?? true;
    toggleBtn.textContent = enabled ? 'ENABLED' : 'DISABLED';
    toggleBtn.className = 'schedule-detail__toggle-btn ' +
      (enabled ? 'schedule-detail__toggle-btn--active' : 'schedule-detail__toggle-btn--inactive');
  }

  function initLocalPeriods(schedData) {
    const periods = schedData?.periods || [];
    localPeriods = periods.map((p) => normalizePeriodForEditor({
      ...p,
      days: [...(p.days || [0, 1, 2, 3, 4, 5, 6])],
      enabled: p.enabled !== false,
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

  function renderPeriodCard(i, defaults, activePeriod, nextPeriod, parent) {

    const p = localPeriods[i];

    const isActive = (p === activePeriod);

    const isNext = (p === nextPeriod);

    const isExpanded = expandedSet.has(i);

    const periodEnabled = p.enabled !== false;

    const preview = formatPeriodPreview(p);



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

      <span class="sched-row__type">${escapeAttr(preview.type)}</span>

      <span class="schedule-form__period-name">${escapeAttr(preview.name)}</span>

      <span class="schedule-form__period-time">${escapeAttr(preview.timing)}</span>

      <span class="sched-row__mode ${preview.modeCls}">${escapeAttr(preview.mode)}</span>

      <button class="schedule-form__delete" title="Delete period">×</button>

      <span class="schedule-form__expand-chevron">${isExpanded ? '▲' : '▼'}</span>

    `;

    card.appendChild(cardHeader);



    // ── Expandable body — hidden when collapsed ────────────────────────────

    const cardBody = document.createElement('div');

    cardBody.className = 'schedule-form__period-body';

    if (!isExpanded) cardBody.hidden = true;



    const scheduleType = p.schedule_type || SCHEDULE_TYPE_WEEKLY;

    const whenByType = ensureWhenState(p);

    const weeklyWhen = whenByType[SCHEDULE_TYPE_WEEKLY];

    const dateWhen = whenByType[SCHEDULE_TYPE_DATE_RANGE];

    const continuousWhen = whenByType[SCHEDULE_TYPE_CONTINUOUS];

    const activeWhen = whenByType[scheduleType];

    const timeMode = activeWhen?.time_mode || 'window';

    let daysHtml = '';

    for (let d = 0; d < 7; d++) {

      const on = (weeklyWhen.days || []).includes(d);

      daysHtml += `<span class="schedule-form__day${on ? ' schedule-form__day--active' : ''}" data-day="${d}">${DAY_NAMES[d]}</span>`;

    }



    let whenHtml = '';

    if (scheduleType === SCHEDULE_TYPE_WEEKLY) {

      whenHtml = `

        <div class="schedule-form__days" data-period="${i}">${daysHtml}</div>

        ${segmentedHtml('time_mode', timeMode, [

          { value: 'all_day', label: 'All day' },

          { value: 'window', label: 'Time window' },

        ], 'schedule-form__segmented--compact')}

        <div class="schedule-form__period-row schedule-form__time-row"${timeMode === 'all_day' ? ' hidden' : ''}>

          <div class="form-group">

            <label class="form-label">Start time</label>

            <input class="form-input form-input--time" type="time" value="${escapeAttr(weeklyWhen.start || '08:00')}" data-when-field="start">

          </div>

          <div class="form-group">

            <label class="form-label">End time</label>

            <input class="form-input form-input--time" type="time" value="${escapeAttr(weeklyWhen.end || '22:00')}" data-when-field="end">

          </div>

        </div>

      `;

    } else if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {

      whenHtml = `

        <div class="schedule-form__date-row">

          <div class="form-group">

            <label class="form-label">Start date</label>

            <input class="form-input" type="date" value="${escapeAttr(dateWhen.start_date)}" data-when-field="start_date">

          </div>

          <div class="form-group">

            <label class="form-label">End date</label>

            <input class="form-input" type="date" value="${escapeAttr(dateWhen.end_date)}" data-when-field="end_date">

          </div>

        </div>

        ${segmentedHtml('time_mode', timeMode, [

          { value: 'all_day', label: 'All day' },

          { value: 'window', label: 'Time window' },

        ], 'schedule-form__segmented--compact')}

        <div class="schedule-form__period-row schedule-form__time-row"${timeMode === 'all_day' ? ' hidden' : ''}>

          <div class="form-group">

            <label class="form-label">Start time</label>

            <input class="form-input form-input--time" type="time" value="${escapeAttr(dateWhen.start || '08:00')}" data-when-field="start">

          </div>

          <div class="form-group">

            <label class="form-label">End time</label>

            <input class="form-input form-input--time" type="time" value="${escapeAttr(dateWhen.end || '22:00')}" data-when-field="end">

          </div>

        </div>

      `;

    } else {

      whenHtml = `

        <div class="schedule-form__date-row">

          <div class="form-group">

            <label class="form-label">Start datetime</label>

            <input class="form-input" type="datetime-local" value="${escapeAttr(continuousWhen.start_at)}" data-when-field="start_at">

          </div>

          <div class="form-group">

            <label class="form-label">End datetime</label>

            <input class="form-input" type="datetime-local" value="${escapeAttr(continuousWhen.end_at)}" data-when-field="end_at">

          </div>

        </div>

      `;

    }



    cardBody.innerHTML = `

      <div class="schedule-form__editor-section">

        <div class="schedule-form__section-title">Type</div>

        ${segmentedHtml('schedule_type', scheduleType, [

          { value: SCHEDULE_TYPE_WEEKLY, label: 'Weekly recurring' },

          { value: SCHEDULE_TYPE_DATE_RANGE, label: 'Date range' },

          { value: SCHEDULE_TYPE_CONTINUOUS, label: 'Continuous span' },

        ])}

      </div>

      <div class="schedule-form__editor-section">

        <div class="schedule-form__section-title">Name</div>

        <div class="form-group">

          <input class="form-input form-input--name" type="text" value="${escapeAttr(p.name || '')}" data-field="name">

        </div>

      </div>

      <div class="schedule-form__editor-section">

        <div class="schedule-form__section-title">When <span class="schedule-form__section-subtitle">${typeLabel(scheduleType)}</span></div>

        ${whenHtml}

      </div>

      <div class="schedule-form__editor-section">

        <div class="schedule-form__section-title">Behaviour</div>

        ${segmentedHtml('mode', p.mode === 'off' ? 'off' : 'comfort', [

          { value: 'comfort', label: 'Comfort' },

          { value: 'off', label: 'Off' },

        ], 'schedule-form__segmented--compact')}

      </div>

      <div class="schedule-form__editor-section">

        <div class="schedule-form__section-title">Overrides</div>

        ${overridesHtml(p, defaults)}

      </div>

    `;



    card.appendChild(cardBody);

    parent.appendChild(card);



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



    // Wire simple text/number inputs inside body

    cardBody.querySelectorAll('[data-field]').forEach((input) => {

      const field = input.dataset.field;

      input.addEventListener('change', () => {

        if (OVERRIDE_META[field]) {

          const parsed = parseFloat(input.value);

          if (Number.isFinite(parsed)) {

            localPeriods[i][field] = parsed;

          } else {

            delete localPeriods[i][field];

          }

          dirty = true;

        } else {

          localPeriods[i][field] = input.value;

          dirty = true;

        }

      });

    });



    cardBody.querySelectorAll('[data-when-field]').forEach((input) => {

      input.addEventListener('change', () => {

        const period = localPeriods[i];

        const when = ensureWhenState(period)[period.schedule_type || SCHEDULE_TYPE_WEEKLY];

        when[input.dataset.whenField] = input.value;

        dirty = true;

      });

    });



    cardBody.querySelectorAll('[data-segmented-field]').forEach((group) => {

      const field = group.dataset.segmentedField;

      group.querySelectorAll('.schedule-form__segment').forEach((btn) => {

        btn.addEventListener('click', () => {

          const period = localPeriods[i];

          const value = btn.dataset.value;

          if (field === 'schedule_type') {

            ensureWhenState(period);

            period.schedule_type = value;

          } else if (field === 'time_mode') {

            const when = ensureWhenState(period)[period.schedule_type || SCHEDULE_TYPE_WEEKLY];

            when.time_mode = value;

          } else if (field === 'mode') {

            period.mode = value;

          }

          dirty = true;

          expandedSet.add(i);

          renderPeriodForms();

        });

      });

    });



    const addOverrideBtn = cardBody.querySelector('[data-action="add-override"]');

    if (addOverrideBtn) {

      addOverrideBtn.addEventListener('click', () => {

        const picker = cardBody.querySelector('[data-action="override-picker"]');

        const field = picker?.value;

        if (!field) return;

        localPeriods[i][field] = overrideBaseline(field, defaults);

        dirty = true;

        expandedSet.add(i);

        renderPeriodForms();

      });

    }



    cardBody.querySelectorAll('[data-remove-override]').forEach((btn) => {

      btn.addEventListener('click', () => {

        delete localPeriods[i][btn.dataset.removeOverride];

        dirty = true;

        expandedSet.add(i);

        renderPeriodForms();

      });

    });



    // Wire day toggles

    cardBody.querySelectorAll('.schedule-form__day').forEach((dayEl) => {

      dayEl.addEventListener('click', () => {

        const d = parseInt(dayEl.dataset.day, 10);

        const weekly = ensureWhenState(localPeriods[i])[SCHEDULE_TYPE_WEEKLY];

        const days = weekly.days || (weekly.days = []);

        const idx = days.indexOf(d);

        if (idx >= 0) {

          days.splice(idx, 1);

          dayEl.classList.remove('schedule-form__day--active');

        } else {

          days.push(d);

          days.sort();

          dayEl.classList.add('schedule-form__day--active');

        }

        dirty = true;

      });

    });

  }



  function renderPeriodForms() {

    localPeriods = localPeriods.map((period) => normalizePeriodForEditor(period));

    const defaults = getDefaults(state);

    const activeIndices = [];

    const inactiveIndices = [];

    localPeriods.forEach((p, i) => {

      if (isPeriodInactive(p)) inactiveIndices.push(i);

      else activeIndices.push(i);

    });

    const activePeriods = activeIndices.map((i) => localPeriods[i]);

    const activePeriod = findActivePeriod(activePeriods);

    const nextPeriod = findNextPeriod(activePeriods);



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



    for (const i of activeIndices) {

      renderPeriodCard(i, defaults, activePeriod, nextPeriod, periodsContainer);

    }



    if (inactiveIndices.length > 0) {

      const details = document.createElement('details');

      details.className = 'sched-inactive';

      const summary = document.createElement('summary');

      summary.className = 'sched-inactive__summary';

      summary.textContent = `Inactive (${inactiveIndices.length})`;

      details.appendChild(summary);

      const inactiveList = document.createElement('div');

      inactiveList.className = 'sched-inactive__list';

      for (const i of inactiveIndices) {

        renderPeriodCard(i, defaults, null, null, inactiveList);

      }

      details.appendChild(inactiveList);

      periodsContainer.appendChild(details);

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
    localPeriods.push({
      name: `Period ${localPeriods.length + 1}`,
      schedule_type: SCHEDULE_TYPE_WEEKLY,
      time_mode: 'window',
      start: '08:00',
      end: '22:00',
      mode: 'comfort',
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

      // Confirm the backend reflected the save before declaring success.
      const wsSchedules = await connection.getSchedules();
      const confirmed = resolveRoomScheduleData(room, wsSchedules, state, periods);
      const confirmedPeriods = confirmed?.periods ?? [];
      if (!periodsMatch(confirmedPeriods, periods)) {
        const fromEntity = resolveRoomScheduleData(room, {}, state, periods)?.periods ?? [];
        if (!periodsMatch(fromEntity, periods)) {
          throw new Error('Backend did not confirm the saved schedule');
        }
      }

      // Re-sync localPeriods to the canonical saved data and re-render the
      // form right away so the user sees the saved state without waiting for
      // the server's state_changed event.
      localPeriods = periods.map((p) => normalizePeriodForEditor({ ...p, days: [...(p.days || [])] }));
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
