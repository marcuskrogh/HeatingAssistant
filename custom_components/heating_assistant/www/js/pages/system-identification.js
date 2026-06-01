import { TimeSeriesChart, makeDataset, loadChartJs, createSparkline, historyToDataPoints } from '../components/time-series-chart.js';
import { createKpiCard, updateKpiCard } from '../components/kpi-card.js';
import { entityValue, formatNumber, systemEntity } from '../utils.js';

const DEFAULTS = {
  sigma_w: 0.1,
  sigma_v: 0.5,
  sigma_b: 0.002,
  thermal_mass: 5000000,
  r_external: 0.05,
  horizon_hours: 6,
  window_open_debounce: 120,
  window_open_close_settle: 300,
  window_open_q_inflation: 10.0,
};

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

export function renderSystemIdentification(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderIdentificationDetail(container, slug, rooms, state, connection, hass);
  }
  return renderIdentificationIndex(container, rooms, state, connection);
}

// ---------------------------------------------------------------------------
// Index view — room selection grid
// ---------------------------------------------------------------------------

function renderIdentificationIndex(container, rooms, state, connection) {
  container.innerHTML = '';
  const sparklines = [];

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'SYSTEM IDENTIFICATION';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Select a room to view and configure its system identification parameters, run parameter estimation, and validate model fit.';
  container.appendChild(desc);

  const grid = document.createElement('div');
  grid.className = 'grid-rooms';
  container.appendChild(grid);

  function buildTiles(st) {
    grid.innerHTML = '';
    for (const room of rooms) {
      const tile = document.createElement('div');
      tile.className = 'card card--clickable identification-tile';

      const fitEntity = st[`sensor.heating_assistant_${room.slug}_model_fit_quality`];
      const fitVal = fitEntity ? parseFloat(fitEntity.state) : null;
      const fitInfo = modelFitBadge(fitVal);

      tile.innerHTML = `
        <span class="room-tile__name">${room.name}</span>
        <div class="identification-tile__kpi-row">
          <div class="identification-tile__kpi-box ${fitInfo.class}">
            <span class="identification-tile__kpi-label">FIT</span>
            <span class="identification-tile__kpi-value">${fitInfo.label}</span>
          </div>
          <div class="identification-tile__kpi-box">
            <span class="identification-tile__kpi-label">RMSE</span>
            <span class="identification-tile__kpi-value" data-room="${room.slug}">\u2014</span>
          </div>
        </div>
        <div class="identification-tile__sparkline">
          <canvas data-sparkline="${room.slug}"></canvas>
        </div>
      `;
      tile.addEventListener('click', () => {
        window.location.hash = `#identification/${room.slug}`;
      });
      grid.appendChild(tile);
    }
  }

  function computeRMSE(filtered, measured) {
    let sumSq = 0, count = 0;
    for (const m of measured) {
      let best = null, bestDist = Infinity;
      for (const f of filtered) {
        const dist = Math.abs(f.x - m.x);
        if (dist < bestDist) { bestDist = dist; best = f; }
      }
      if (best && bestDist < 120000) {
        sumSq += (best.y - m.y) ** 2;
        count++;
      }
    }
    return count > 0 ? Math.sqrt(sumSq / count) : null;
  }

  async function loadSparklines() {
    const entityIds = [];
    for (const room of rooms) {
      entityIds.push(`sensor.heating_assistant_${room.slug}_temperature_filtered`);
      entityIds.push(`sensor.heating_assistant_${room.slug}_temperature_measured`);
    }
    const history = await connection.getHistory(entityIds, 6);

    for (const room of rooms) {
      const filteredHist = history[`sensor.heating_assistant_${room.slug}_temperature_filtered`] || [];
      const measuredHist = history[`sensor.heating_assistant_${room.slug}_temperature_measured`] || [];
      const filteredPts = historyToDataPoints(filteredHist);
      const measuredPts = historyToDataPoints(measuredHist);

      const rmse = computeRMSE(filteredPts, measuredPts);
      const rmseEl = grid.querySelector(`[data-room="${room.slug}"]`);
      if (rmseEl) {
        rmseEl.textContent = rmse != null ? `${rmse.toFixed(3)} \u00b0C` : '\u2014';
      }

      const canvas = grid.querySelector(`[data-sparkline="${room.slug}"]`);
      if (canvas && filteredPts.length > 0) {
        const datasets = [
          { data: filteredPts, borderColor: '#4fc3f7', borderWidth: 1.5, spanGaps: true },
          { data: measuredPts, borderColor: '#e57373', borderWidth: 0, pointRadius: 1.5, pointBackgroundColor: '#e57373', showLine: false, spanGaps: true },
        ];
        const chart = await createSparkline(canvas, datasets);
        sparklines.push(chart);
      }
    }
  }

  buildTiles(state);
  loadSparklines();

  return {
    update(newState) {
      for (const room of rooms) {
        const fitEntity = newState[`sensor.heating_assistant_${room.slug}_model_fit_quality`];
        const fitVal = fitEntity ? parseFloat(fitEntity.state) : null;
        const fitInfo = modelFitBadge(fitVal);
        const tile = grid.querySelector(`[data-room="${room.slug}"]`);
        if (tile) {
          const kpiBox = tile.closest('.identification-tile__kpi-row')?.querySelector('.identification-tile__kpi-box');
          if (kpiBox) {
            kpiBox.className = `identification-tile__kpi-box ${fitInfo.class}`;
            const valEl = kpiBox.querySelector('.identification-tile__kpi-value');
            if (valEl) valEl.textContent = fitInfo.label;
          }
        }
      }
    },
    destroy() {
      for (const chart of sparklines) chart.destroy();
      sparklines.length = 0;
    },
  };
}

