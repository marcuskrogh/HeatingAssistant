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

  const controlBar = document.createElement('div');
  controlBar.className = 'tuning-controls__row';
  controlBar.innerHTML = `
    <label class="form-label" for="tuning-room-select" style="margin-bottom:0">ROOM</label>
    <select class="form-select" id="tuning-room-select">
      ${rooms.map((r) => `<option value="${r.slug}">${r.name}</option>`).join('')}
    </select>
  `;
  container.appendChild(controlBar);

  const paramsSection = document.createElement('div');
  paramsSection.className = 'card tuning-section';
  paramsSection.innerHTML = `
    <div class="tuning-section__title">MODEL PARAMETERS</div>
    <div class="tuning-params-grid">
      <div class="form-group">
        <label class="form-label" for="param-sigma-w">\u03c3_w (process noise)</label>
        <input class="form-input" type="number" id="param-sigma-w" step="0.01" min="0.001" value="${DEFAULTS.sigma_w}">
        <span class="form-hint">K/\u221as</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-sigma-v">\u03c3_v (measurement noise)</label>
        <input class="form-input" type="number" id="param-sigma-v" step="0.01" min="0.001" value="${DEFAULTS.sigma_v}">
        <span class="form-hint">\u00b0C</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-thermal-mass">Thermal Mass</label>
        <input class="form-input" type="number" id="param-thermal-mass" step="100000" min="100000" value="${DEFAULTS.thermal_mass}">
        <span class="form-hint">J/K</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-r-external">R_external</label>
        <input class="form-input" type="number" id="param-r-external" step="0.001" min="0.001" value="${DEFAULTS.r_external}">
        <span class="form-hint">K/W</span>
      </div>
      <div class="form-group">
        <label class="form-label" for="param-horizon">Horizon</label>
        <input class="form-input" type="number" id="param-horizon" step="0.5" min="0.5" max="72" value="${DEFAULTS.horizon_hours}">
        <span class="form-hint">hours</span>
      </div>
    </div>
  `;
  container.appendChild(paramsSection);

  const actionsSection = document.createElement('div');
  actionsSection.className = 'tuning-actions';
  actionsSection.innerHTML = `
    <button class="btn btn--primary" id="btn-sysid">RUN EKF RECONSTRUCTION</button>
    <button class="btn btn--secondary" id="btn-open-loop">RUN OPEN-LOOP SIM</button>
    <button class="btn btn--accent" id="btn-estimate-ml">ESTIMATE PARAMETERS (ML)</button>
    <span class="tuning-actions__status" id="action-status"></span>
  `;
  container.appendChild(actionsSection);

  const resultsSection = document.createElement('div');
  resultsSection.className = 'tuning-results';
  const kpiGrid = document.createElement('div');
  kpiGrid.className = 'grid-kpi';
  resultsSection.appendChild(kpiGrid);
  container.appendChild(resultsSection);

  const kpiRmse = createKpiCard({ value: '\u2014', label: 'RMSE', unit: '' });
  const kpiMae = createKpiCard({ value: '\u2014', label: 'MAE', unit: '' });
  const kpiC = createKpiCard({ value: '\u2014', label: 'Thermal Mass', unit: '' });
  const kpiR = createKpiCard({ value: '\u2014', label: 'R_external', unit: '' });
  kpiGrid.appendChild(kpiRmse);
  kpiGrid.appendChild(kpiMae);
  kpiGrid.appendChild(kpiC);
  kpiGrid.appendChild(kpiR);

  const chartEl = document.createElement('div');
  chartEl.className = 'tuning-chart';
  container.appendChild(chartEl);

  const sysidChart = new TimeSeriesChart(chartEl, {
    title: 'STATE ESTIMATION FIT',
    yLabel: '\u00b0C',
    height: 280,
  });

  const roomSelect = container.querySelector('#tuning-room-select');
  const sigmaWInput = container.querySelector('#param-sigma-w');
  const sigmaVInput = container.querySelector('#param-sigma-v');
  const thermalMassInput = container.querySelector('#param-thermal-mass');
  const rExternalInput = container.querySelector('#param-r-external');
  const horizonInput = container.querySelector('#param-horizon');
  const btnSysid = container.querySelector('#btn-sysid');
  const btnOpenLoop = container.querySelector('#btn-open-loop');
  const btnEstimateMl = container.querySelector('#btn-estimate-ml');
  const statusEl = container.querySelector('#action-status');

  let currentRoom = rooms[0]?.slug || '';
  let latestState = state;

  function setStatus(text, type = '') {
    statusEl.textContent = text;
    statusEl.className = 'tuning-actions__status';
    if (type === 'running') statusEl.classList.add('tuning-actions__status--running');
    if (type === 'error') statusEl.classList.add('tuning-actions__status--error');
  }

  function sysidEntityId(slug) {
    return `sensor.heating_assistant_${slug}_sysid_simulation`;
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

  function renderResults(slug, st) {
    const entity = st[sysidEntityId(slug)];
    const attrs = entity?.attributes || {};
    const rmse = attrs.rmse;
    const mae = attrs.mae;
    const tm = attrs.thermal_mass;
    const re = attrs.r_external;

    updateKpiCard(kpiRmse, { value: rmse != null ? formatNumber(rmse, 3) + ' \u00b0C' : '\u2014' });
    updateKpiCard(kpiMae, { value: mae != null ? formatNumber(mae, 3) + ' \u00b0C' : '\u2014' });
    updateKpiCard(kpiC, { value: tm != null ? formatMass(tm) : '\u2014' });
    updateKpiCard(kpiR, { value: re != null ? formatNumber(re, 4) + ' K/W' : '\u2014' });

    buildSysidChart(sysidChart, attrs.simulation);
  }

  function formatMass(val) {
    const num = parseFloat(val);
    if (isNaN(num)) return '\u2014';
    if (num >= 1e6) return (num / 1e6).toFixed(2) + ' MJ/K';
    if (num >= 1e3) return (num / 1e3).toFixed(0) + ' kJ/K';
    return num.toFixed(0) + ' J/K';
  }

  roomSelect.addEventListener('change', (e) => {
    currentRoom = e.target.value;
    populateParamsFromState(currentRoom, latestState);
    renderResults(currentRoom, latestState);
  });

  btnSysid.addEventListener('click', async () => {
    setStatus('Running EKF reconstruction\u2026', 'running');
    disableButtons(true);
    try {
      await hass.callService('heating_assistant', 'run_sysid_simulation', {
        room_name: currentRoom,
        horizon_hours: parseFloat(horizonInput.value),
        sigma_w: parseFloat(sigmaWInput.value),
        sigma_v: parseFloat(sigmaVInput.value),
        [`thermal_mass_${currentRoom}`]: parseFloat(thermalMassInput.value),
        [`r_external_${currentRoom}`]: parseFloat(rExternalInput.value),
      });
      setStatus('Complete \u2014 waiting for state update\u2026', 'running');
    } catch (err) {
      setStatus('Error: ' + (err.message || err), 'error');
    }
    disableButtons(false);
  });

  btnOpenLoop.addEventListener('click', async () => {
    setStatus('Running open-loop simulation\u2026', 'running');
    disableButtons(true);
    try {
      await hass.callService('heating_assistant', 'run_open_loop_simulation', {
        room_name: currentRoom,
        segment_length: 30,
      });
      setStatus('Complete \u2014 waiting for state update\u2026', 'running');
    } catch (err) {
      setStatus('Error: ' + (err.message || err), 'error');
    }
    disableButtons(false);
  });

  btnEstimateMl.addEventListener('click', async () => {
    setStatus('Running ML parameter estimation\u2026', 'running');
    disableButtons(true);
    try {
      await hass.callService('heating_assistant', 'estimate_parameters_ml', {
        apply_parameters: true,
      });
      setStatus('ML estimation complete.', '');
    } catch (err) {
      setStatus('Error: ' + (err.message || err), 'error');
    }
    disableButtons(false);
  });

  function disableButtons(disabled) {
    btnSysid.disabled = disabled;
    btnOpenLoop.disabled = disabled;
    btnEstimateMl.disabled = disabled;
  }

  populateParamsFromState(currentRoom, state);
  renderResults(currentRoom, state);

  return {
    update(newState) {
      latestState = newState;
      renderResults(currentRoom, newState);
    },
    destroy() {
      sysidChart.destroy();
    },
  };
}

function buildSysidChart(chart, simulation) {
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
      borderWidth: 0, pointRadius: 2.5, pointHoverRadius: 4,
      pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
      showLine: false,
    }),
    makeDataset('Predicted', predicted, '#4fc3f7', { borderWidth: 2 }),
    makeDataset('Above 2\u03c3', covUpper, 'rgba(79,195,247,0.3)', {
      borderWidth: 1, pointRadius: 0, fill: false,
    }),
    makeDataset('Below 2\u03c3', covLower, 'rgba(79,195,247,0.3)', {
      borderWidth: 1, pointRadius: 0,
      fill: '-1', backgroundColor: 'rgba(79,195,247,0.12)',
    }),
  ];

  const allY = [...measured, ...predicted, ...covUpper, ...covLower];
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const p of allY) {
    if (p.y < yMin) yMin = p.y;
    if (p.y > yMax) yMax = p.y;
  }
  const margin = (yMax - yMin) * 0.05 || 0.5;

  chart.render(datasets, { yMin: yMin - margin, yMax: yMax + margin });
}
