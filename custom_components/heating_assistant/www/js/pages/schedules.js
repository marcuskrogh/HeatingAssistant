import { findActivePeriod, findNextPeriod, periodModeDisplay, getRoomScheduleData, periodRowHtml, scheduleEnabledBadgeHtml, scheduleSectionHeaderHtml } from '../schedule-utils.js';
import { signalLabel, experimentRowHtml, findNextScheduledExperiment, experimentStatusInfo } from '../experiment-utils.js';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function renderSchedules(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderScheduleDetail(container, slug, rooms, state, connection, hass);
  }
  return renderScheduleIndex(container, rooms, state, connection, hass);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Robust lookup: tries slug, name, and case-insensitive normalised match. */
const getScheduleDataForRoom = getRoomScheduleData;

/** Renders a single period summary row element. */
function makePeriodRow(p, isActive, isNext) {
  const row = document.createElement('div');
  row.innerHTML = periodRowHtml(p, isActive, isNext);
  return row.firstElementChild;
}

// ---------------------------------------------------------------------------
// Experiment helpers
// ---------------------------------------------------------------------------

const EXCITATION_OPTIONS = [
  { value: 'step', label: 'Step (recommended)' },
  { value: 'prbs', label: 'PRBS' },
  { value: 'pulse', label: 'Pulse' },
];

function fmtExpTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function fmtExpDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function fmtExpWindow(exp) {
  if (!exp.start_ts) return '—';
  const sd = new Date(exp.start_ts * 1000);
  const ed = new Date((exp.end_ts || 0) * 1000);
  const sameDay = sd.toDateString() === ed.toDateString();
  const startStr = `${fmtExpDate(exp.start_ts)} ${fmtExpTime(exp.start_ts)}`;
  const endStr = sameDay ? fmtExpTime(exp.end_ts) : `${fmtExpDate(exp.end_ts)} ${fmtExpTime(exp.end_ts)}`;
  return `${startStr} → ${endStr}`;
}

