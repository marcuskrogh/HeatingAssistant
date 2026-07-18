import {
  activeOverrideFields,
  findActivePeriod,
  findNextPeriod,
  formatPeriodPreview,
  hasOverride,
  isPeriodInactive,
  movePeriodInList,
  normalizePeriodForEditor,
  OVERRIDE_META,
  overrideBaseline,
  remapExpandedIndices,
  SCHEDULE_TYPE_CONTINUOUS,
  SCHEDULE_TYPE_DATE_RANGE,
  SCHEDULE_TYPE_WEEKLY,
  serializeSchedulePeriod,
} from '../schedule-utils.js?v=112';
import { setPanelHash } from '../panel-hash.js?v=112';
import { setScheduleEnabled, updateRoomSchedule } from '../ha-services.js?v=112';
import { getScheduleDataForRoom, patchStateSchedule, periodsMatch, resolveRoomScheduleData, CONFIG_ENTITY } from './schedules-shared.js?v=112';
import { renderExperimentsSection } from './schedules-experiments.js?v=112';
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// Touch hold duration before a card enters drag mode (SWD-24). Chosen so
// vertical scrolling on a period card feels native but a deliberate press
// grabs it. ~400ms sits between iOS long-press (~500ms) and a tap.
const TOUCH_DRAG_HOLD_MS = 400;
// Max finger travel during the hold window before we abandon the drag intent
// and let the browser scroll instead.
const TOUCH_DRAG_HOLD_SLOP_PX = 8;

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
  const roomTitle = document.createElement('h2');
  roomTitle.className = 'room-header__title';
  roomTitle.textContent = room.name;
  roomHeader.appendChild(roomTitle);
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
  comfortDesc.textContent = 'Daily comfort windows that set target temperature and operating mode for this room. Drag enabled periods to reorder — the first match wins.';
  container.appendChild(comfortDesc);

  // Unsaved-changes banner (shown while dirty; blocks reorder).
  const dirtyBanner = document.createElement('div');
  dirtyBanner.className = 'sched-detail__dirty-banner';
  dirtyBanner.hidden = true;
  dirtyBanner.innerHTML = `
    <span class="sched-detail__dirty-banner-dot"></span>
    <span class="sched-detail__dirty-banner-text">Unsaved changes — save to reorder periods.</span>
  `;
  container.appendChild(dirtyBanner);

  // Period form cards live here — enabled (draggable) section.
  const periodsContainer = document.createElement('div');
  periodsContainer.id = 'periods-container';
  periodsContainer.className = 'schedule-form__periods';
  container.appendChild(periodsContainer);

  // Inactive (disabled) periods section header + container.
  const inactiveHeader = document.createElement('div');
  inactiveHeader.className = 'sched-detail__section-header sched-detail__section-header--inactive';
  inactiveHeader.hidden = true;
  inactiveHeader.innerHTML = `
    <span class="sched-detail__section-title">INACTIVE PERIODS</span>
  `;
  container.appendChild(inactiveHeader);

  const inactiveContainer = document.createElement('div');
  inactiveContainer.id = 'inactive-periods-container';
  inactiveContainer.className = 'schedule-form__periods schedule-form__periods--inactive';
  container.appendChild(inactiveContainer);

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
  let dirtyPeriodIndices = new Set();
  let scheduleLoadGen = 0;
  /** Periods last persisted successfully; held until WS confirms the same payload. */
  let savedScheduleSnapshot = null;
  // Tracks which period indices are currently expanded in the UI
  let expandedSet = new Set();
  // True while a persist call (Save button or drop auto-save) is in flight.
  let saveInFlight = false;

  // ── DnD state ──────────────────────────────────────────────────────────
  // Active drag session (mouse HTML5 drag OR emulated touch drag). Both
  // pathways share the same shape so downstream helpers stay simple.
  //   { fromIndex, mode: 'mouse'|'touch', overIndex, pointerY }
  let dragState = null;
  // Touch pending state: press started but hold timer hasn't fired yet.
  //   { periodIndex, timer, startX, startY, cardEl }
  let touchPending = null;

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
    dirtyPeriodIndices = new Set();
  }

  function applySchedulePayload(roomSchedules) {
    const newData = resolveScheduleData(roomSchedules);
    renderToggle(newData);
    if (!dirty && !saveInFlight) {
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

  // ── Dirty-state helpers ────────────────────────────────────────────────

  function markPeriodDirty(i) {
    dirtyPeriodIndices.add(i);
    setDirty(true);
  }

  function setDirty(next) {
    dirty = Boolean(next);
    if (!dirty) dirtyPeriodIndices.clear();
    updateDirtyUI();
  }

  function updateDirtyUI() {
    dirtyBanner.hidden = !dirty;
    container.classList.toggle('sched-detail--dirty', dirty);
    container.classList.toggle('sched-detail--saving', saveInFlight);

    if (dirty && !saveInFlight) {
      if (!saveStatus.classList.contains('tuning-actions__status--error')) {
        saveStatus.textContent = 'Unsaved changes';
        saveStatus.className = 'tuning-actions__status tuning-actions__status--dirty';
      }
    } else if (!saveInFlight && !saveStatus.classList.contains('tuning-actions__status--success')
                             && !saveStatus.classList.contains('tuning-actions__status--error')) {
      saveStatus.textContent = '';
      saveStatus.className = 'tuning-actions__status';
    }

    const allCards = [
      ...periodsContainer.querySelectorAll('.schedule-form__period'),
      ...inactiveContainer.querySelectorAll('.schedule-form__period'),
    ];
    for (const card of allCards) {
      const idx = Number(card.dataset.periodIndex);
      const isUnsaved = dirtyPeriodIndices.has(idx);
      card.classList.toggle('schedule-form__period--unsaved', isUnsaved);
    }

    // Reorder is only allowed when there are no unsaved edits and no save in
    // flight. Toggling `draggable` here (rather than re-rendering) keeps the
    // UI feedback instantaneous as the user types / saves.
    const reorderLocked = dirty || saveInFlight;
    periodsContainer.classList.toggle('schedule-form__periods--reorder-locked', reorderLocked);
    for (const card of periodsContainer.querySelectorAll('.schedule-form__period--draggable')) {
      if (reorderLocked) card.removeAttribute('draggable');
      else card.setAttribute('draggable', 'true');
    }
  }

  /** Flush any in-progress values in visible editors back into localPeriods.
   *
   * Safety net for browsers that don't fire `input` for partial time/date
   * entries. Called before starting a drag and before Save so what the user
   * sees on screen is what gets persisted.
   */
  function flushOpenEditorsToLocal() {
    const cards = [
      ...periodsContainer.querySelectorAll('[data-period-index]'),
      ...inactiveContainer.querySelectorAll('[data-period-index]'),
    ];
    for (const card of cards) {
      const i = Number(card.dataset.periodIndex);
      const period = localPeriods[i];
      if (!period) continue;
      const body = card.querySelector('.schedule-form__period-body');
      if (!body || body.hidden) continue;
      body.querySelectorAll('[data-field]').forEach((input) => {
        const field = input.dataset.field;
        if (OVERRIDE_META[field]) {
          const parsed = parseFloat(input.value);
          if (Number.isFinite(parsed)) period[field] = parsed;
        } else if (input.value !== undefined) {
          period[field] = input.value;
        }
      });
      body.querySelectorAll('[data-when-field]').forEach((input) => {
        const when = ensureWhenState(period)[period.schedule_type || SCHEDULE_TYPE_WEEKLY];
        if (input.value !== undefined && input.value !== '') {
          when[input.dataset.whenField] = input.value;
        }
      });
    }
  }

  // ── Persist helper (used by Save button + drop auto-save) ──────────────

  /**
   * Persist the current localPeriods via update_room_schedule and confirm
   * against the coordinator. Mirrors the historical Save behaviour so drop
   * auto-saves get the same anti-race guarantees.
   *
   * On success: dirty=false, saved status message, resolves.
   * On failure: dirty stays true, error status message, rejects.
   */
  async function persistPeriods({ reason }) {
    if (saveInFlight) throw new Error('Save already in progress');
    saveInFlight = true;
    btnSave.disabled = true;

    const savingLabel = reason === 'drop' ? 'Saving new order…' : 'Saving…';
    saveStatus.textContent = savingLabel;
    saveStatus.className = 'tuning-actions__status tuning-actions__status--running';
    updateDirtyUI();

    try {
      const defaults = getDefaults(state);
      // Rebuild persisted order as [enabled-in-priority-order, ...disabled]
      // so priority is unambiguously the position in the enabled group.
      const orderedLocal = [
        ...localPeriods.filter((p) => p.enabled !== false),
        ...localPeriods.filter((p) => p.enabled === false),
      ];
      const periods = orderedLocal.map((p) => serializeSchedulePeriod(p, defaults));
      await updateRoomSchedule(hass, room.slug, periods);

      // Hold the saved payload until WS confirms it — prevents stale/empty
      // getSchedules() responses from wiping the form after save.
      savedScheduleSnapshot = periods.map((p) => ({ ...p, days: [...(p.days || [])] }));

      // Invalidate in-flight schedule reloads started before this save.
      scheduleLoadGen++;

      // Patch the shared state object immediately.
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

      // Re-sync localPeriods to the canonical saved data and re-render.
      localPeriods = periods.map((p) => normalizePeriodForEditor({ ...p, days: [...(p.days || [])] }));

      dirty = false;
      dirtyPeriodIndices.clear();
      saveInFlight = false;
      btnSave.disabled = false;

      renderPeriodForms();

      const savedLabel = reason === 'drop' ? 'Order saved.' : 'Saved.';
      saveStatus.textContent = savedLabel;
      saveStatus.className = 'tuning-actions__status tuning-actions__status--success';
      return periods;
    } catch (err) {
      saveInFlight = false;
      btnSave.disabled = false;
      // Keep the unsaved order visible — user can Save to retry.
      dirty = true;
      updateDirtyUI();
      saveStatus.textContent = 'Error: ' + (err?.message || err);
      saveStatus.className = 'tuning-actions__status tuning-actions__status--error';
      throw err;
    }
  }

  // ── DnD helpers ────────────────────────────────────────────────────────

  /** Selectors that should NOT initiate a drag when the pointer is on them. */
  const INTERACTIVE_DRAG_EXCLUDE_SELECTOR = [
    'input',
    'button',
    'select',
    'textarea',
    'label',
    '.schedule-form__day',
    '[data-action]',
    '[data-remove-override]',
    '[data-segmented-field]',
    '.schedule-form__period-body',
  ].join(', ');

  function isDragExcludedTarget(target) {
    if (!target || !(target instanceof Element)) return false;
    return Boolean(target.closest(INTERACTIVE_DRAG_EXCLUDE_SELECTOR));
  }

  function enabledIndicesInOrder() {
    // Contiguous active (non-inactive) prefix used by drop-target math.
    // Inactive = disabled OR fully past (SWD-22); those are not draggable.
    const out = [];
    for (let i = 0; i < localPeriods.length; i++) {
      if (!isPeriodInactive(localPeriods[i])) out.push(i);
    }
    return out;
  }

  function beforeDragStart() {
    // Flush any partially-typed values into localPeriods first so nothing is
    // lost when we collapse the open editors below.
    flushOpenEditorsToLocal();
    // Collapse all open editors — keeps the drag list compact and avoids
    // reflows from tall cards while the user is scrubbing.
    if (expandedSet.size > 0) {
      expandedSet = new Set();
      // Instead of a full re-render (which would tear down the card we're
      // about to drag), just fold each open body directly.
      const cards = periodsContainer.querySelectorAll('.schedule-form__period--expanded');
      for (const card of cards) {
        card.classList.remove('schedule-form__period--expanded');
        const body = card.querySelector('.schedule-form__period-body');
        if (body) body.hidden = true;
        const chev = card.querySelector('.schedule-form__expand-chevron');
        if (chev) chev.textContent = '▼';
      }
    }
  }

  function clearDragVisuals() {
    for (const card of periodsContainer.querySelectorAll('.schedule-form__period--dragging')) {
      card.classList.remove('schedule-form__period--dragging');
    }
    for (const card of periodsContainer.querySelectorAll('.schedule-form__period--drop-before, .schedule-form__period--drop-after')) {
      card.classList.remove('schedule-form__period--drop-before', 'schedule-form__period--drop-after');
    }
  }

  function computeTargetIndexFromPointer(pointerY, sourceIndex) {
    // Return the localPeriods index we would drop AT (before which the source
    // gets inserted, or 'end' if past the last card). Only enabled cards are
    // draggable, so we constrain the target to the enabled group.
    const cards = Array.from(periodsContainer.querySelectorAll('.schedule-form__period--draggable'));
    if (cards.length === 0) return sourceIndex;

    let target = null;
    let insertBefore = true;

    for (const card of cards) {
      const idx = Number(card.dataset.periodIndex);
      if (!Number.isFinite(idx)) continue;
      const rect = card.getBoundingClientRect();
      const mid = rect.top + rect.height / 2;
      if (pointerY < mid) {
        target = idx;
        insertBefore = true;
        break;
      }
    }

    if (target === null) {
      // After the last enabled card.
      const enabled = enabledIndicesInOrder();
      target = enabled[enabled.length - 1];
      insertBefore = false;
    }

    // Translate (target, insertBefore) into the final index in localPeriods
    // AFTER the source has been removed. movePeriodInList handles this by
    // operating on the original array: we just need the desired final index.
    let finalIndex = target;
    if (!insertBefore) finalIndex = target + 1;
    if (finalIndex > sourceIndex) finalIndex -= 1;

    // Clamp within the enabled range so a drop can never land inside the
    // inactive group. Enabled group occupies indices [0 .. lastEnabled].
    const enabled = enabledIndicesInOrder();
    if (enabled.length === 0) return sourceIndex;
    const firstEnabled = enabled[0];
    const lastEnabled = enabled[enabled.length - 1];
    if (finalIndex < firstEnabled) finalIndex = firstEnabled;
    if (finalIndex > lastEnabled) finalIndex = lastEnabled;
    return finalIndex;
  }

  function showDropIndicator(fromIndex, pointerY) {
    // Highlight the insertion slot for feedback.
    clearDropOnlyVisuals();
    const cards = Array.from(periodsContainer.querySelectorAll('.schedule-form__period--draggable'));
    if (cards.length === 0) return;
    let indicated = false;
    for (const card of cards) {
      const idx = Number(card.dataset.periodIndex);
      if (idx === fromIndex) continue;
      const rect = card.getBoundingClientRect();
      const mid = rect.top + rect.height / 2;
      if (pointerY < mid) {
        card.classList.add('schedule-form__period--drop-before');
        indicated = true;
        break;
      }
    }
    if (!indicated) {
      const enabled = enabledIndicesInOrder();
      const last = cards.find((c) => Number(c.dataset.periodIndex) === enabled[enabled.length - 1]);
      if (last && Number(last.dataset.periodIndex) !== fromIndex) {
        last.classList.add('schedule-form__period--drop-after');
      }
    }
  }

  function clearDropOnlyVisuals() {
    for (const card of periodsContainer.querySelectorAll('.schedule-form__period--drop-before, .schedule-form__period--drop-after')) {
      card.classList.remove('schedule-form__period--drop-before', 'schedule-form__period--drop-after');
    }
  }

  async function commitReorder(fromIndex, toIndex) {
    if (fromIndex === toIndex) return;
    const remapped = remapExpandedIndices(expandedSet, fromIndex, toIndex);
    expandedSet = remapped;
    const remappedDirty = remapExpandedIndices(dirtyPeriodIndices, fromIndex, toIndex);
    dirtyPeriodIndices = remappedDirty;
    localPeriods = movePeriodInList(localPeriods, fromIndex, toIndex);

    // Re-render optimistically so the new order shows before the round-trip.
    renderPeriodForms();

    // Auto-save the new order. On failure, keep the unsaved order and mark
    // every enabled period as unsaved (the reorder itself is the change).
    try {
      await persistPeriods({ reason: 'drop' });
    } catch (err) {
      for (let i = 0; i < localPeriods.length; i++) {
        if (!isPeriodInactive(localPeriods[i])) dirtyPeriodIndices.add(i);
      }
      dirty = true;
      updateDirtyUI();
    }
  }

  // Mouse HTML5 drag handlers wired per-card in renderPeriodForms.
  function attachMouseDrag(card, periodIndex) {
    card.addEventListener('dragstart', (e) => {
      if (dirty || saveInFlight) {
        e.preventDefault();
        return;
      }
      if (isDragExcludedTarget(e.target)) {
        e.preventDefault();
        return;
      }
      dragState = { fromIndex: periodIndex, mode: 'mouse', overIndex: periodIndex };
      try {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(periodIndex));
      } catch (_) { /* some browsers reject setData in shadow DOM — ignore */ }
      card.classList.add('schedule-form__period--dragging');
      beforeDragStart();
    });

    card.addEventListener('dragover', (e) => {
      if (!dragState || dragState.mode !== 'mouse') return;
      e.preventDefault();
      try { e.dataTransfer.dropEffect = 'move'; } catch (_) { /* ignore */ }
      showDropIndicator(dragState.fromIndex, e.clientY);
    });

    card.addEventListener('drop', (e) => {
      if (!dragState || dragState.mode !== 'mouse') return;
      e.preventDefault();
      // Prevent the container-level drop from re-firing on the same event.
      // Currently safe (dragState is nulled below) but explicit stopPropagation
      // avoids any future ambiguity if that invariant changes.
      e.stopPropagation();
      const finalIndex = computeTargetIndexFromPointer(e.clientY, dragState.fromIndex);
      const from = dragState.fromIndex;
      dragState = null;
      clearDragVisuals();
      commitReorder(from, finalIndex);
    });

    card.addEventListener('dragend', () => {
      dragState = null;
      clearDragVisuals();
    });
  }

  // Container-level dragover so drops in the gap between cards are handled.
  periodsContainer.addEventListener('dragover', (e) => {
    if (!dragState || dragState.mode !== 'mouse') return;
    e.preventDefault();
    try { e.dataTransfer.dropEffect = 'move'; } catch (_) { /* ignore */ }
    showDropIndicator(dragState.fromIndex, e.clientY);
  });
  periodsContainer.addEventListener('drop', (e) => {
    if (!dragState || dragState.mode !== 'mouse') return;
    e.preventDefault();
    const finalIndex = computeTargetIndexFromPointer(e.clientY, dragState.fromIndex);
    const from = dragState.fromIndex;
    dragState = null;
    clearDragVisuals();
    commitReorder(from, finalIndex);
  });

  // Touch drag: hold, then move. Uses touch events so we can preventDefault
  // once the drag has started to stop the page from scrolling.
  function attachTouchDrag(card, periodIndex) {
    card.addEventListener('touchstart', (e) => {
      if (dirty || saveInFlight) return;
      if (e.touches.length !== 1) return;
      if (isDragExcludedTarget(e.target)) return;
      const t = e.touches[0];
      cancelTouchPending();
      touchPending = {
        periodIndex,
        startX: t.clientX,
        startY: t.clientY,
        cardEl: card,
        timer: setTimeout(() => {
          // Seed pointerY from the initial press. Without this, a hold-then-
          // release with no `touchmove` would leave pointerY undefined and
          // the drop math would silently land at the top of the list.
          const startY = touchPending?.startY;
          touchPending = null;
          dragState = {
            fromIndex: periodIndex,
            mode: 'touch',
            overIndex: periodIndex,
            pointerY: startY,
            cardEl: card,
          };
          card.classList.add('schedule-form__period--dragging');
          beforeDragStart();
        }, TOUCH_DRAG_HOLD_MS),
      };
    }, { passive: true });

    card.addEventListener('touchmove', (e) => {
      // Waiting for the hold: cancel if the finger slides too far.
      if (touchPending && touchPending.periodIndex === periodIndex) {
        const t = e.touches[0];
        const dx = t.clientX - touchPending.startX;
        const dy = t.clientY - touchPending.startY;
        if (Math.abs(dx) > TOUCH_DRAG_HOLD_SLOP_PX || Math.abs(dy) > TOUCH_DRAG_HOLD_SLOP_PX) {
          cancelTouchPending();
        }
        return;
      }
      // Actively dragging: prevent scroll and update indicator.
      if (dragState && dragState.mode === 'touch' && dragState.fromIndex === periodIndex) {
        e.preventDefault();
        const t = e.touches[0];
        dragState.pointerY = t.clientY;
        showDropIndicator(dragState.fromIndex, t.clientY);
      }
    }, { passive: false });

    card.addEventListener('touchend', () => {
      if (touchPending && touchPending.periodIndex === periodIndex) {
        cancelTouchPending();
        return;
      }
      if (dragState && dragState.mode === 'touch' && dragState.fromIndex === periodIndex) {
        const from = dragState.fromIndex;
        // Fall back to the dragging card's current midpoint if pointerY was
        // never populated (e.g. hold-then-release with zero touchmove).
        let pointerY = dragState.pointerY;
        if (pointerY == null) {
          const target = dragState.cardEl ?? card;
          const rect = target?.getBoundingClientRect?.();
          pointerY = rect ? rect.top + rect.height / 2 : 0;
        }
        const finalIndex = computeTargetIndexFromPointer(pointerY, from);
        dragState = null;
        clearDragVisuals();
        commitReorder(from, finalIndex);
      }
    });

    card.addEventListener('touchcancel', () => {
      if (touchPending && touchPending.periodIndex === periodIndex) {
        cancelTouchPending();
      }
      if (dragState && dragState.mode === 'touch' && dragState.fromIndex === periodIndex) {
        dragState = null;
        clearDragVisuals();
      }
    });
  }

  function cancelTouchPending() {
    if (touchPending?.timer) clearTimeout(touchPending.timer);
    touchPending = null;
  }

  // ── Render ─────────────────────────────────────────────────────────────

  /**
   * Reorder `localPeriods` so every enabled period comes before every
   * disabled one, without perturbing the relative order inside each group.
   *
   * Guarantees drop-target math in `computeTargetIndexFromPointer` — which
   * clamps to `[firstEnabled..lastEnabled]` — is only ever applied to a
   * contiguous enabled prefix. Freshly loaded schedules (before the first
   * persist rebuilds the wire order) or a stale interleaving from an older
   * client can otherwise put a disabled period between two enabled ones.
   *
   * `expandedSet` and `dirtyPeriodIndices` are keyed by index, so we remap
   * them by **object identity** rather than assuming any positional shift.
   */
  function normalizeEnabledFirst() {
    const active = [];
    const inactive = [];
    for (const p of localPeriods) {
      if (isPeriodInactive(p)) inactive.push(p);
      else active.push(p);
    }
    const next = [...active, ...inactive];
    if (next.every((p, i) => p === localPeriods[i])) return;
    const remap = (set) => {
      const out = new Set();
      for (const idx of set) {
        const period = localPeriods[idx];
        if (!period) continue;
        const newIdx = next.indexOf(period);
        if (newIdx >= 0) out.add(newIdx);
      }
      return out;
    };
    expandedSet = remap(expandedSet);
    dirtyPeriodIndices = remap(dirtyPeriodIndices);
    localPeriods = next;
  }

  function renderPeriodForms() {
    normalizeEnabledFirst();
    localPeriods = localPeriods.map((period) => normalizePeriodForEditor(period));
    const defaults = getDefaults(state);
    // NOW/NEXT only among non-inactive periods (SWD-22).
    const activeList = localPeriods.filter((p) => !isPeriodInactive(p));
    const activePeriod = findActivePeriod(activeList);
    const nextPeriod = findNextPeriod(activeList);

    periodsTitleEl.textContent = localPeriods.length > 0
      ? `COMFORT PERIODS (${localPeriods.length})`
      : 'COMFORT PERIODS';

    periodsContainer.innerHTML = '';
    inactiveContainer.innerHTML = '';

    if (localPeriods.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'sched-detail__empty';
      empty.innerHTML = `
        <p>No periods configured for this room.</p>
        <p>Click <strong>+ Add Period</strong> above to create a schedule.</p>
      `;
      periodsContainer.appendChild(empty);
      inactiveHeader.hidden = true;
      updateDirtyUI();
      return;
    }

    let hasActive = false;
    let hasInactive = false;

    for (let i = 0; i < localPeriods.length; i++) {
      const p = localPeriods[i];
      const inactive = isPeriodInactive(p);
      if (inactive) hasInactive = true; else hasActive = true;
      const card = buildPeriodCard(i, p, {
        defaults,
        isActive: !inactive && p === activePeriod,
        isNext: !inactive && p === nextPeriod,
      });
      if (inactive) {
        inactiveContainer.appendChild(card);
      } else {
        periodsContainer.appendChild(card);
      }
    }

    if (!hasActive) {
      const empty = document.createElement('div');
      empty.className = 'sched-detail__empty sched-detail__empty--muted';
      empty.innerHTML = `<p>No active periods. Enable one below or add a new period above.</p>`;
      periodsContainer.appendChild(empty);
    }
    inactiveHeader.hidden = !hasInactive;

    updateDirtyUI();
  }

  function buildPeriodCard(i, p, { defaults, isActive, isNext }) {
    const isExpanded = expandedSet.has(i);
    const periodEnabled = p.enabled !== false;
    const periodInactive = isPeriodInactive(p);
    const preview = formatPeriodPreview(p);

    const card = document.createElement('div');
    card.className = 'card schedule-form__period' +
      (isActive ? ' schedule-form__period--active' : '') +
      (isNext ? ' schedule-form__period--next' : '') +
      (isExpanded ? ' schedule-form__period--expanded' : '') +
      (!periodEnabled ? ' schedule-form__period--disabled' : '');
    card.dataset.periodIndex = String(i);

    // Only active (non-inactive) cards participate in drag reorder (SWD-24 + SWD-22).
    if (!periodInactive) {
      card.classList.add('schedule-form__period--draggable');
      // Draggable attribute is toggled by updateDirtyUI based on state.
      if (!dirty && !saveInFlight) card.setAttribute('draggable', 'true');
    }

    // ── Collapsed header — always visible ──────────────────────────────────
    const cardHeader = document.createElement('div');
    cardHeader.className = 'schedule-form__period-header';
    cardHeader.innerHTML = `
      <div class="schedule-form__period-header-main">
        ${isActive ? '<span class="sched-detail__now-badge">NOW</span>' : ''}
        ${isNext ? '<span class="sched-detail__next-badge">NEXT</span>' : ''}
        <button type="button" class="sched-period-toggle ${periodEnabled ? 'sched-period-toggle--on' : 'sched-period-toggle--off'}" data-action="toggle-enabled" title="${periodEnabled ? 'Disable period' : 'Enable period'}">${periodEnabled ? 'ON' : 'OFF'}</button>
        <span class="sched-row__type">${escapeAttr(preview.type)}</span>
        <span class="schedule-form__period-name">${escapeAttr(preview.name)}</span>
        <span class="schedule-form__period-time">${escapeAttr(preview.timing)}</span>
        <span class="sched-row__mode ${preview.modeCls}">${escapeAttr(preview.mode)}</span>
      </div>
      <div class="schedule-form__period-header-actions">
        <span class="schedule-form__unsaved-dot" aria-hidden="true" title="Unsaved changes"></span>
        <button class="schedule-form__delete" title="Delete period" data-action="delete">×</button>
        <span class="schedule-form__expand-chevron">${isExpanded ? '▲' : '▼'}</span>
      </div>
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
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="date" value="${escapeAttr(dateWhen.start_date)}" data-when-field="start_date">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">End date</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="date" value="${escapeAttr(dateWhen.end_date)}" data-when-field="end_date">
            </div>
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
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="datetime-local" value="${escapeAttr(continuousWhen.start_at)}" data-when-field="start_at">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">End datetime</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="datetime-local" value="${escapeAttr(continuousWhen.end_at)}" data-when-field="end_at">
            </div>
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

    // Toggle expansion on header click (except action buttons / drag grip)
    cardHeader.addEventListener('click', (e) => {
      if (e.target.closest('.schedule-form__delete, [data-action="toggle-enabled"]')) return;
      const willExpand = !expandedSet.has(i);
      if (willExpand) expandedSet.add(i); else expandedSet.delete(i);
      card.classList.toggle('schedule-form__period--expanded', willExpand);
      cardBody.hidden = !willExpand;
      cardHeader.querySelector('.schedule-form__expand-chevron').textContent = willExpand ? '▲' : '▼';
    });

    // Per-period enable toggle — re-enabling appends to the end of enabled group.
    cardHeader.querySelector('[data-action="toggle-enabled"]').addEventListener('click', (e) => {
      e.stopPropagation();
      const period = localPeriods[i];
      const wasEnabled = period.enabled !== false;
      if (wasEnabled) {
        period.enabled = false;
        markPeriodDirty(i);
        renderPeriodForms();
      } else {
        // Move this period to the end of the enabled group so it lands with
        // the lowest priority — matches SWD-24 plan ("enable → append").
        // Robust regardless of any interleaving that mid-edit toggles may
        // have introduced: detach first, then find the end of the enabled
        // group in the remaining array and re-insert there.
        period.enabled = true;
        const from = i;
        const detached = localPeriods.splice(from, 1)[0];
        const shiftDown = (set) => {
          const out = new Set();
          for (const idx of set) {
            if (idx < from) out.add(idx);
            else if (idx > from) out.add(idx - 1);
          }
          return out;
        };
        expandedSet = shiftDown(expandedSet);
        dirtyPeriodIndices = shiftDown(dirtyPeriodIndices);

        let lastActiveIdx = -1;
        for (let k = 0; k < localPeriods.length; k++) {
          if (!isPeriodInactive(localPeriods[k])) lastActiveIdx = k;
        }
        const insertAt = lastActiveIdx + 1;

        const shiftUp = (set) => {
          const out = new Set();
          for (const idx of set) {
            if (idx >= insertAt) out.add(idx + 1); else out.add(idx);
          }
          return out;
        };
        expandedSet = shiftUp(expandedSet);
        dirtyPeriodIndices = shiftUp(dirtyPeriodIndices);
        localPeriods.splice(insertAt, 0, detached);
        markPeriodDirty(insertAt);
        renderPeriodForms();
      }
    });

    // Delete — rebuild expandedSet / dirtyPeriodIndices with shifted indices
    cardHeader.querySelector('[data-action="delete"]').addEventListener('click', (e) => {
      e.stopPropagation();
      const shift = (set) => {
        const out = new Set();
        for (const idx of set) {
          if (idx < i) out.add(idx);
          else if (idx > i) out.add(idx - 1);
        }
        return out;
      };
      expandedSet = shift(expandedSet);
      dirtyPeriodIndices = shift(dirtyPeriodIndices);
      localPeriods.splice(i, 1);
      setDirty(true);
      renderPeriodForms();
    });

    // Wire text/number inputs — use `input` so we mark dirty on every
    // keystroke and the dirty gate reacts instantly.
    cardBody.querySelectorAll('[data-field]').forEach((input) => {
      const field = input.dataset.field;
      input.addEventListener('input', () => {
        if (OVERRIDE_META[field]) {
          const parsed = parseFloat(input.value);
          if (Number.isFinite(parsed)) {
            localPeriods[i][field] = parsed;
          } else if (input.value === '') {
            delete localPeriods[i][field];
          }
        } else {
          localPeriods[i][field] = input.value;
        }
        markPeriodDirty(i);
      });
    });

    cardBody.querySelectorAll('[data-when-field]').forEach((input) => {
      input.addEventListener('input', () => {
        const period = localPeriods[i];
        const when = ensureWhenState(period)[period.schedule_type || SCHEDULE_TYPE_WEEKLY];
        when[input.dataset.whenField] = input.value;
        markPeriodDirty(i);
      });
      // Dates/times can move a period between active and inactive — re-bucket
      // on change (not every keystroke) so focus is preserved while typing.
      input.addEventListener('change', () => {
        const period = localPeriods[i];
        const when = ensureWhenState(period)[period.schedule_type || SCHEDULE_TYPE_WEEKLY];
        when[input.dataset.whenField] = input.value;
        markPeriodDirty(i);
        expandedSet.add(i);
        renderPeriodForms();
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
          markPeriodDirty(i);
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
        markPeriodDirty(i);
        expandedSet.add(i);
        renderPeriodForms();
      });
    }

    cardBody.querySelectorAll('[data-remove-override]').forEach((btn) => {
      btn.addEventListener('click', () => {
        delete localPeriods[i][btn.dataset.removeOverride];
        markPeriodDirty(i);
        expandedSet.add(i);
        renderPeriodForms();
      });
    });

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
        markPeriodDirty(i);
      });
    });

    // Wire DnD (only for active / non-inactive cards)
    if (!periodInactive) {
      attachMouseDrag(card, i);
      attachTouchDrag(card, i);
    }

    return card;
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
      patchStateSchedule(state, room.slug, localPeriods, !currentEnabled);
      toggleStatus.textContent = '';
      toggleStatus.className = 'tuning-actions__status';
      renderToggle({ enabled: !currentEnabled });
    } catch (err) {
      toggleStatus.textContent = 'Error: ' + (err.message || err);
      toggleStatus.className = 'tuning-actions__status tuning-actions__status--error';
    }
  });

  // Add a blank period — auto-expand it. New periods are enabled, so they
  // must land at the end of the enabled group (not appended after any
  // disabled entries) to keep priority = position in the enabled prefix.
  btnAdd.addEventListener('click', () => {
    const newPeriod = {
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
    };
    let lastActiveIdx = -1;
    for (let k = 0; k < localPeriods.length; k++) {
      if (!isPeriodInactive(localPeriods[k])) lastActiveIdx = k;
    }
    const insertAt = lastActiveIdx + 1;
    const shiftUp = (set) => {
      const out = new Set();
      for (const idx of set) {
        if (idx >= insertAt) out.add(idx + 1); else out.add(idx);
      }
      return out;
    };
    expandedSet = shiftUp(expandedSet);
    dirtyPeriodIndices = shiftUp(dirtyPeriodIndices);
    localPeriods.splice(insertAt, 0, newPeriod);
    expandedSet.add(insertAt);
    markPeriodDirty(insertAt);
    renderPeriodForms();
    const sel = '.schedule-form__period[data-period-index="' + insertAt + '"]';
    const newCard = periodsContainer.querySelector(sel)
      || inactiveContainer.querySelector(sel);
    if (newCard) newCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });

  // Save all periods (manual)
  btnSave.addEventListener('click', async () => {
    flushOpenEditorsToLocal();
    try {
      await persistPeriods({ reason: 'save' });
    } catch (err) {
      // persistPeriods already surfaced the error into the status area.
    }
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
    destroy() {
      cancelTouchPending();
      dragState = null;
      expSection.destroy();
    },
  };
}
