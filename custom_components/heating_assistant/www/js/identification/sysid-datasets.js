import { deleteDataset, createDataset } from '../ha-services.js?v=93';

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

function _toLocalInput(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
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
      Store the identification window configured above as a named, permanent dataset.
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
    <div id="ds-selected-note" class="ds-loaded-note"></div>
  `;

  const dsListSection = document.createElement('div');
  dsListSection.className = 'card tuning-section';
  const dsCollapsible = createCollapsible({ title: 'Stored Datasets', open: false });
  dsCollapsible.body.innerHTML = `
    <p class="tuning-section__desc" style="margin:0 0 12px">
      Select datasets for joint automatic identification, or load one into the
      custom window to inspect, validate, or identify it on its own.
    </p>
    <div class="ds-toolbar">
      <button class="btn btn--accent" id="btn-identify-selected" disabled>
        Run Automatic Identification (0)
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

  // Multi-select set of dataset ids chosen for joint identification.
  const selectedIds = new Set();

  function setStatus(el, text, type = '') {
    el.textContent = text;
    el.className = 'tuning-actions__status';
    if (type) el.classList.add(`tuning-actions__status--${type}`);
  }

  // Renderer for the "loaded dataset" note (wired back to the page closure).
  // A loaded dataset drives the top auto-identification / EKF / open-loop tools
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
  function updateSelectionToolbar() {
    const n = selectedIds.size;
    btnIdentifySelected.textContent = `Run Automatic Identification (${n})`;
    btnIdentifySelected.disabled = n === 0;
    btnClearSelection.disabled = n === 0;
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
  // source for the top auto-identification / EKF / open-loop tools, so the user
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

  let lastDatasets = [];
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
      return `
        <div class="store-row store-row--dataset ${sel ? 'store-row--selected' : ''} ${isLoaded ? 'store-row--loaded' : ''}" data-id="${d.id}">
          <div class="store-row__main">
            <div class="store-row__name">
              <span class="store-row__title">${d.name || '(unnamed)'}</span>
              <span class="store-row__tag store-row__tag--${source.toLowerCase() === 'experiment' ? 'accent' : ''}">${source}</span>
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
  };
}

function formatMass(val) {
  const num = parseFloat(val);
  if (isNaN(num)) return '—';
  if (num >= 1e6) return (num / 1e6).toFixed(2) + ' MJ/K';
  if (num >= 1e3) return (num / 1e3).toFixed(0) + ' kJ/K';
  return num.toFixed(0) + ' J/K';
}


function buildEkfChart(chart, simulation) {
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

function buildOlChart(chart, simulation) {
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
