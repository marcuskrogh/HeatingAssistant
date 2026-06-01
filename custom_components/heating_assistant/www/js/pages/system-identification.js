import { TimeSeriesChart, makeDataset, loadChartJs } from '../components/time-series-chart.js';
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
      tile.className = 'card identification-tile';
      tile.style.cursor = 'pointer';

      const fitEntity = st[`sensor.heating_assistant_${room.slug}_model_fit_quality`];
      const fitVal = fitEntity ? parseFloat(fitEntity.state) : null;
      const fitInfo = modelFitBadge(fitVal);

      const sysidEntity = st[`sensor.heating_assistant_${room.slug}_sysid_simulation`];
      const lastIdent = sysidEntity?.attributes?.thermal_mass != null ? 'Identified' : 'Not yet run';

      tile.innerHTML = `
        <div class="identification-tile__name">${room.name}</div>
        <div class="identification-tile__row">
          <span class="fit-badge ${fitInfo.class}">${fitInfo.label}</span>
          <span class="identification-tile__status">${lastIdent}</span>
        </div>
      `;
      tile.addEventListener('click', () => {
        window.location.hash = `#identification/${room.slug}`;
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
    <p class="tuning-section__desc">Run maximum-likelihood estimation to identify thermal model parameters from historical data. Results are applied immediately.</p>
    <div class="tuning-actions">
      <button class="btn btn--accent" id="btn-estimate-ml">Run Identification</button>
      <span class="tuning-actions__status" id="ident-status"></span>
    </div>
    <div class="tuning-section__title" style="margin-top:20px">Identified Parameters</div>
    <div class="grid-kpi" id="ident-kpis"></div>
  `;
  container.appendChild(identSection);

  const identKpiGrid = identSection.querySelector('#ident-kpis');
  const kpiC = createKpiCard({ value: '\u2014', label: 'Thermal Mass', unit: '' });
  const kpiR = createKpiCard({ value: '\u2014', label: 'R External', unit: '' });
  const kpiSigW = createKpiCard({ value: '\u2014', label: '\u03c3_w', unit: '' });
  const kpiSigV = createKpiCard({ value: '\u2014', label: '\u03c3_v', unit: '' });
  identKpiGrid.appendChild(kpiC);
  identKpiGrid.appendChild(kpiR);
  identKpiGrid.appendChild(kpiSigW);
  identKpiGrid.appendChild(kpiSigV);

  // --- Section: Model Validation ---
  const validSection = document.createElement('div');
  validSection.className = 'card tuning-section';
  validSection.innerHTML = `
    <div class="tuning-section__title">Model Validation</div>
    <p class="tuning-section__desc">Run simulations to evaluate model fit. EKF reconstruction uses Kalman filtering with uncertainty bounds. Open-loop simulation tests the raw model without state correction.</p>
    <div class="tuning-params-grid">
      <div class="form-group">
        <label class="form-label" for="param-sigma-w">Process Noise (\u03c3_w)</label>
        <input class="form-input" type="number" id="param-sigma-w" step="0.01" min="0.001" value="${DEFAULTS.sigma_w}">
        <span class="form-hint">K/\u221as</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-sigma-v">Measurement Noise (\u03c3_v)</label>
        <input class="form-input" type="number" id="param-sigma-v" step="0.01" min="0.001" value="${DEFAULTS.sigma_v}">
        <span class="form-hint">\u00b0C</span>
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
      <button class="btn btn--ghost" id="btn-reset-defaults" title="Reset all parameters to their default values">Reset to Defaults</button>
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
  const kpiEkfRmse = createKpiCard({ value: '\u2014', label: 'RMSE', unit: '' });
  const kpiEkfMae = createKpiCard({ value: '\u2014', label: 'MAE', unit: '' });
  ekfKpiGrid.appendChild(kpiEkfRmse);
  ekfKpiGrid.appendChild(kpiEkfMae);

  const ekfChartEl = document.createElement('div');
  ekfChartEl.className = 'tuning-chart';
  container.appendChild(ekfChartEl);

  const ekfChart = new TimeSeriesChart(ekfChartEl, {
    title: 'EKF RECONSTRUCTION',
    yLabel: '\u00b0C',
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
  const kpiOlRmse = createKpiCard({ value: '\u2014', label: 'RMSE', unit: '' });
  const kpiOlMae = createKpiCard({ value: '\u2014', label: 'MAE', unit: '' });
  olKpiGrid.appendChild(kpiOlRmse);
  olKpiGrid.appendChild(kpiOlMae);

  const olChartEl = document.createElement('div');
  olChartEl.className = 'tuning-chart';
  container.appendChild(olChartEl);

  const olChart = new TimeSeriesChart(olChartEl, {
    title: 'OPEN-LOOP SIMULATION',
    yLabel: '\u00b0C',
    height: 260,
  });

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
  const btnResetDefaults = container.querySelector('#btn-reset-defaults');
  const btnEstimateMl = container.querySelector('#btn-estimate-ml');
  const identStatusEl = container.querySelector('#ident-status');
  const simStatusEl = container.querySelector('#sim-status');

  let latestState = state;

  function setStatus(el, text, type = '') {
    el.textContent = text;
    el.className = 'tuning-actions__status';
    if (type) el.classList.add(`tuning-actions__status--${type}`);
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
    const entity = st[sysidEntityId(slug)];
    const attrs = entity?.attributes;
    if (attrs) {
      if (attrs.sigma_w != null) sigmaWInput.value = attrs.sigma_w;
      if (attrs.sigma_v != null) sigmaVInput.value = attrs.sigma_v;
      if (attrs.thermal_mass != null) thermalMassInput.value = attrs.thermal_mass;
      if (attrs.r_external != null) rExternalInput.value = attrs.r_external;
      if (attrs.horizon_hours != null) horizonInput.value = attrs.horizon_hours;
    }
  }

  function renderIdentKpis(slug, st) {
    const entity = st[sysidEntityId(slug)];
    const attrs = entity?.attributes || {};
    updateKpiCard(kpiC, { value: attrs.thermal_mass != null ? formatMass(attrs.thermal_mass) : '\u2014' });
    updateKpiCard(kpiR, { value: attrs.r_external != null ? formatNumber(attrs.r_external, 4) + ' K/W' : '\u2014' });
    updateKpiCard(kpiSigW, { value: attrs.sigma_w != null ? formatNumber(attrs.sigma_w, 3) + ' K/\u221as' : '\u2014' });
    updateKpiCard(kpiSigV, { value: attrs.sigma_v != null ? formatNumber(attrs.sigma_v, 3) + ' \u00b0C' : '\u2014' });
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
      await hass.callService('heating_assistant', 'estimate_parameters_ml', {
        apply_parameters: true,
      });
      setStatus(identStatusEl, 'Complete \u2014 parameters applied.', '');
    } catch (err) {
      setStatus(identStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnEstimateMl.disabled = false;
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

  // Reset defaults
  btnResetDefaults.addEventListener('click', () => {
    sigmaWInput.value = DEFAULTS.sigma_w;
    sigmaVInput.value = DEFAULTS.sigma_v;
    thermalMassInput.value = DEFAULTS.thermal_mass;
    rExternalInput.value = DEFAULTS.r_external;
    horizonInput.value = DEFAULTS.horizon_hours;
    setStatus(simStatusEl, 'Parameters reset to defaults.', '');
  });

  // Initial population
  populateEstimFromState(state);
  populateParamsFromState(roomSlug, state);
  renderAll(roomSlug, state);

  return {
    update(newState) {
      latestState = newState;
      renderAll(roomSlug, newState);
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
  if (isNaN(num)) return '\u2014';
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
    makeDataset('Above 2\u03c3', covUpper, 'rgba(79,195,247,0.25)', {
      borderWidth: 0, pointRadius: 0, fill: false,
    }),
    makeDataset('Below 2\u03c3', covLower, 'rgba(79,195,247,0.25)', {
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
