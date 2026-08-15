import { deleteDataset, createDataset } from '../ha-services.js?v=125';
import { createCollapsible } from '../components/collapsible.js?v=125';
import { makeDataset } from '../components/time-series-chart.js?v=125';

function _fmtTs(ts) {
  if (ts == null) return '—';
  const d = new Date(ts * 1000);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function _fmtDuration(seconds) {
  if (seconds == null || !isFinite(seconds)) return '—';
  const h = seconds / 3600;
  if (h >= 1) return `${h.toFixed(1)} h`;
  return `${Math.round(seconds / 60)} min`;
}

const PE_CATEGORIES = [
  { id: 'closed_window_envelope', short: 'Envelope', label: 'Closed-window envelope' },
  { id: 'heater_excitation', short: 'Heater', label: 'Heater excitation' },
  { id: 'solar_variation', short: 'Solar', label: 'Solar variation' },
  { id: 'open_contact', short: 'Open UA', label: 'Open-contact (extra UA)' },
];

const PE_GUIDES = {
  closed_window_envelope: {
    title: 'Closed-window envelope',
    target: 'At least 12 hours closed. A full day is better.',
    why: 'This is how the room holds heat when windows and doors stay shut — the core thermal-mass and insulation fit.',
    do: 'Keep windows and doors closed and let heating run as usual. Overnight, or while you are out, is ideal: no extra discomfort, and the house stays quiet.',
    avoid: 'Do not leave a window ajar during this period. You do not need to change setpoints.',
    store: 'Set the estimation window to that closed period, Save Current Window, then Use the new set.',
  },
  heater_excitation: {
    title: 'Heater excitation',
    target: 'A few on/off cycles in the saved window. Constant-off or always-on will not cover it.',
    why: 'Heater scale is only visible when the heater actually cycles.',
    do: 'Piggyback on cycling you already have: price-driven control, a scheduled setback, or a small setpoint change while you are out. A couple of on/off cycles is enough.',
    avoid: 'Do not force large comfort-band violations. A modest, existing cycle is better than a long override.',
    store: 'Save a window that includes those cycles, then Use the set.',
  },
  solar_variation: {
    title: 'Solar variation',
    target: 'A daytime stretch with changing sunlight. Night-only windows will not cover it.',
    why: 'The solar scale needs sunlight that actually changes during the window.',
    do: 'Record a clear or mixed day with curtains as usual. No heating change is required.',
    avoid: 'Do not stay up to run a night-only experiment. Wait for daylight.',
    store: 'Save that daytime window, then Use the set.',
  },
  open_contact: {
    title: 'Open-contact (extra UA)',
    target: 'About 30 minutes with a window or door open.',
    why: 'Extra outdoor exchange is only visible while a contact is actually open.',
    do: 'Piggyback on a planned airing you already do. Thirty minutes is enough; close afterwards. Prefer mild outdoor weather.',
    avoid: 'Do not leave a window open overnight in cold weather. Short and intentional beats long and uncomfortable.',
    store: 'Include the open period in the saved window, then Use the set.',
    na: 'This room has no window or door contact configured. Open UA is not required for recommended estimation.',
  },
};

function _toLocalInput(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function _datasetCategoryTags(dataset) {
  const cats = Array.isArray(dataset && dataset.coverage_categories)
    ? dataset.coverage_categories
    : [];
  return cats.filter((cat) => cat && cat.status === 'checked');
}

function _unionSelectedStatuses(datasets, selectedIds) {
  const byId = {};
  for (const spec of PE_CATEGORIES) {
    byId[spec.id] = 'unchecked';
  }
  for (const ds of datasets) {
    const cats = Array.isArray(ds.coverage_categories) ? ds.coverage_categories : [];
    for (const cat of cats) {
      if (cat && cat.status === 'na') byId[cat.id] = 'na';
    }
  }
  for (const ds of datasets) {
    if (!selectedIds.has(ds.id)) continue;
    const cats = Array.isArray(ds.coverage_categories) ? ds.coverage_categories : [];
    for (const cat of cats) {
      if (cat && cat.status === 'checked') byId[cat.id] = 'checked';
    }
  }
  return byId;
}

function _datasetCovers(dataset, catId) {
  const cats = Array.isArray(dataset && dataset.coverage_categories)
    ? dataset.coverage_categories
    : [];
  return cats.some((cat) => cat && cat.id === catId && cat.status === 'checked');
}

// Build the stored-dataset cards, append them to the container, and wire all
// their interactions.  Returns a handle with a ``destroy`` method that tears
// down the periodic refresh timer.
export function setupDatasetsAndExperiments(ctx) {
  const {
    container, paramsCard, room, roomSlug, hass, connection,
    windowStartInput, windowEndInput, horizonInput, applyWindowMode, toDatetimeLocal,
    getWindowMode, setSelectedDataset, clearSelectedDataset, onDatasetSelectionRenderer,
  } = ctx;

  const saveMount = paramsCard.querySelector('#ds-save-mount');
  saveMount.innerHTML = `
    <div class="params-subsection__title">Save Current Window</div>
    <p class="params-subsection__desc">
      Store the parameter-estimation window configured above as a named, permanent dataset.
      After saving, Use the set in Stored Datasets so matching category tiles turn teal.
    </p>
    <div class="ds-save-row ds-save-row--compact">
      <div class="form-group ds-save-row__name">
        <label class="form-label" for="ds-name">New dataset name</label>
        <input class="form-input" type="text" id="ds-name" placeholder="e.g. Cold snap — ${room.name}">
      </div>
      <div class="form-group ds-save-row__notes">
        <label class="form-label" for="ds-notes">Notes (optional)</label>
        <input class="form-input" type="text" id="ds-notes" placeholder="description">
      </div>
      <div class="ds-save-row__action">
        <button class="btn btn--primary" id="btn-save-dataset">Save Current Window</button>
        <span class="tuning-actions__status" id="ds-status"></span>
      </div>
    </div>
    <div class="pe-save-coverage" id="pe-save-coverage">
      <div class="pe-save-coverage__title">This window would cover</div>
      <div class="pe-save-coverage__chips" id="pe-save-coverage-chips"></div>
      <p class="pe-save-coverage__hint">
        Tap a category tile below for a low-comfort way to collect any missing data.
      </p>
    </div>
    <div id="ds-selected-note" class="ds-loaded-note"></div>
  `;

  const dsListSection = document.createElement('div');
  dsListSection.className = 'card tuning-section';
  const dsCollapsible = createCollapsible({ title: 'Stored Datasets', open: true });
  dsCollapsible.body.innerHTML = `
    <p class="tuning-section__desc" style="margin:0 0 12px">
      Use stored datasets for joint automatic parameter estimation. Each set
      shows the categories it covers. Using a set lights those categories.
    </p>
    <div class="pe-coverage" id="pe-coverage">
      <div class="params-subsection__title">Recommended data</div>
      <p class="pe-coverage__desc">
        Tap a category for a low-comfort recipe, then save that window and Use
        the set. Select at least one set in each category, then run the
        recommended estimate. These indicators are a guide — they do not
        exclude samples from the fit.
      </p>
      <div class="pe-coverage-row" id="pe-coverage-list" role="group"
        aria-label="Recommended data categories"></div>
      <div class="pe-coverage-guide" id="pe-coverage-guide" hidden></div>
    </div>
    <div class="ds-toolbar">
      <button class="btn btn--accent" id="btn-identify-selected" disabled>
        Run Automatic Parameter Estimation (0)
      </button>
      <button class="btn btn--ghost btn--sm" id="btn-clear-selection" disabled>Clear selection</button>
      <span class="tuning-actions__status" id="ds-id-status"></span>
    </div>
    <div id="ds-list" class="store-list"></div>
  `;
  dsListSection.appendChild(dsCollapsible.element);
  container.appendChild(dsListSection);

  // ---- References --------------------------------------------------------
  const dsStatus = saveMount.querySelector('#ds-status');
  const dsListEl = dsCollapsible.body.querySelector('#ds-list');
  const dsNameInput = saveMount.querySelector('#ds-name');
  const dsNotesInput = saveMount.querySelector('#ds-notes');
  const dsSelectedNote = saveMount.querySelector('#ds-selected-note');
  const dsIdStatus = dsCollapsible.body.querySelector('#ds-id-status');
  const btnIdentifySelected = dsCollapsible.body.querySelector('#btn-identify-selected');
  const btnClearSelection = dsCollapsible.body.querySelector('#btn-clear-selection');
  const coverageListEl = dsCollapsible.body.querySelector('#pe-coverage-list');
  const coverageGuideEl = dsCollapsible.body.querySelector('#pe-coverage-guide');
  const saveCoverageChipsEl = saveMount.querySelector('#pe-save-coverage-chips');

  // Multi-select set of dataset ids chosen for joint identification.
  const selectedIds = new Set();
  let lastDatasets = [];
  let openGuideId = null;
  let previewSeq = 0;

  function setStatus(el, text, type = '') {
    el.textContent = text;
    el.className = 'tuning-actions__status';
    if (type) el.classList.add(`tuning-actions__status--${type}`);
  }

  // Renderer for the "loaded dataset" note (wired back to the page closure).
  // A loaded dataset drives the top parameter estimation / EKF / open-loop tools
  // through its snapshotted data; the note offers a way back to the live window.
  onDatasetSelectionRenderer((label) => {
    dsSelectedNote.innerHTML = '';
    if (label) {
      const text = document.createElement('span');
      text.innerHTML = `Loaded for the tools above: <strong>${label}</strong> `;
      dsSelectedNote.appendChild(text);
      const clearBtn = document.createElement('button');
      clearBtn.className = 'btn btn--ghost btn--sm';
      clearBtn.textContent = 'Use live window instead';
      clearBtn.addEventListener('click', () => clearSelectedDataset());
      dsSelectedNote.appendChild(clearBtn);
    }
    // Re-mark the loaded row whenever the loaded dataset changes.
    markLoadedRow();
  });

  // ---- Multi-select identification ---------------------------------------
  function _requiredReady(statuses) {
    return PE_CATEGORIES.every((spec) => {
      const status = statuses[spec.id] || 'unchecked';
      return status === 'checked' || status === 'na';
    });
  }

  function updateSelectionToolbar() {
    const n = selectedIds.size;
    const statuses = _unionSelectedStatuses(lastDatasets, selectedIds);
    const ready = n > 0 && _requiredReady(statuses);
    if (ready) {
      btnIdentifySelected.textContent = 'Run recommended estimation';
    } else {
      btnIdentifySelected.textContent = `Run Automatic Parameter Estimation (${n})`;
    }
    btnIdentifySelected.disabled = n === 0;
    btnIdentifySelected.classList.toggle('btn--accent', ready || n > 0);
    btnClearSelection.disabled = n === 0;
    refreshCoverage();
  }

  function _matchingDatasetNames(catId) {
    return lastDatasets
      .filter((ds) => _datasetCovers(ds, catId))
      .map((ds) => ds.name || '(unnamed)');
  }

  function applyGuideHighlight() {
    dsListEl.querySelectorAll('.store-row').forEach((row) => {
      const ds = lastDatasets.find((item) => item.id === row.dataset.id);
      const match = Boolean(openGuideId && ds && _datasetCovers(ds, openGuideId));
      row.classList.toggle('store-row--guide-match', match);
    });
  }

  function renderGuide(statuses) {
    if (!coverageGuideEl) return;
    const spec = PE_CATEGORIES.find((item) => item.id === openGuideId);
    const guide = spec ? PE_GUIDES[spec.id] : null;
    if (!spec || !guide) {
      coverageGuideEl.hidden = true;
      coverageGuideEl.innerHTML = '';
      applyGuideHighlight();
      return;
    }
    const status = (statuses && statuses[spec.id]) || 'unchecked';
    const supplied = status === 'checked';
    const na = status === 'na';
    const kicker = na ? 'N/A' : (supplied ? 'Supplied' : 'Not set');
    const matching = _matchingDatasetNames(spec.id);
    const matchLine = na
      ? ''
      : (matching.length
        ? `<p class="pe-coverage-guide__match">Already covering: ${matching.join(', ')}</p>`
        : '<p class="pe-coverage-guide__match">No stored set covers this yet.</p>');
    coverageGuideEl.hidden = false;
    coverageGuideEl.innerHTML = na
      ? `
        <div class="pe-coverage-guide__kicker">${kicker}</div>
        <h3 class="pe-coverage-guide__title">${guide.title}</h3>
        <p class="pe-coverage-guide__why">${guide.na}</p>`
      : `
        <div class="pe-coverage-guide__kicker">${kicker} · ${guide.target}</div>
        <h3 class="pe-coverage-guide__title">${guide.title}</h3>
        <p class="pe-coverage-guide__why">${guide.why}</p>
        <ol class="pe-coverage-guide__steps">
          <li>${guide.do}</li>
          <li>${guide.avoid}</li>
          <li>${guide.store}</li>
        </ol>
        ${matchLine}
        <button type="button" class="btn btn--ghost btn--sm" id="pe-guide-scroll-save">
          Scroll to Save Current Window
        </button>`;
    const scrollBtn = coverageGuideEl.querySelector('#pe-guide-scroll-save');
    if (scrollBtn) {
      scrollBtn.addEventListener('click', () => {
        saveMount.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
    applyGuideHighlight();
  }

  function toggleGuide(catId) {
    openGuideId = openGuideId === catId ? null : catId;
    refreshCoverage();
  }

  function _coverageOpts() {
    const { startTs, endTs } = currentWindowBounds();
    if (getWindowMode() === 'custom') {
      return { roomSlug, windowStart: startTs, windowEnd: endTs };
    }
    const hrs = horizonInput ? parseFloat(horizonInput.value) : 6;
    return { roomSlug, horizonHours: isFinite(hrs) ? hrs : 6 };
  }

  async function refreshSavePreview() {
    if (!saveCoverageChipsEl || !connection || typeof connection.getPeCoverage !== 'function') {
      return;
    }
    const seq = ++previewSeq;
    const result = await connection.getPeCoverage(_coverageOpts());
    if (seq !== previewSeq) return;
    const byId = {};
    const categories = (result && Array.isArray(result.categories)) ? result.categories : [];
    for (const cat of categories) {
      if (cat && cat.id) byId[cat.id] = cat.status || 'unchecked';
    }
    const coveredShorts = [];
    saveCoverageChipsEl.innerHTML = PE_CATEGORIES.map((spec) => {
      const status = byId[spec.id] || 'unchecked';
      const na = status === 'na';
      const on = status === 'checked';
      if (on) coveredShorts.push(spec.short);
      const state = na ? 'N/A' : (on ? 'Yes' : 'No');
      return `<span class="pe-save-chip pe-save-chip--${status}">${spec.short}: ${state}</span>`;
    }).join('');
    if (!(dsNameInput.value || '').trim() && coveredShorts.length) {
      dsNameInput.placeholder = `${coveredShorts.join(' + ')} — ${room.name}`;
    }
  }

  function refreshCoverage() {
    if (!coverageListEl) return;
    const statuses = _unionSelectedStatuses(lastDatasets, selectedIds);
    coverageListEl.innerHTML = PE_CATEGORIES.map((spec) => {
      const status = statuses[spec.id] || 'unchecked';
      const na = status === 'na';
      const on = status === 'checked';
      const open = openGuideId === spec.id;
      const state = na ? 'N/A' : (on ? 'Supplied' : 'Not set');
      const aria = na ? 'not applicable' : (on ? 'supplied' : 'not supplied');
      const openClass = open ? ' pe-coverage-tile--open' : '';
      return `<button type="button" class="pe-coverage-tile pe-coverage-tile--${status}${openClass}"
        data-pe-cat="${spec.id}" aria-pressed="${open ? 'true' : 'false'}"
        aria-expanded="${open ? 'true' : 'false'}" aria-controls="pe-coverage-guide"
        aria-label="${spec.label}: ${aria}. Open how to collect." title="${spec.label}">
        <span class="pe-coverage-tile__name">${spec.short}</span>
        <span class="pe-coverage-tile__state">${state}</span>
      </button>`;
    }).join('');
    coverageListEl.querySelectorAll('[data-pe-cat]').forEach((btn) => {
      btn.addEventListener('click', () => toggleGuide(btn.dataset.peCat));
    });
    renderGuide(statuses);
    refreshSavePreview();
  }

  function markLoadedRow() {
    const loadedId = ctx.getSelectedDatasetId ? ctx.getSelectedDatasetId() : null;
    dsListEl.querySelectorAll('.store-row').forEach((row) => {
      row.classList.toggle('store-row--loaded', row.dataset.id === loadedId);
    });
  }

  btnClearSelection.addEventListener('click', () => {
    selectedIds.clear();
    dsListEl.querySelectorAll('.store-row').forEach((row) => {
      row.classList.remove('store-row--selected');
      const b = row.querySelector('[data-sel]');
      if (b) {
        b.classList.remove('btn--accent');
        b.classList.add('btn--ghost');
        const lbl = b.querySelector('.store-sel-label');
        if (lbl) lbl.textContent = 'Use';
      }
    });
    updateSelectionToolbar();
  });

  btnIdentifySelected.addEventListener('click', async () => {
    if (selectedIds.size === 0) return;
    btnIdentifySelected.disabled = true;
    await ctx.runAutoIdentification(
      { dataset_ids: [...selectedIds] }, dsIdStatus,
    );
    updateSelectionToolbar();
  });

  // ---- Dataset saving / listing -----------------------------------------
  function currentWindowBounds() {
    // Resolve the window the user currently has configured into [start, end]
    // UNIX seconds, honouring whichever tab (Recent Horizon or Custom Date
    // Range) is actively selected — the custom inputs always hold a value
    // (they're pre-filled from the horizon as a default), so they must only
    // be used when the Custom tab is actually the one showing.
    if (getWindowMode() === 'custom') {
      const startVal = windowStartInput.value;
      const endVal = windowEndInput.value;
      const startTs = startVal ? new Date(startVal).getTime() / 1000 : NaN;
      const endTs = endVal ? new Date(endVal).getTime() / 1000 : NaN;
      if (isFinite(startTs) && isFinite(endTs) && endTs > startTs) {
        return { startTs, endTs };
      }
    }
    const hrs = horizonInput ? parseFloat(horizonInput.value) : 6;
    const endTs = Date.now() / 1000;
    const startTs = endTs - (isFinite(hrs) ? hrs : 6) * 3600;
    return { startTs, endTs };
  }

  saveMount.querySelector('#btn-save-dataset').addEventListener('click', async () => {
    const name = (dsNameInput.value || '').trim();
    if (!name) {
      setStatus(dsStatus, 'Enter a dataset name first.', 'error');
      return;
    }
    const { startTs, endTs } = currentWindowBounds();
    setStatus(dsStatus, 'Saving…', 'running');
    try {
      const resp = await createDataset(hass, {
        name,
        window_start: startTs,
        window_end: endTs,
        room_name: roomSlug,
        notes: dsNotesInput.value || '',
      });
      const count = resp?.response?.record_count;
      setStatus(dsStatus, count != null ? `Saved (${count} records).` : 'Saved.', 'success');
      dsNameInput.value = '';
      dsNotesInput.value = '';
      await refreshDatasets();
    } catch (err) {
      setStatus(dsStatus, 'Error: ' + (err.message || err), 'error');
    }
  });

  // Load a single dataset into the custom window and mark it as the active
  // source for the top parameter estimation / EKF / open-loop tools, so the user
  // can validate or manually identify it on its own.
  function loadDataset(datasetId, datasets) {
    const ds = (datasets || lastDatasets).find((d) => d.id === datasetId);
    if (!ds) return;
    if (ds.data_start_ts != null) {
      windowStartInput.value = toDatetimeLocal(new Date(ds.data_start_ts * 1000));
    }
    if (ds.data_end_ts != null) {
      windowEndInput.value = toDatetimeLocal(new Date(ds.data_end_ts * 1000));
    }
    applyWindowMode('custom');
    setSelectedDataset(ds.id, ds.name);
  }

  // Toggle a dataset's membership in the multi-select identification set.
  function toggleSelected(datasetId, rowEl, btnEl) {
    if (selectedIds.has(datasetId)) {
      selectedIds.delete(datasetId);
    } else {
      selectedIds.add(datasetId);
    }
    const on = selectedIds.has(datasetId);
    rowEl.classList.toggle('store-row--selected', on);
    btnEl.classList.toggle('btn--accent', on);
    btnEl.classList.toggle('btn--ghost', !on);
    const lbl = btnEl.querySelector('.store-sel-label');
    if (lbl) lbl.textContent = on ? 'Selected' : 'Use';
    updateSelectionToolbar();
  }

  async function refreshDatasets() {
    const datasets = await connection.listDatasets(roomSlug);
    // ``null`` means the fetch failed (transient WebSocket error). Keep the
    // currently-rendered list and selection rather than wiping them to an
    // empty "no datasets" state that only a page refresh would recover from.
    if (datasets == null) return;
    lastDatasets = datasets;
    dsCollapsible.setBadge(datasets.length ? `${datasets.length}` : '');

    // Drop selections for datasets that no longer exist.
    const liveIds = new Set(datasets.map((d) => d.id));
    [...selectedIds].forEach((id) => { if (!liveIds.has(id)) selectedIds.delete(id); });

    if (datasets.length === 0) {
      dsListEl.innerHTML = '<span class="tuning-section__desc">No stored datasets yet. Save the current window to create one.</span>';
      updateSelectionToolbar();
      return;
    }

    const loadedId = ctx.getSelectedDatasetId ? ctx.getSelectedDatasetId() : null;
    const stat = (label, value) => `<span class="store-stat"><span class="store-stat__k">${label}</span><span class="store-stat__v">${value}</span></span>`;
    dsListEl.innerHTML = datasets.map((d) => {
      const sel = selectedIds.has(d.id);
      const isLoaded = d.id === loadedId;
      const roomLabel = d.room_name || d.room_slug || '—';
      const source = (d.source || '').toLowerCase() === 'experiment' ? 'Experiment' : 'Manual';
      const span = (d.data_start_ts != null)
        ? `${_fmtTs(d.data_start_ts)} → ${_fmtTs(d.data_end_ts)}`
        : '—';
      const dur = _fmtDuration(d.duration_s != null ? d.duration_s : (d.data_end_ts - d.data_start_ts));
      const recs = d.record_count != null ? `${d.record_count}` : '—';
      const notes = d.notes ? `<div class="store-row__notes">${d.notes}</div>` : '';
      const catTags = _datasetCategoryTags(d).map((cat) => {
        const short = cat.short_label || PE_CATEGORIES.find((s) => s.id === cat.id)?.short || cat.label || '';
        return `<span class="store-row__tag store-row__tag--cat">${short}</span>`;
      }).join('');
      return `
        <div class="store-row store-row--dataset ${sel ? 'store-row--selected' : ''} ${isLoaded ? 'store-row--loaded' : ''}" data-id="${d.id}">
          <div class="store-row__main">
            <div class="store-row__name">
              <span class="store-row__title">${d.name || '(unnamed)'}</span>
              <span class="store-row__tag store-row__tag--${source.toLowerCase() === 'experiment' ? 'accent' : ''}">${source}</span>
              ${catTags}
            </div>
            <div class="store-row__meta">
              ${stat('Room', roomLabel)}${stat('Window', span)}${stat('Length', dur)}${stat('Points', recs)}
            </div>
            ${notes}
          </div>
          <div class="store-row__actions">
            <button class="btn btn--sm ${sel ? 'btn--accent' : 'btn--ghost'}" data-sel="${d.id}">
              <span class="store-sel-label">${sel ? 'Selected' : 'Use'}</span>
            </button>
            <button class="btn btn--ghost btn--sm" data-load="${d.id}">Load</button>
            <button class="btn btn--ghost btn--sm store-row__del" data-del="${d.id}">Delete</button>
          </div>
        </div>`;
    }).join('');

    dsListEl.querySelectorAll('[data-sel]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const row = btn.closest('.store-row');
        toggleSelected(btn.dataset.sel, row, btn);
      });
    });
    dsListEl.querySelectorAll('[data-load]').forEach((btn) => {
      btn.addEventListener('click', () => loadDataset(btn.dataset.load, datasets));
    });
    dsListEl.querySelectorAll('[data-del]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (!window.confirm('Delete this dataset? This cannot be undone.')) return;
        btn.disabled = true;
        try {
          await deleteDataset(hass, btn.dataset.del);
          selectedIds.delete(btn.dataset.del);
          await refreshDatasets();
        } catch (_) { btn.disabled = false; }
      });
    });

    updateSelectionToolbar();
  }

  // Initial population, plus a light periodic refresh so auto-saved datasets
  // appear without a manual reload.
  refreshDatasets();
  const timer = setInterval(() => {
    refreshDatasets();
  }, 30000);

  return {
    destroy() { clearInterval(timer); },
    refreshCoverage,
  };
}