function tsToLocalInput(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function expStatusInfo(status) {
  return experimentStatusInfo(status);
}

function expCardModifier(status) {
  if (status === 'running') return ' schedule-form__period--exp-running';
  if (status === 'scheduled') return ' schedule-form__period--exp-scheduled';
  return '';
}

// ---------------------------------------------------------------------------
// Index view — room schedule cards
// ---------------------------------------------------------------------------

function renderScheduleIndex(container, rooms, state, connection, hass) {
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

  let cachedSchedules = {};

  function buildCards(roomSchedules, expsByRoom = {}) {
    grid.innerHTML = '';
    cachedSchedules = roomSchedules;

    for (const room of rooms) {
      const schedData = getScheduleDataForRoom(roomSchedules, room);
      const periods = schedData?.periods || [];
      const enabled = schedData?.enabled ?? true;

      const activePeriod = findActivePeriod(periods);
      const nextPeriod = findNextPeriod(periods);

      const roomExps = expsByRoom[room.slug] || [];
      const upcomingExps = roomExps.filter((e) => e.status === 'scheduled' || e.status === 'running');
      const activeExp = upcomingExps.find((e) => e.status === 'running') || null;
      const nextExp = findNextScheduledExperiment(upcomingExps);

      const card = document.createElement('div');
      card.className = 'card card--clickable sched-index-card';

      // ── Header: room name ────────────────────────────────────────────────
      const cardHeader = document.createElement('div');
      cardHeader.className = 'sched-index-card__header';
      cardHeader.innerHTML = `<span class="sched-index-card__name">${room.name}</span>`;
      card.appendChild(cardHeader);

      // ── Comfort periods section ─────────────────────────────────────────
      const comfortSection = document.createElement('div');
      comfortSection.className = 'sched-section';
      comfortSection.innerHTML = scheduleSectionHeaderHtml(
        'COMFORT PERIODS',
        scheduleEnabledBadgeHtml(enabled),
      );
      if (periods.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'sched-index-card__empty';
        empty.textContent = 'No periods configured — click to add';
        comfortSection.appendChild(empty);
      } else {
        const list = document.createElement('div');
        list.className = 'sched-index-card__list';

        const preview = periods.slice(0, 4);
        const overflow = periods.length - preview.length;

        for (const p of preview) {
          list.appendChild(makePeriodRow(p, p === activePeriod, p === nextPeriod));
        }

        if (overflow > 0) {
          const more = document.createElement('div');
          more.className = 'sched-index-card__overflow';
          more.textContent = `+${overflow} more…`;
          list.appendChild(more);
        }

        comfortSection.appendChild(list);
      }
      card.appendChild(comfortSection);

      // ── Experiments section ─────────────────────────────────────────────
      const expSection = document.createElement('div');
      expSection.className = 'sched-section';
      expSection.innerHTML = scheduleSectionHeaderHtml('EXPERIMENTS');
      if (upcomingExps.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'sched-index-card__empty';
        empty.textContent = 'No experiments scheduled';
        expSection.appendChild(empty);
      } else {
        const list = document.createElement('div');
        list.className = 'sched-index-card__list';
        const preview = upcomingExps.slice(0, 3);
        const overflow = upcomingExps.length - preview.length;
        for (const e of preview) {
          const row = document.createElement('div');
          row.innerHTML = experimentRowHtml(e, {
            isActive: e === activeExp,
            isNext: !activeExp && e === nextExp,
          });
          list.appendChild(row.firstElementChild);
        }
        if (overflow > 0) {
          const more = document.createElement('div');
          more.className = 'sched-index-card__overflow';
          more.textContent = `+${overflow} more…`;
          list.appendChild(more);
        }
        expSection.appendChild(list);
      }
      card.appendChild(expSection);

      card.addEventListener('click', () => {
        window.location.hash = `#schedules/${room.slug}`;
      });
      grid.appendChild(card);
    }
  }

  // Fetch schedules and experiments from the coordinator via WebSocket
  Promise.all([connection.getSchedules(), connection.listExperiments()]).then(([roomSchedules, experiments]) => {
    const expsByRoom = {};
    if (Array.isArray(experiments)) {
      for (const e of experiments) {
        if (e?.room_slug) {
          (expsByRoom[e.room_slug] || (expsByRoom[e.room_slug] = [])).push(e);
        }
      }
    }
    buildCards(roomSchedules, expsByRoom);
  });

  return {
    update(newState) {
      // On state update, re-fetch from WebSocket to stay in sync
      Promise.all([connection.getSchedules(), connection.listExperiments()]).then(([roomSchedules, experiments]) => {
        const expsByRoom = {};
        if (Array.isArray(experiments)) {
          for (const e of experiments) {
            if (e?.room_slug) {
              (expsByRoom[e.room_slug] || (expsByRoom[e.room_slug] = [])).push(e);
            }
          }
        }
        buildCards(roomSchedules, expsByRoom);
      });
    },
    destroy() {},
  };
}

// ---------------------------------------------------------------------------
// Experiment section (for detail view)
// ---------------------------------------------------------------------------

function renderExperimentsSection(container, room, connection, hass) {
  const sectionWrap = document.createElement('div');
  sectionWrap.className = 'sched-section sched-section--detail';
  container.appendChild(sectionWrap);

  const sectionHeader = document.createElement('div');
  sectionHeader.className = 'sched-detail__section-header';
  sectionHeader.innerHTML = `
    <span class="sched-detail__section-title" id="exp-section-title">EXPERIMENTS</span>
    <button class="btn btn--primary btn--sm" id="btn-add-exp">+ Add Experiment</button>
  `;
  sectionWrap.insertBefore(sectionHeader, sectionWrap.firstChild);

  const expDesc = document.createElement('p');
  expDesc.className = 'tuning-section__desc';
  expDesc.style.margin = '0 0 14px';
  expDesc.textContent = 'Override comfort schedules with test-signal excitation to capture thermal response data for identification.';
  sectionWrap.appendChild(expDesc);

  const formContainer = document.createElement('div');
  sectionWrap.appendChild(formContainer);

  const expListContainer = document.createElement('div');
  sectionWrap.appendChild(expListContainer);

  let experiments = [];
  let formVisible = false;
  const expandedExpIds = new Set();

  const sectionTitleEl = sectionWrap.querySelector('#exp-section-title');
  const btnAdd = sectionWrap.querySelector('#btn-add-exp');

  function getDefaultWindow() {
    const now = new Date();
    const start = new Date(now);
    start.setHours(23, 0, 0, 0);
    if (start <= now) start.setDate(start.getDate() + 1);
    return { start, end: new Date(start.getTime() + 6 * 3600 * 1000) };
  }

  function setFormStatus(el, text, type = '') {
    el.textContent = text;
    el.className = 'tuning-actions__status';
    if (type) el.classList.add(`tuning-actions__status--${type}`);
  }

  function buildFormCard(prefill = null) {
    const isEdit = prefill != null;
    const { start: defStart, end: defEnd } = getDefaultWindow();

    const card = document.createElement('div');
    card.className = 'card schedule-form__period schedule-form__period--expanded';
    card.style.marginBottom = '8px';
    card.innerHTML = `
      <div class="schedule-form__period-header">
        <span class="schedule-form__period-name">${isEdit ? 'Edit Experiment' : 'New Experiment'}</span>
        <button class="schedule-form__delete" id="ef-discard" title="Discard">×</button>
      </div>
      <div class="schedule-form__period-body">
        <div class="schedule-form__period-row">
          <div class="form-group">
            <label class="form-label">Name</label>
            <input class="form-input form-input--name" type="text" id="ef-name"
              placeholder="${room.name} overnight"
              value="${prefill?.name || ''}">
          </div>
          <div class="form-group">
            <label class="form-label">Signal Type</label>
            <select class="schedule-form__mode-select" id="ef-signal">
              ${EXCITATION_OPTIONS.map((o) =>
                `<option value="${o.value}"${(prefill?.signal_type || 'step') === o.value ? ' selected' : ''}>${o.label}</option>`
              ).join('')}
            </select>
          </div>
        </div>
        <div class="schedule-form__period-row">
          <div class="form-group">
            <label class="form-label">Start</label>
            <input class="form-input" type="datetime-local" id="ef-start"
              value="${isEdit ? tsToLocalInput(prefill.start_ts) : tsToLocalInput(defStart.getTime() / 1000)}">
          </div>
          <div class="form-group">
            <label class="form-label">End</label>
            <input class="form-input" type="datetime-local" id="ef-end"
              value="${isEdit ? tsToLocalInput(prefill.end_ts) : tsToLocalInput(defEnd.getTime() / 1000)}">
          </div>
        </div>
        <div class="schedule-form__period-row">
          <div class="form-group">
            <label class="form-label">Step Magnitude</label>
            <input class="form-input form-input--time" type="number" id="ef-step"
              min="0.05" max="1" step="0.05" value="${prefill?.step_pct ?? 1.0}">
            <span class="form-hint">0–1 fraction of max power</span>
          </div>
          <div class="form-group">
            <label class="form-label">Switching Period (min)</label>
            <input class="form-input form-input--time" type="number" id="ef-period"
              min="5" step="5" value="${prefill?.period_s != null ? Math.round(prefill.period_s / 60) : 60}">
            <span class="form-hint">For PRBS / pulse signals</span>
          </div>
          <div class="form-group">
            <label class="form-label">Settling Buffer (min)</label>
            <input class="form-input form-input--time" type="number" id="ef-settle"
              min="0" step="15" value="${prefill?.settle_s != null ? Math.round(prefill.settle_s / 60) : 120}">
            <span class="form-hint">Rest period at window end</span>
          </div>
        </div>
        <div class="schedule-form__period-row">
          <div class="form-group">
            <label class="form-label">Min Temperature (°C)</label>
            <input class="form-input form-input--time" type="number" id="ef-min"
              step="0.5" value="${prefill?.min_temp ?? 12}">
            <span class="form-hint">Frost protection floor</span>
          </div>
          <div class="form-group">
            <label class="form-label">Max Temperature (°C)</label>
            <input class="form-input form-input--time" type="number" id="ef-max"
              step="0.5" value="${prefill?.max_temp ?? 26}">
            <span class="form-hint">Safety ceiling</span>
          </div>
          <div class="form-group">
            <label class="form-label">Auto-save Dataset</label>
            <select class="schedule-form__mode-select" id="ef-autosave">
              <option value="yes"${(prefill?.auto_save !== false) ? ' selected' : ''}>Yes</option>
              <option value="no"${(prefill?.auto_save === false) ? ' selected' : ''}>No</option>
            </select>
          </div>
        </div>
        <div class="tuning-actions" style="margin-top:12px">
          <button class="btn btn--accent" id="ef-submit">${isEdit ? 'Update Experiment' : 'Schedule Experiment'}</button>
          <span class="tuning-actions__status" id="ef-status"></span>
        </div>
      </div>
    `;

    const submitBtn = card.querySelector('#ef-submit');
    const statusEl = card.querySelector('#ef-status');

    card.querySelector('#ef-discard').addEventListener('click', hideForm);

    submitBtn.addEventListener('click', async () => {
      const startTs = new Date(card.querySelector('#ef-start').value).getTime() / 1000;
      const endTs = new Date(card.querySelector('#ef-end').value).getTime() / 1000;
      if (!isFinite(startTs) || !isFinite(endTs) || endTs <= startTs) {
        setFormStatus(statusEl, 'End must be after start.', 'error');
        return;
      }
      const stepPct = parseFloat(card.querySelector('#ef-step').value);
      if (!(stepPct > 0 && stepPct <= 1)) {
        setFormStatus(statusEl, 'Step magnitude must be between 0 and 1.', 'error');
        return;
      }
      const settleS = Math.max(0, parseFloat(card.querySelector('#ef-settle').value) * 60);
      if (settleS >= endTs - startTs) {
        setFormStatus(statusEl, 'Settling buffer must be shorter than the experiment window.', 'error');
        return;
      }
      const updatingId = isEdit ? prefill.id : null;
      setFormStatus(statusEl, updatingId ? 'Updating…' : 'Scheduling…', 'running');
      submitBtn.disabled = true;
      try {
        await hass.callService('heating_assistant', 'schedule_experiment', {
          room_name: room.slug,
          start: startTs,
          end: endTs,
          name: card.querySelector('#ef-name').value || '',
          signal_type: card.querySelector('#ef-signal').value,
          step_pct: stepPct,
          period_s: Math.max(60, parseFloat(card.querySelector('#ef-period').value) * 60),
          settle_s: settleS,
          min_temp: parseFloat(card.querySelector('#ef-min').value),
          max_temp: parseFloat(card.querySelector('#ef-max').value),
          auto_save: card.querySelector('#ef-autosave').value === 'yes',
        });
        if (updatingId) {
          try {
            await hass.callService('heating_assistant', 'delete_experiment', { experiment_id: updatingId });
          } catch (_) { /* leave original if removal fails */ }
        }
        hideForm();
        await refresh();
      } catch (err) {
        setFormStatus(statusEl, 'Error: ' + (err.message || err), 'error');
        submitBtn.disabled = false;
      }
    });

    return card;
  }

  function showForm(prefill = null) {
    formVisible = true;
    formContainer.innerHTML = '';
    formContainer.appendChild(buildFormCard(prefill));
    btnAdd.textContent = '✕ Cancel';
  }

  function hideForm() {
    formVisible = false;
    formContainer.innerHTML = '';
    btnAdd.textContent = '+ Add Experiment';
  }

  function renderList() {
    const active = experiments.filter((e) => e.status === 'scheduled' || e.status === 'running');
    const terminal = experiments.filter((e) => e.status !== 'scheduled' && e.status !== 'running');

    sectionTitleEl.textContent = experiments.length > 0
      ? `EXPERIMENTS (${experiments.length})`
      : 'EXPERIMENTS';

    expListContainer.innerHTML = '';

    if (experiments.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'sched-detail__empty';
      empty.innerHTML = `
        <p>No experiments scheduled for this room.</p>
        <p>Click <strong>+ Add Experiment</strong> above to create one.</p>
      `;
      expListContainer.appendChild(empty);
      return;
    }

    const sorted = [
      ...active.sort((a, b) => (a.start_ts || 0) - (b.start_ts || 0)),
      ...terminal.sort((a, b) => (b.start_ts || 0) - (a.start_ts || 0)),
    ];

    sorted.forEach((exp) => {
      const { label: statusLabel, cls: statusCls } = expStatusInfo(exp.status);
      const isTerminal = exp.status === 'completed' || exp.status === 'cancelled';
      const isRunning = exp.status === 'running';
      const isScheduled = exp.status === 'scheduled';
      const isExpanded = expandedExpIds.has(exp.id);
      const modifier = expCardModifier(exp.status);

      const card = document.createElement('div');
      card.className = `card schedule-form__period${modifier}${isExpanded ? ' schedule-form__period--expanded' : ''}`;

      card.innerHTML = `
        <div class="schedule-form__period-header exp-card__header">
          <div class="exp-card__header-row">
            <span class="schedule-form__period-name">${exp.name || '(unnamed)'}</span>
            <span class="exp-signal-badge">${signalLabel(exp.signal_type)}</span>
            <span class="exp-status-badge ${statusCls}">${statusLabel}</span>
            ${isRunning ? `<button class="btn btn--ghost btn--sm" data-cancel="${exp.id}">Cancel</button>` : ''}
            ${(isScheduled || isTerminal) ? `<button class="schedule-form__delete" data-delete="${exp.id}" title="Delete">×</button>` : ''}
            <span class="schedule-form__expand-chevron">${isExpanded ? '▲' : '▼'}</span>
          </div>
          <div class="exp-card__header-window">
            <span class="schedule-form__period-time">${fmtExpWindow(exp)}</span>
          </div>
        </div>
        <div class="schedule-form__period-body"${isExpanded ? '' : ' hidden'}>
          <div class="schedule-form__period-row">
            <div class="form-group">
              <label class="form-label">Window Start</label>
              <span class="form-input form-input--time" style="display:block">${fmtExpDate(exp.start_ts)} ${fmtExpTime(exp.start_ts)}</span>
            </div>
            <div class="form-group">
              <label class="form-label">Window End</label>
              <span class="form-input form-input--time" style="display:block">${fmtExpDate(exp.end_ts)} ${fmtExpTime(exp.end_ts)}</span>
            </div>
            <div class="form-group">
              <label class="form-label">Signal Type</label>
              <span class="form-input form-input--time" style="display:block">${signalLabel(exp.signal_type)}</span>
            </div>
          </div>
          <div class="schedule-form__period-row">
            <div class="form-group">
              <label class="form-label">Step Magnitude</label>
              <span class="form-input form-input--time" style="display:block">${exp.step_pct ?? '—'}</span>
            </div>
            <div class="form-group">
              <label class="form-label">Switching Period</label>
              <span class="form-input form-input--time" style="display:block">${exp.period_s != null ? Math.round(exp.period_s / 60) + ' min' : '—'}</span>
            </div>
            <div class="form-group">
              <label class="form-label">Settling Buffer</label>
              <span class="form-input form-input--time" style="display:block">${exp.settle_s != null ? Math.round(exp.settle_s / 60) + ' min' : '—'}</span>
            </div>
          </div>
          <div class="schedule-form__period-row">
            <div class="form-group">
              <label class="form-label">Min Temp</label>
              <span class="form-input form-input--time" style="display:block">${exp.min_temp != null ? exp.min_temp + ' °C' : '—'}</span>
            </div>
            <div class="form-group">
              <label class="form-label">Max Temp</label>
              <span class="form-input form-input--time" style="display:block">${exp.max_temp != null ? exp.max_temp + ' °C' : '—'}</span>
            </div>
            <div class="form-group">
              <label class="form-label">Auto-save</label>
              <span class="form-input form-input--time" style="display:block">${exp.auto_save ? 'Yes' : 'No'}</span>
            </div>
          </div>
          ${isScheduled ? `
            <div class="tuning-actions" style="margin-top:8px">
              <button class="btn btn--ghost btn--sm" data-edit="${exp.id}">Edit</button>
            </div>
          ` : ''}
        </div>
      `;

      // Expand/collapse
      const header = card.querySelector('.schedule-form__period-header');
      const body = card.querySelector('.schedule-form__period-body');
      const chevron = card.querySelector('.schedule-form__expand-chevron');
      header.addEventListener('click', (e) => {
        if (e.target.closest('.schedule-form__delete') || e.target.closest('.btn')) return;
        const willExpand = !expandedExpIds.has(exp.id);
        if (willExpand) expandedExpIds.add(exp.id);
        else expandedExpIds.delete(exp.id);
        card.classList.toggle('schedule-form__period--expanded', willExpand);
        body.hidden = !willExpand;
        chevron.textContent = willExpand ? '▲' : '▼';
      });

      // Delete
      const deleteBtn = card.querySelector('[data-delete]');
      if (deleteBtn) {
        deleteBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          deleteBtn.disabled = true;
          try {
            await hass.callService('heating_assistant', 'delete_experiment', {
              experiment_id: deleteBtn.dataset.delete,
            });
            await refresh();
          } catch (_) { deleteBtn.disabled = false; }
        });
      }

      // Cancel
      const cancelBtn = card.querySelector('[data-cancel]');
      if (cancelBtn) {
        cancelBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          cancelBtn.disabled = true;
          try {
            await hass.callService('heating_assistant', 'cancel_experiment', {
              experiment_id: cancelBtn.dataset.cancel,
            });
            await refresh();
          } catch (_) { cancelBtn.disabled = false; }
        });
      }

      // Edit (scheduled only)
      const editBtn = card.querySelector('[data-edit]');
      if (editBtn) {
        editBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          showForm(exp);
        });
      }

      expListContainer.appendChild(card);
    });
  }

  async function refresh() {
    const all = await connection.listExperiments();
    if (all == null) return;
    experiments = all
      .filter((e) => e.room_slug === room.slug)
      .sort((a, b) => (b.start_ts || 0) - (a.start_ts || 0));
    renderList();
  }

  btnAdd.addEventListener('click', () => {
    if (formVisible) hideForm();
    else showForm();
  });

  refresh();
  const refreshTimer = setInterval(refresh, 30000);
  return { destroy() { clearInterval(refreshTimer); } };
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
  // Tracks which period indices are currently expanded in the UI
  let expandedSet = new Set();

  function getScheduleFromWS(roomSchedules) {
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
      const { text: modeText, cls: modeCls } = periodModeDisplay(p);

      const card = document.createElement('div');
      card.className = 'card schedule-form__period' +
        (isActive ? ' schedule-form__period--active' : '') +
        (isNext ? ' schedule-form__period--next' : '') +
        (isExpanded ? ' schedule-form__period--expanded' : '');

      // ── Collapsed header — always visible ──────────────────────────────────
      const cardHeader = document.createElement('div');
      cardHeader.className = 'schedule-form__period-header';
      cardHeader.innerHTML = `
        ${isActive ? '<span class="sched-detail__now-badge">NOW</span>' : ''}
        ${isNext ? '<span class="sched-detail__next-badge">NEXT</span>' : ''}
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
    const roomSchedules = await connection.getSchedules();
    const schedData = getScheduleFromWS(roomSchedules);
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

  // Initial render — fetch from WebSocket to get persisted schedule data
  connection.getSchedules().then((roomSchedules) => {
    const schedData = getScheduleFromWS(roomSchedules);
    renderToggle(schedData);
    initLocalPeriods(schedData);
    renderPeriodForms();
  });

  // Add experiment section
  const expSection = renderExperimentsSection(container, room, connection, hass);

  return {
    update(newState) {
      state = newState;
      connection.getSchedules().then((roomSchedules) => {
        const newData = getScheduleFromWS(roomSchedules);
        renderToggle(newData);
        if (!dirty) {
          initLocalPeriods(newData);
          renderPeriodForms();
        }
      });
    },
    destroy() { expSection.destroy(); },
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
