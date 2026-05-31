import { TimeSeriesChart, makeDataset, loadChartJs } from '../components/time-series-chart.js';
import { createKpiCard, updateKpiCard } from '../components/kpi-card.js';
import { entityValue, formatNumber, systemEntity } from '../utils.js';

const DEFAULTS = {
  sigma_w: 0.1,
  sigma_v: 0.5,
  thermal_mass: 5000000,
  r_external: 0.05,
  horizon_hours: 6,
};

export function renderTuning(container, rooms, state, connection, hass) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'SYSTEM TUNING';
  container.appendChild(header);

  // --- Room selector ---
  const controlBar = document.createElement('div');
  controlBar.className = 'tuning-controls__row';
  controlBar.innerHTML = `
    <label class="form-label" for="tuning-room-select" style="margin-bottom:0">Room</label>
    <select class="form-select" id="tuning-room-select">
      ${rooms.map((r) => `<option value="${r.slug}">${r.name}</option>`).join('')}
    </select>
  `;
  container.appendChild(controlBar);

  // --- Section 1: Parameter Identification ---
  const identSection = document.createElement('div');
  identSection.className = 'card tuning-section';
  identSection.innerHTML = `
    <div class="tuning-section__title">Parameter Identification</div>
    <p class="tuning-section__desc">Run maximum-likelihood estimation to identify thermal model parameters from historical data. Results are applied immediately.</p>
    <div class="tuning-actions">
      <button class="btn btn--accent" id="btn-estimate-ml">Run Identification</button>
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

  // --- Section 2: Model Validation ---
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

  // --- Wire up interactions ---
  const roomSelect = container.querySelector('#tuning-room-select');
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
  const identStatusEl = container.querySelector('#ident-status');
  const simStatusEl = container.querySelector('#sim-status');

  let currentRoom = rooms[0]?.slug || '';
  let latestState = state;

  function setIdentStatus(text, type = '') {
    identStatusEl.textContent = text;
    identStatusEl.className = 'tuning-actions__status';
    if (type) identStatusEl.classList.add(`tuning-actions__status--${type}`);
  }

  function setSimStatus(text, type = '') {
    simStatusEl.textContent = text;
    simStatusEl.className = 'tuning-actions__status';
    if (type) simStatusEl.classList.add(`tuning-actions__status--${type}`);
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

  // Populate form fields for simulation: thermal_mass/r_external from active
  // model (temperature_filtered sensor), noise params from last sysid run.
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

    updateKpiCard(kpiC, { value: attrs.thermal_mass != null ? formatMass(attrs.thermal_mass) : '—' });
    updateKpiCard(kpiR, { value: attrs.r_external != null ? formatNumber(attrs.r_external, 4) + ' K/W' : '—' });
  }

  function renderEkfResults(slug, st) {
    const entity = st[sysidEntityId(slug)];
    const attrs = entity?.attributes || {};

    updateKpiCard(kpiEkfRmse, { value: attrs.rmse != null ? formatNumber(attrs.rmse, 3) + ' °C' : '—' });
    updateKpiCard(kpiEkfMae, { value: attrs.mae != null ? formatNumber(attrs.mae, 3) + ' °C' : '—' });

    buildEkfChart(ekfChart, attrs.simulation);
  }

  function renderOlResults(slug, st) {
    const entity = st[openLoopEntityId(slug)];
    const attrs = entity?.attributes || {};

    updateKpiCard(kpiOlRmse, { value: attrs.open_loop_rmse != null ? formatNumber(attrs.open_loop_rmse, 3) + ' °C' : '—' });
    updateKpiCard(kpiOlMae, { value: attrs.open_loop_mae != null ? formatNumber(attrs.open_loop_mae, 3) + ' °C' : '—' });

    buildOlChart(olChart, attrs.simulation);
  }

  function renderAll(slug, st) {
    renderIdentKpis(slug, st);
    renderEkfResults(slug, st);
    renderOlResults(slug, st);
  }

  roomSelect.addEventListener('change', (e) => {
    currentRoom = e.target.value;
    populateParamsFromState(currentRoom, latestState);
    renderAll(currentRoom, latestState);
  });

  btnEstimateMl.addEventListener('click', async () => {
    setIdentStatus('Running identification…', 'running');
    btnEstimateMl.disabled = true;
    try {
      await hass.callService('heating_assistant', 'estimate_parameters_ml', {
        apply_parameters: true,
      });
      setIdentStatus('Complete — parameters applied.', '');
    } catch (err) {
      setIdentStatus('Error: ' + (err.message || err), 'error');
    }
    btnEstimateMl.disabled = false;
  });

  btnSysid.addEventListener('click', async () => {
    setSimStatus('Running EKF reconstruction…', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await hass.callService('heating_assistant', 'run_sysid_simulation', {
        room_name: currentRoom,
        horizon_hours: parseFloat(horizonInput.value),
        sigma_w: parseFloat(sigmaWInput.value),
        sigma_v: parseFloat(sigmaVInput.value),
        [`thermal_mass_${currentRoom}`]: parseFloat(thermalMassInput.value),
        [`r_external_${currentRoom}`]: parseFloat(rExternalInput.value),
      });
      setSimStatus('Complete.', '');
    } catch (err) {
      setSimStatus('Error: ' + (err.message || err), 'error');
    }
    btnSysid.disabled = false;
    btnOpenLoop.disabled = false;
  });

  btnOpenLoop.addEventListener('click', async () => {
    setSimStatus('Running open-loop simulation…', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await hass.callService('heating_assistant', 'run_open_loop_simulation', {
        room_name: currentRoom,
        segment_length: 30,
        horizon_hours: parseFloat(horizonInput.value),
      });
      setSimStatus('Complete.', '');
    } catch (err) {
      setSimStatus('Error: ' + (err.message || err), 'error');
    }
    btnSysid.disabled = false;
    btnOpenLoop.disabled = false;
  });

  btnApplyParams.addEventListener('click', async () => {
    setSimStatus('Applying parameters to model…', 'running');
    btnApplyParams.disabled = true;
    try {
      await hass.callService('heating_assistant', 'apply_manual_parameters', {
        room_name: currentRoom,
        thermal_mass: parseFloat(thermalMassInput.value),
        r_external: parseFloat(rExternalInput.value),
      });
      setSimStatus('Parameters applied to active model.', '');
    } catch (err) {
      setSimStatus('Error: ' + (err.message || err), 'error');
    }
    btnApplyParams.disabled = false;
  });

  btnResetDefaults.addEventListener('click', async () => {
    setSimStatus('Resetting to defaults…', 'running');
    btnResetDefaults.disabled = true;
    try {
      await hass.callService('heating_assistant', 'reset_estimated_parameters', {});
      sigmaWInput.value = DEFAULTS.sigma_w;
      sigmaVInput.value = DEFAULTS.sigma_v;
      horizonInput.value = DEFAULTS.horizon_hours;
      setSimStatus('Model reset to configured defaults.', '');
    } catch (err) {
      setSimStatus('Error: ' + (err.message || err), 'error');
    }
    btnResetDefaults.disabled = false;
  });

  populateParamsFromState(currentRoom, state);
  renderAll(currentRoom, state);

  return {
    update(newState) {
      latestState = newState;
      renderIdentKpis(currentRoom, newState);
      renderEkfResults(currentRoom, newState);
      renderOlResults(currentRoom, newState);
    },
    destroy() {
      ekfChart.destroy();
      olChart.destroy();
    },
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