export function formatMass(val) {
  const num = parseFloat(val);
  if (isNaN(num)) return '—';
  if (num >= 1e6) return (num / 1e6).toFixed(2) + ' MJ/K';
  if (num >= 1e3) return (num / 1e3).toFixed(0) + ' kJ/K';
  return num.toFixed(0) + ' J/K';
}


export function buildEkfChart(chart, simulation) {
  if (!simulation || simulation.length === 0) {
    chart.render([], {});
    return;
  }

  const measured = [];
  const predicted = [];
  const covUpper = [];
  const covLower = [];
  const predictedWall = [];
  const wallCovUpper = [];
  const wallCovLower = [];
  let hasWall = false;

  // Open-window samples arrive as null measured/predicted.  Push an explicit
  // {x, y: null} so the line datasets (drawn with spanGaps:false) break at the
  // gap rather than drawing a straight segment across the excluded period.
  // Measured is a scatter (showLine:false), so its nulls are simply not plotted.
  for (const entry of simulation) {
    const t = new Date(entry.time).getTime();
    if (isNaN(t)) continue;
    if (entry.measured != null) measured.push({ x: t, y: entry.measured });
    predicted.push({ x: t, y: entry.predicted ?? null });
    covUpper.push({ x: t, y: entry.cov_upper ?? null });
    covLower.push({ x: t, y: entry.cov_lower ?? null });
    if (entry.predicted_wall != null) hasWall = true;
    predictedWall.push({ x: t, y: entry.predicted_wall ?? null });
    wallCovUpper.push({ x: t, y: entry.wall_cov_upper ?? null });
    wallCovLower.push({ x: t, y: entry.wall_cov_lower ?? null });
  }

  const datasets = [
    makeDataset('Measured (air)', measured, '#e57373', {
      borderWidth: 0, pointRadius: 2, pointHoverRadius: 4,
      pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
      showLine: false,
    }),
    makeDataset('Predicted (air)', predicted, '#4fc3f7', { borderWidth: 2, spanGaps: false }),
    makeDataset('Above 2σ (air)', covUpper, 'rgba(79,195,247,0.25)', {
      borderWidth: 0, pointRadius: 0, fill: false, spanGaps: false,
    }),
    makeDataset('Below 2σ (air)', covLower, 'rgba(79,195,247,0.25)', {
      borderWidth: 0, pointRadius: 0,
      fill: '-1', backgroundColor: 'rgba(79,195,247,0.10)', spanGaps: false,
    }),
  ];

  if (hasWall) {
    datasets.push(
      makeDataset('Predicted (wall)', predictedWall, '#a5d6a7', { borderWidth: 2, borderDash: [4, 3], spanGaps: false }),
      makeDataset('Above 2σ (wall)', wallCovUpper, 'rgba(165,214,167,0.25)', {
        borderWidth: 0, pointRadius: 0, fill: false, spanGaps: false,
      }),
      makeDataset('Below 2σ (wall)', wallCovLower, 'rgba(165,214,167,0.25)', {
        borderWidth: 0, pointRadius: 0,
        fill: '-1', backgroundColor: 'rgba(165,214,167,0.10)', spanGaps: false,
      }),
    );
  }

  const allSeries = [measured, predicted, covUpper, covLower, predictedWall, wallCovUpper, wallCovLower];
  const { yMin, yMax } = computeChartLimits(allSeries);
  chart.render(datasets, { yMin, yMax });
}

