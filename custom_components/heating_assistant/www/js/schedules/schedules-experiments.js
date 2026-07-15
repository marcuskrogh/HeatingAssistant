import { signalLabel, experimentRowHtml } from '../experiment-utils.js?v=102';
import { cancelExperiment, deleteExperiment, scheduleExperiment } from '../ha-services.js?v=102';
import {
  EXCITATION_OPTIONS, fmtExpWindow, tsToLocalInput, expStatusInfo, expCardModifier,
} from './schedules-shared.js?v=102';

export function renderExperimentsSection(container, room, connection, hass) {
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
        await scheduleExperiment(hass, {
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
            await deleteExperiment(hass, updatingId);
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
            await deleteExperiment(hass, deleteBtn.dataset.delete);
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
            await cancelExperiment(hass, cancelBtn.dataset.cancel);
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