// ---------------------------------------------------------------------------
// Detail view — per-room identification
// ---------------------------------------------------------------------------

function renderIdentificationDetail(container, roomSlug, rooms, state, connection, hass) {
  const room = rooms.find((r) => r.slug === roomSlug);
  if (!room) {
    container.innerHTML = `<div class="loading">Room not found: ${roomSlug}</div>`;
    return { update() {}, destroy() {} };
  }

  container.innerHTML = '';

  // Back navigation
  const nav = document.createElement('button');
  nav.className = 'nav-back';
  nav.innerHTML = '<span class="nav-back__arrow">\u2190</span> IDENTIFICATION';
  nav.addEventListener('click', () => { window.location.hash = '#identification'; });
  container.appendChild(nav);

  const header = document.createElement('div');
  header.className = 'room-header';
  header.innerHTML = `<h2 class="room-header__title">${room.name}</h2>`;
  container.appendChild(header);

  // --- Section: State Estimation Parameters (global) ---
  const estimSection = document.createElement('div');
  estimSection.className = 'card tuning-section';
  estimSection.innerHTML = `
    <div class="tuning-section__title">State Estimation Parameters</div>
    <p class="tuning-section__desc">Global parameters controlling the Kalman filter and window detection. Changes affect all rooms and take effect immediately.</p>
    <div class="tuning-params-grid">
      <div class="form-group">
        <label class="form-label" for="estim-sigma-w">Process Noise (\u03c3_w)</label>
        <input class="form-input" type="number" id="estim-sigma-w" step="0.001" min="0.000001" max="10" value="${DEFAULTS.sigma_w}">
        <span class="form-hint">K/\u221as</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="estim-sigma-v">Sensor Noise (\u03c3_v)</label>
        <input class="form-input" type="number" id="estim-sigma-v" step="0.001" min="0.000001" max="10" value="${DEFAULTS.sigma_v}">
        <span class="form-hint">K</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="estim-sigma-b">Calibration Drift (\u03c3_b)</label>
        <input class="form-input" type="number" id="estim-sigma-b" step="0.0001" min="0.00000001" max="1" value="${DEFAULTS.sigma_b}">
        <span class="form-hint">K/\u221as</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="estim-debounce">Window Open Debounce</label>
        <input class="form-input" type="number" id="estim-debounce" step="10" min="0" max="3600" value="${DEFAULTS.window_open_debounce}">
        <span class="form-hint">seconds</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="estim-settle">Window Close Settle</label>
        <input class="form-input" type="number" id="estim-settle" step="10" min="0" max="3600" value="${DEFAULTS.window_open_close_settle}">
        <span class="form-hint">seconds</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="estim-q-inflation">Uncertainty Multiplier</label>
        <input class="form-input" type="number" id="estim-q-inflation" step="1" min="1" max="1000" value="${DEFAULTS.window_open_q_inflation}">
        <span class="form-hint">\u00d7 covariance</span>
      </div>
    </div>
    <div class="tuning-actions" style="margin-top:16px">
      <button class="btn btn--primary" id="btn-apply-estim">Apply</button>
      <span class="tuning-actions__status" id="estim-apply-status"></span>
    </div>
  `;
  container.appendChild(estimSection);

  // --- Section: Parameter Identification ---
  const identSection = document.createElement('div');
  identSection.className = 'card tuning-section';
  identSection.innerHTML = `
    <div class="tuning-section__title">Parameter Identification</div>
    <p class="tuning-section__desc">Run maximum-likelihood estimation to identify thermal model parameters from historical data. Results are shown for review — click "Apply Identified Model" to commit them to the active model.</p>
    <div class="tuning-actions">
      <button class="btn btn--accent" id="btn-estimate-ml">Run Identification</button>
      <button class="btn btn--primary" id="btn-apply-identified" disabled>Apply Identified Model</button>
      <span class="tuning-actions__status" id="ident-status"></span>
    </div>
    <div class="tuning-section__title" style="margin-top:20px">Active Model Parameters</div>
    <p class="tuning-section__desc" style="margin-bottom:8px">Currently applied parameters in the active thermal model.</p>
    <div class="grid-kpi" id="ident-kpis"></div>
  `;
  container.appendChild(identSection);

  const identKpiGrid = identSection.querySelector('#ident-kpis');
  const kpiC = createKpiCard({ value: '—', label: 'Thermal Mass', unit: '' });
  const kpiR = createKpiCard({ value: '—', label: 'R External', unit: '' });
  identKpiGrid.appendChild(kpiC);
  identKpiGrid.appendChild(kpiR);

  // --- Section: Model Validation ---
  const validSection = document.createElement('div');
  validSection.className = 'card tuning-section';
  validSection.innerHTML = `
    <div class="tuning-section__title">Model Validation</div>
    <p class="tuning-section__desc">Run simulations to evaluate model fit. EKF reconstruction uses Kalman filtering with uncertainty bounds. Open-loop simulation tests the raw model without state correction. Use <em>Apply to Model</em> to write the current parameter values into the active model.</p>
    <div class="tuning-params-grid">
      <div class="form-group">
        <label class="form-label" for="param-sigma-w">Process Noise (σ_w)</label>
        <input class="form-input" type="number" id="param-sigma-w" step="0.01" min="0.001" value="${DEFAULTS.sigma_w}">
        <span class="form-hint">K/√s</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-sigma-v">Measurement Noise (σ_v)</label>
        <input class="form-input" type="number" id="param-sigma-v" step="0.01" min="0.001" value="${DEFAULTS.sigma_v}">
        <span class="form-hint">°C</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-thermal-mass">Thermal Mass (C)</label>
        <input class="form-input" type="number" id="param-thermal-mass" step="100000" min="10000" value="${DEFAULTS.thermal_mass}">
        <span class="form-hint">J/K</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-r-external">Thermal Resistance (R)</label>
        <input class="form-input" type="number" id="param-r-external" step="0.001" min="0.0001" value="${DEFAULTS.r_external}">
        <span class="form-hint">K/W</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-horizon">Horizon</label>
        <input class="form-input" type="number" id="param-horizon" step="0.5" min="0.5" max="72" value="${DEFAULTS.horizon_hours}">
        <span class="form-hint">hours</span>
      </div>
    </div>
    <div class="tuning-actions" style="margin-top:20px">
      <button class="btn btn--primary" id="btn-sysid">EKF Reconstruction</button>
      <button class="btn btn--secondary" id="btn-open-loop">Open-Loop Simulation</button>
      <button class="btn btn--accent" id="btn-apply-params" title="Apply the current C and R values above to the active model">Apply to Model</button>
      <button class="btn btn--ghost" id="btn-reset-defaults" title="Reset the active model back to configured default parameters">Reset to Defaults</button>
      <span class="tuning-actions__status" id="sim-status"></span>
    </div>
  `;
  container.appendChild(validSection);

  // --- Results: EKF ---
  const ekfResultsSection = document.createElement('div');
  ekfResultsSection.className = 'tuning-section';
  ekfResultsSection.innerHTML = `
    <div class="tuning-section__title">EKF Reconstruction Results</div>
    <div class="grid-kpi" id="ekf-kpis"></div>
  `;
  container.appendChild(ekfResultsSection);

  const ekfKpiGrid = ekfResultsSection.querySelector('#ekf-kpis');
  const kpiEkfRmse = createKpiCard({ value: '—', label: 'RMSE', unit: '' });
  const kpiEkfMae = createKpiCard({ value: '—', label: 'MAE', unit: '' });
  ekfKpiGrid.appendChild(kpiEkfRmse);
  ekfKpiGrid.appendChild(kpiEkfMae);

  const ekfChartEl = document.createElement('div');
  ekfChartEl.className = 'tuning-chart';
  container.appendChild(ekfChartEl);

  const ekfChart = new TimeSeriesChart(ekfChartEl, {
    title: 'EKF RECONSTRUCTION',
    yLabel: '°C',
    height: 260,
  });

  // --- Results: Open-Loop ---
  const olResultsSection = document.createElement('div');
  olResultsSection.className = 'tuning-section';
  olResultsSection.innerHTML = `
    <div class="tuning-section__title">Open-Loop Simulation Results</div>
    <div class="grid-kpi" id="ol-kpis"></div>
  `;
  container.appendChild(olResultsSection);

  const olKpiGrid = olResultsSection.querySelector('#ol-kpis');
  const kpiOlRmse = createKpiCard({ value: '—', label: 'RMSE', unit: '' });
  const kpiOlMae = createKpiCard({ value: '—', label: 'MAE', unit: '' });
  olKpiGrid.appendChild(kpiOlRmse);
  olKpiGrid.appendChild(kpiOlMae);

  const olChartEl = document.createElement('div');
  olChartEl.className = 'tuning-chart';
  container.appendChild(olChartEl);

  const olChart = new TimeSeriesChart(olChartEl, {
    title: 'OPEN-LOOP SIMULATION',
    yLabel: '°C',
    height: 260,
  });

  // --- Section: Parameter History ---
  const historySection = document.createElement('div');
  historySection.className = 'card tuning-section';
  historySection.innerHTML = `
    <div class="tuning-section__title">Applied Model History</div>
    <p class="tuning-section__desc" style="margin-bottom:12px">Up to 10 most recent parameter sets that have been applied to this room. Use "Revert" to restore a previous set.</p>
    <div id="param-history-list"></div>
  `;
  container.appendChild(historySection);

  const historyListEl = historySection.querySelector('#param-history-list');

  function renderParamHistory(st) {
    historyListEl.innerHTML = '';
    const config = st[CONFIG_ENTITY]?.attributes || {};
    const history = config.parameter_history?.[roomSlug] || [];
    if (history.length === 0) {
      historyListEl.innerHTML = '<span class="tuning-section__desc">No history available.</span>';
      return;
    }
    const table = document.createElement('table');
    table.className = 'param-history-table';
    table.innerHTML = `
      <thead>
        <tr>
          <th>#</th>
          <th>Date</th>
          <th>Source</th>
          <th>Thermal Mass</th>
          <th>R External</th>
          <th>RMSE</th>
          <th></th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    const tbody = table.querySelector('tbody');
    for (let i = 0; i < history.length; i++) {
      const entry = history[i];
      const date = entry.estimated_at ? new Date(entry.estimated_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '\u2014';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${i + 1}</td>
        <td>${date}</td>
        <td>${(entry.source || 'manual').toUpperCase()}</td>
        <td>${entry.thermal_mass != null ? formatMass(entry.thermal_mass) : '\u2014'}</td>
        <td>${entry.r_external != null ? formatNumber(entry.r_external, 4) : '\u2014'}</td>
        <td>${entry.rmse != null ? formatNumber(entry.rmse, 3) + ' \u00b0C' : '\u2014'}</td>
        <td><button class="btn btn--ghost btn--sm" data-revert="${i}">Revert</button></td>
      `;
      tbody.appendChild(tr);
    }
    historyListEl.appendChild(table);

    table.querySelectorAll('[data-revert]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const idx = parseInt(btn.dataset.revert, 10);
        btn.disabled = true;
        btn.textContent = '\u2026';
        try {
          await hass.callService('heating_assistant', 'revert_parameters', {
            room_name: roomSlug,
            history_index: idx,
          });
          btn.textContent = '\u2713';
        } catch (err) {
          btn.textContent = 'ERR';
        }
      });
    });
  }

  // --- Wire up interactions ---
  const estimSigmaW = container.querySelector('#estim-sigma-w');
  const estimSigmaV = container.querySelector('#estim-sigma-v');
  const estimSigmaB = container.querySelector('#estim-sigma-b');
  const estimDebounce = container.querySelector('#estim-debounce');
  const estimSettle = container.querySelector('#estim-settle');
  const estimQInflation = container.querySelector('#estim-q-inflation');
  const btnApplyEstim = container.querySelector('#btn-apply-estim');
  const estimApplyStatus = container.querySelector('#estim-apply-status');

  const sigmaWInput = container.querySelector('#param-sigma-w');
  const sigmaVInput = container.querySelector('#param-sigma-v');
  const thermalMassInput = container.querySelector('#param-thermal-mass');
  const rExternalInput = container.querySelector('#param-r-external');
  const horizonInput = container.querySelector('#param-horizon');
  const btnSysid = container.querySelector('#btn-sysid');
  const btnOpenLoop = container.querySelector('#btn-open-loop');
  const btnApplyParams = container.querySelector('#btn-apply-params');
  const btnResetDefaults = container.querySelector('#btn-reset-defaults');
  const btnEstimateMl = container.querySelector('#btn-estimate-ml');
  const btnApplyIdentified = container.querySelector('#btn-apply-identified');
  const identStatusEl = container.querySelector('#ident-status');
  const simStatusEl = container.querySelector('#sim-status');

  let latestState = state;

  function setStatus(el, text, type = '') {
    el.textContent = text;
    el.className = 'tuning-actions__status';
    if (type) el.classList.add(`tuning-actions__status--${type}`);
  }

  function filteredEntityId(slug) {
    return `sensor.heating_assistant_${slug}_temperature_filtered`;
  }

  function sysidEntityId(slug) {
    return `sensor.heating_assistant_${slug}_sysid_simulation`;
  }

  function openLoopEntityId(slug) {
    return `sensor.heating_assistant_${slug}_open_loop_rmse`;
  }

  function populateEstimFromState(st) {
    const config = st[CONFIG_ENTITY]?.attributes || {};
    if (config.sigma_w != null) estimSigmaW.value = config.sigma_w;
    if (config.sigma_v != null) estimSigmaV.value = config.sigma_v;
    if (config.sigma_b != null) estimSigmaB.value = config.sigma_b;
    if (config.window_open_debounce != null) estimDebounce.value = config.window_open_debounce;
    if (config.window_open_close_settle != null) estimSettle.value = config.window_open_close_settle;
    if (config.window_open_q_inflation != null) estimQInflation.value = config.window_open_q_inflation;
  }

  function populateParamsFromState(slug, st) {
    const filtered = st[filteredEntityId(slug)];
    const filteredAttrs = filtered?.attributes;
    if (filteredAttrs) {
      if (filteredAttrs.thermal_mass != null) thermalMassInput.value = filteredAttrs.thermal_mass;
      if (filteredAttrs.r_external != null) rExternalInput.value = filteredAttrs.r_external;
    }
    const sysidEntity = st[sysidEntityId(slug)];
    const sysidAttrs = sysidEntity?.attributes;
    if (sysidAttrs) {
      if (sysidAttrs.sigma_w != null) sigmaWInput.value = sysidAttrs.sigma_w;
      if (sysidAttrs.sigma_v != null) sigmaVInput.value = sysidAttrs.sigma_v;
      if (sysidAttrs.horizon_hours != null) horizonInput.value = sysidAttrs.horizon_hours;
    }
  }

  // Active model parameters — always read from the live temperature_filtered sensor.
  function renderIdentKpis(slug, st) {
    const entity = st[filteredEntityId(slug)];
    const attrs = entity?.attributes || {};
    updateKpiCard(kpiC, { value: attrs.thermal_mass != null ? formatMass(attrs.thermal_mass) : '\u2014' });
    updateKpiCard(kpiR, { value: attrs.r_external != null ? formatNumber(attrs.r_external, 4) + ' K/W' : '\u2014' });
  }

  function renderEkfResults(slug, st) {
    const entity = st[sysidEntityId(slug)];
    const attrs = entity?.attributes || {};
    updateKpiCard(kpiEkfRmse, { value: attrs.rmse != null ? formatNumber(attrs.rmse, 3) + ' \u00b0C' : '\u2014' });
    updateKpiCard(kpiEkfMae, { value: attrs.mae != null ? formatNumber(attrs.mae, 3) + ' \u00b0C' : '\u2014' });
    buildEkfChart(ekfChart, attrs.simulation);
  }

  function renderOlResults(slug, st) {
    const entity = st[openLoopEntityId(slug)];
    const attrs = entity?.attributes || {};
    updateKpiCard(kpiOlRmse, { value: attrs.open_loop_rmse != null ? formatNumber(attrs.open_loop_rmse, 3) + ' \u00b0C' : '\u2014' });
    updateKpiCard(kpiOlMae, { value: attrs.open_loop_mae != null ? formatNumber(attrs.open_loop_mae, 3) + ' \u00b0C' : '\u2014' });
    buildOlChart(olChart, attrs.simulation);
  }

  function renderAll(slug, st) {
    renderIdentKpis(slug, st);
    renderEkfResults(slug, st);
    renderOlResults(slug, st);
  }

  // Apply estimation params
  btnApplyEstim.addEventListener('click', async () => {
    setStatus(estimApplyStatus, 'Saving\u2026', 'running');
    btnApplyEstim.disabled = true;
    try {
      await hass.callService('heating_assistant', 'update_estimation_params', {
        sigma_w: parseFloat(estimSigmaW.value),
        sigma_v: parseFloat(estimSigmaV.value),
        sigma_b: parseFloat(estimSigmaB.value),
        window_open_debounce: parseInt(estimDebounce.value, 10),
        window_open_close_settle: parseInt(estimSettle.value, 10),
        window_open_q_inflation: parseFloat(estimQInflation.value),
      });
      setStatus(estimApplyStatus, 'Applied.', 'success');
    } catch (err) {
      setStatus(estimApplyStatus, 'Error: ' + (err.message || err), 'error');
    }
    btnApplyEstim.disabled = false;
  });

  // Run ML identification
  btnEstimateMl.addEventListener('click', async () => {
    setStatus(identStatusEl, 'Running identification\u2026', 'running');
    btnEstimateMl.disabled = true;
    try {
      await hass.callService('heating_assistant', 'estimate_parameters_ml', {});
      setStatus(identStatusEl, 'Complete \u2014 review results below, then click Apply to use them.', '');
      btnApplyIdentified.disabled = false;
    } catch (err) {
      setStatus(identStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnEstimateMl.disabled = false;
  });

  // Apply identified model — stores ML result into history + makes active
  btnApplyIdentified.addEventListener('click', async () => {
    setStatus(identStatusEl, 'Applying identified model\u2026', 'running');
    btnApplyIdentified.disabled = true;
    try {
      const entity = latestState[filteredEntityId(roomSlug)];
      const attrs = entity?.attributes || {};
      const cVal = parseFloat(thermalMassInput.value) || attrs.thermal_mass;
      const rVal = parseFloat(rExternalInput.value) || attrs.r_external;
      await hass.callService('heating_assistant', 'store_identified_parameters', {
        room_name: roomSlug,
        thermal_mass: cVal,
        r_external: rVal,
        source: 'ml',
      });
      setStatus(identStatusEl, 'Identified model applied and stored in history.', 'success');
    } catch (err) {
      setStatus(identStatusEl, 'Error: ' + (err.message || err), 'error');
    }
  });

  // EKF reconstruction
  btnSysid.addEventListener('click', async () => {
    setStatus(simStatusEl, 'Running EKF reconstruction\u2026', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await hass.callService('heating_assistant', 'run_sysid_simulation', {
        room_name: roomSlug,
        horizon_hours: parseFloat(horizonInput.value),
        sigma_w: parseFloat(sigmaWInput.value),
        sigma_v: parseFloat(sigmaVInput.value),
        [`thermal_mass_${roomSlug}`]: parseFloat(thermalMassInput.value),
        [`r_external_${roomSlug}`]: parseFloat(rExternalInput.value),
      });
      setStatus(simStatusEl, 'Complete.', '');
    } catch (err) {
      setStatus(simStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnSysid.disabled = false;
    btnOpenLoop.disabled = false;
  });

  // Open-loop simulation
  btnOpenLoop.addEventListener('click', async () => {
    setStatus(simStatusEl, 'Running open-loop simulation\u2026', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await hass.callService('heating_assistant', 'run_open_loop_simulation', {
        room_name: roomSlug,
        segment_length: 30,
        horizon_hours: parseFloat(horizonInput.value),
      });
      setStatus(simStatusEl, 'Complete.', '');
    } catch (err) {
      setStatus(simStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnSysid.disabled = false;
    btnOpenLoop.disabled = false;
  });

  // Apply manual params to active model
  btnApplyParams.addEventListener('click', async () => {
    setStatus(simStatusEl, 'Applying parameters to model\u2026', 'running');
    btnApplyParams.disabled = true;
    try {
      await hass.callService('heating_assistant', 'store_identified_parameters', {
        room_name: roomSlug,
        thermal_mass: parseFloat(thermalMassInput.value),
        r_external: parseFloat(rExternalInput.value),
        source: 'manual',
      });
      setStatus(simStatusEl, 'Parameters applied and stored in history.', 'success');
    } catch (err) {
      setStatus(simStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnApplyParams.disabled = false;
  });

  // Reset defaults
  btnResetDefaults.addEventListener('click', async () => {
    setStatus(simStatusEl, 'Resetting to defaults\u2026', 'running');
    btnResetDefaults.disabled = true;
    try {
      await hass.callService('heating_assistant', 'reset_estimated_parameters', {});
      sigmaWInput.value = DEFAULTS.sigma_w;
      sigmaVInput.value = DEFAULTS.sigma_v;
      horizonInput.value = DEFAULTS.horizon_hours;
      setStatus(simStatusEl, 'Model reset to configured defaults.', 'success');
    } catch (err) {
      setStatus(simStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnResetDefaults.disabled = false;
  });

  // Initial population
  populateEstimFromState(state);
  populateParamsFromState(roomSlug, state);
  renderAll(roomSlug, state);
  renderParamHistory(state);

  return {
    update(newState) {
      latestState = newState;
      renderAll(roomSlug, newState);
      renderParamHistory(newState);
    },
    destroy() {
      ekfChart.destroy();
      olChart.destroy();
    },
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatMass(val) {
  const num = parseFloat(val);
  if (isNaN(num)) return '—';
  if (num >= 1e6) return (num / 1e6).toFixed(2) + ' MJ/K';
  if (num >= 1e3) return (num / 1e3).toFixed(0) + ' kJ/K';
  return num.toFixed(0) + ' J/K';
}

function modelFitBadge(val) {
  if (val == null || isNaN(val)) return { label: '\u2014', class: '' };
  if (val > 0.8) return { label: 'GOOD', class: 'fit--good' };
  if (val > 0.5) return { label: 'ACCEPTABLE', class: 'fit--acceptable' };
  return { label: 'POOR', class: 'fit--poor' };
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

  for (const entry of simulation) {
    const t = new Date(entry.time).getTime();
    if (isNaN(t)) continue;
    if (entry.measured != null) measured.push({ x: t, y: entry.measured });
    if (entry.predicted != null) predicted.push({ x: t, y: entry.predicted });
    if (entry.cov_upper != null) covUpper.push({ x: t, y: entry.cov_upper });
    if (entry.cov_lower != null) covLower.push({ x: t, y: entry.cov_lower });
  }

  const datasets = [
    makeDataset('Measured', measured, '#e57373', {
      borderWidth: 0, pointRadius: 2, pointHoverRadius: 4,
      pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
      showLine: false,
    }),
    makeDataset('Predicted', predicted, '#4fc3f7', { borderWidth: 2 }),
    makeDataset('Above 2σ', covUpper, 'rgba(79,195,247,0.25)', {
      borderWidth: 0, pointRadius: 0, fill: false,
    }),
    makeDataset('Below 2σ', covLower, 'rgba(79,195,247,0.25)', {
      borderWidth: 0, pointRadius: 0,
      fill: '-1', backgroundColor: 'rgba(79,195,247,0.10)',
    }),
  ];

  const { yMin, yMax } = computeChartLimits([measured, predicted, covUpper, covLower]);
  chart.render(datasets, { yMin, yMax });
}

function buildOlChart(chart, simulation) {
  if (!simulation || simulation.length === 0) {
    chart.render([], {});
    return;
  }

  const measured = [];
  const predicted = [];

  for (const entry of simulation) {
    const t = new Date(entry.time).getTime();
    if (isNaN(t)) continue;
    if (entry.measured != null) measured.push({ x: t, y: entry.measured });
    if (entry.predicted != null) predicted.push({ x: t, y: entry.predicted });
  }

  const datasets = [
    makeDataset('Measured', measured, '#e57373', {
      borderWidth: 0, pointRadius: 2, pointHoverRadius: 4,
      pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
      showLine: false,
    }),
    makeDataset('Predicted', predicted, '#ffb74d', { borderWidth: 2 }),
  ];

  const { yMin, yMax } = computeChartLimits([measured, predicted]);
  chart.render(datasets, { yMin, yMax });
}

function computeChartLimits(dataSets) {
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const points of dataSets) {
    for (const p of points) {
      if (p.y < yMin) yMin = p.y;
      if (p.y > yMax) yMax = p.y;
    }
  }
  if (!isFinite(yMin) || !isFinite(yMax)) return { yMin: undefined, yMax: undefined };
  const margin = (yMax - yMin) * 0.05 || 0.5;
  return { yMin: yMin - margin, yMax: yMax + margin };
}