export function buildOlChart(chart, simulation) {
  if (!simulation || simulation.length === 0) {
    chart.render([], {});
    return;
  }

  const measured = [];
  const predicted = [];
  const predictedWall = [];
  let hasWall = false;

  // Push explicit nulls at open-window gaps so the predicted line breaks
  // (spanGaps:false) instead of bridging straight across the excluded period.
  for (const entry of simulation) {
    const t = new Date(entry.time).getTime();
    if (isNaN(t)) continue;
    if (entry.measured != null) measured.push({ x: t, y: entry.measured });
    predicted.push({ x: t, y: entry.predicted ?? null });
    if (entry.predicted_wall != null) hasWall = true;
    predictedWall.push({ x: t, y: entry.predicted_wall ?? null });
  }

  const datasets = [
    makeDataset('Measured (air)', measured, '#e57373', {
      borderWidth: 0, pointRadius: 2, pointHoverRadius: 4,
      pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
      showLine: false,
    }),
    makeDataset('Predicted (air)', predicted, '#4fc3f7', { borderWidth: 2, spanGaps: false }),
  ];

  if (hasWall) {
    datasets.push(
      makeDataset('Predicted (wall)', predictedWall, '#a5d6a7', { borderWidth: 2, borderDash: [4, 3], spanGaps: false }),
    );
  }

  const { yMin, yMax } = computeChartLimits([measured, predicted, predictedWall]);
  chart.render(datasets, { yMin, yMax });
}

function computeChartLimits(dataSets) {
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const points of dataSets) {
    for (const p of points) {
      if (p.y == null) continue;  // gap placeholder — ignore in limit search
      if (p.y < yMin) yMin = p.y;
      if (p.y > yMax) yMax = p.y;
    }
  }
  if (!isFinite(yMin) || !isFinite(yMax)) return { yMin: undefined, yMax: undefined };
  const margin = (yMax - yMin) * 0.05 || 0.5;
  return { yMin: yMin - margin, yMax: yMax + margin };
}
