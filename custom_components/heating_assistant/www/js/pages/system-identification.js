import { TimeSeriesChart, makeDataset, loadChartJs, createSparkline, historyToDataPoints } from '../components/time-series-chart.js';
import { createKpiCard, updateKpiCard } from '../components/kpi-card.js';
import { entityValue, formatNumber, systemEntity } from '../utils.js';

// Default parameter values — must match backend DEFAULT_* constants in const.py
const DEFAULTS = {
  sigma_w: 0.1,
  sigma_v: 0.5,
  sigma_b: 0.002,
  thermal_mass: 5000000,
  r_external: 0.05,
  horizon_hours: 6,
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
            <span class="identification-tile__kpi-value" data-room="${room.slug}">—</span>
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
        rmseEl.textContent = rmse != null ? `${rmse.toFixed(3)} °C` : '—';
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
  nav.innerHTML = '<span class="nav-back__arrow">←</span> IDENTIFICATION';
  nav.addEventListener('click', () => { window.location.hash = '#identification'; });
  container.appendChild(nav);

  const header = document.createElement('div');
  header.className = 'room-header';
  header.innerHTML = `<h2 class="room-header__title">${room.name}</h2>`;
  container.appendChild(header);

  // -----------------------------------------------------------------------
  // Section 1: Action buttons (top of page)
  // -----------------------------------------------------------------------
  const actionsCard = document.createElement('div');
  actionsCard.className = 'card tuning-section';
  actionsCard.innerHTML = `
    <div class="tuning-section__title">Actions</div>
    <p class="tuning-section__desc">
      <strong>Run Auto-Identification</strong> runs ML estimation (dry run) and populates the fields below with the identified values — no changes are committed yet.
      <strong>Apply Parameters</strong> writes the current field values to the active model.
      <strong>Reset to Defaults</strong> fills all fields with factory defaults without committing.
    </p>
    <div class="tuning-actions">
      <button class="btn btn--accent tuning-actions__btn" id="btn-auto-identify">Run Auto-Identification</button>
      <button class="btn btn--primary tuning-actions__btn" id="btn-apply-params">Apply Parameters</button>
      <button class="btn btn--ghost tuning-actions__btn" id="btn-reset-defaults">Reset to Defaults</button>
      <span class="tuning-actions__status" id="action-status"></span>
    </div>
  `;
  container.appendChild(actionsCard);

  // -----------------------------------------------------------------------
  // Section 2: Parameter fields (organised by category)
  // -----------------------------------------------------------------------
  const paramsCard = document.createElement('div');
  paramsCard.className = 'card tuning-section';
  paramsCard.innerHTML = `
    <div class="tuning-section__title">Identification Parameters</div>

    <div class="params-subsection">
      <div class="params-subsection__title">Model Parameters</div>
      <div class="tuning-params-grid">
        <div class="form-group">
          <label class="form-label" for="param-thermal-mass">Thermal Mass (C)</label>
          <input class="form-input" type="number" id="param-thermal-mass"
            step="100000" min="10000" value="${DEFAULTS.thermal_mass}">
          <span class="form-hint">J/K &mdash; thermal storage capacity of the room</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="param-r-external">Thermal Resistance (R<sub>ext</sub>)</label>
          <input class="form-input" type="number" id="param-r-external"
            step="0.001" min="0.0001" value="${DEFAULTS.r_external}">
          <span class="form-hint">K/W &mdash; envelope resistance to outdoor</span>
        </div>
      </div>
    </div>

    <div class="params-subsection">
      <div class="params-subsection__title">Stochastic Parameters</div>
      <div class="tuning-params-grid">
        <div class="form-group">
          <label class="form-label" for="param-sigma-w">Process Noise (&sigma;<sub>w</sub>)</label>
          <input class="form-input" type="number" id="param-sigma-w"
            step="0.001" min="0.000001" max="10" value="${DEFAULTS.sigma_w}">
          <span class="form-hint">K/&radic;s &mdash; model adaptation rate</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="param-sigma-v">Sensor Noise (&sigma;<sub>v</sub>)</label>
          <input class="form-input" type="number" id="param-sigma-v"
            step="0.001" min="0.000001" max="10" value="${DEFAULTS.sigma_v}">
          <span class="form-hint">K &mdash; expected temperature sensor noise</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="param-sigma-b">Calibration Drift (&sigma;<sub>b</sub>)</label>
          <input class="form-input" type="number" id="param-sigma-b"
            step="0.0001" min="0.00000001" max="1" value="${DEFAULTS.sigma_b}">
          <span class="form-hint">K/&radic;s &mdash; allowed sensor drift rate</span>
        </div>
      </div>
    </div>

    <div class="params-subsection params-subsection--last">
      <div class="params-subsection__title">Identification Window</div>
      <div class="tuning-params-grid">
        <div class="form-group">
          <label class="form-label" for="param-horizon">Horizon</label>
          <input class="form-input" type="number" id="param-horizon"
            step="0.5" min="0.5" max="72" value="${DEFAULTS.horizon_hours}">
          <span class="form-hint">hours &mdash; history window used for simulation and validation</span>
        </div>
      </div>
    </div>
  `;
  container.appendChild(paramsCard);

  // -----------------------------------------------------------------------
  // Section 3: Model validation (clearly separated from parameters)
  // -----------------------------------------------------------------------
  const divider = document.createElement('hr');
  divider.className = 'section-divider';
  container.appendChild(divider);

  const validationIntro = document.createElement('div');
  validationIntro.className = 'card tuning-section';
  validationIntro.innerHTML = `
    <div class="tuning-section__title">Model Validation</div>
    <p class="tuning-section__desc">
      Run simulations using the parameter values above to evaluate model fit over the
      selected horizon. <strong>EKF Reconstruction</strong> uses a Kalman filter with
      &plusmn;2&sigma; uncertainty bounds. <strong>Open-Loop Simulation</strong> tests the
      raw thermal model without state correction. Each section shows the temperature fit
      together with the heating input and disturbances that drove it over the same horizon.
    </p>
  `;
  container.appendChild(validationIntro);

  // Each validation section is laid out top-to-bottom as:
  //   button → fit KPIs → temperature plot → heating-input plot → disturbance plot
  // so the action sits directly above the plots it produces.
  function buildValidationSection({ title, btnId, btnClass, btnLabel, statusId, kpiId }) {
    const section = document.createElement('div');
    section.className = 'card tuning-section';
    section.innerHTML = `
      <div class="tuning-section__title">${title}</div>
      <div class="tuning-actions">
        <button class="btn ${btnClass}" id="${btnId}">${btnLabel}</button>
        <span class="tuning-actions__status" id="${statusId}"></span>
      </div>
      <div class="grid-kpi" id="${kpiId}"></div>
      <div class="tuning-chart" data-chart="temp"></div>
      <div class="tuning-chart" data-chart="inputs"></div>
      <div class="tuning-chart" data-chart="disturb"></div>
    `;
    container.appendChild(section);
    return section;
  }

  // ---- EKF Reconstruction section ----
  const ekfSection = buildValidationSection({
    title: 'EKF Reconstruction',
    btnId: 'btn-sysid', btnClass: 'btn--primary', btnLabel: 'Run EKF Reconstruction',
    statusId: 'ekf-status', kpiId: 'ekf-kpis',
  });

  const ekfKpiGrid = ekfSection.querySelector('#ekf-kpis');
  const kpiEkfRmse = createKpiCard({ value: '—', label: 'RMSE', unit: '' });
  const kpiEkfMae = createKpiCard({ value: '—', label: 'MAE', unit: '' });
  ekfKpiGrid.appendChild(kpiEkfRmse);
  ekfKpiGrid.appendChild(kpiEkfMae);

  const ekfChart = new TimeSeriesChart(ekfSection.querySelector('[data-chart="temp"]'), {
    title: 'EKF RECONSTRUCTION', yLabel: '°C', height: 260,
  });
  const ekfInputsChart = new TimeSeriesChart(ekfSection.querySelector('[data-chart="inputs"]'), {
    title: 'HEATING INPUT', yLabel: 'W', height: 180,
  });
  const ekfDisturbChart = new TimeSeriesChart(ekfSection.querySelector('[data-chart="disturb"]'), {
    title: 'DISTURBANCES', yLabel: '°C', y2: true, y2Label: 'W', height: 180,
  });

  // ---- Open-Loop Simulation section ----
  const olSection = buildValidationSection({
    title: 'Open-Loop Simulation',
    btnId: 'btn-open-loop', btnClass: 'btn--primary', btnLabel: 'Run Open-Loop Simulation',
    statusId: 'ol-status', kpiId: 'ol-kpis',
  });

  const olKpiGrid = olSection.querySelector('#ol-kpis');
  const kpiOlRmse = createKpiCard({ value: '—', label: 'RMSE', unit: '' });
  const kpiOlMae = createKpiCard({ value: '—', label: 'MAE', unit: '' });
  olKpiGrid.appendChild(kpiOlRmse);
  olKpiGrid.appendChild(kpiOlMae);

  const olChart = new TimeSeriesChart(olSection.querySelector('[data-chart="temp"]'), {
    title: 'OPEN-LOOP SIMULATION', yLabel: '°C', height: 260,
  });
  const olInputsChart = new TimeSeriesChart(olSection.querySelector('[data-chart="inputs"]'), {
    title: 'HEATING INPUT', yLabel: 'W', height: 180,
  });
  const olDisturbChart = new TimeSeriesChart(olSection.querySelector('[data-chart="disturb"]'), {
    title: 'DISTURBANCES', yLabel: '°C', y2: true, y2Label: 'W', height: 180,
  });

  // -----------------------------------------------------------------------
  // Section 4: Applied model history
  // -----------------------------------------------------------------------
  const historySection = document.createElement('div');
  historySection.className = 'card tuning-section';
  historySection.innerHTML = `
    <div class="tuning-section__title">Applied Model History</div>
    <p class="tuning-section__desc" style="margin-bottom:12px">Up to 10 most recent parameter sets applied to this room. Click <strong>Revert</strong> to restore a previous set.</p>
    <div id="param-history-list"></div>
  `;
  container.appendChild(historySection);

  const historyListEl = historySection.querySelector('#param-history-list');

  // -----------------------------------------------------------------------
  // Input references
  // -----------------------------------------------------------------------
  const thermalMassInput = container.querySelector('#param-thermal-mass');
  const rExternalInput = container.querySelector('#param-r-external');
  const sigmaWInput = container.querySelector('#param-sigma-w');
  const sigmaVInput = container.querySelector('#param-sigma-v');
  const sigmaBInput = container.querySelector('#param-sigma-b');
  const horizonInput = container.querySelector('#param-horizon');
  const btnAutoIdentify = container.querySelector('#btn-auto-identify');
  const btnApplyParams = container.querySelector('#btn-apply-params');
  const btnResetDefaults = container.querySelector('#btn-reset-defaults');
  const btnSysid = container.querySelector('#btn-sysid');
  const btnOpenLoop = container.querySelector('#btn-open-loop');
  const actionStatusEl = container.querySelector('#action-status');
  const ekfStatusEl = container.querySelector('#ekf-status');
  const olStatusEl = container.querySelector('#ol-status');

  let latestState = state;

  // All user-editable parameter fields.
  const paramInputs = [thermalMassInput, rExternalInput, sigmaWInput, sigmaVInput, sigmaBInput, horizonInput];

  // Tracks whether the user has begun a manual identification process (i.e.
  // changed any parameter). Once true, the reactive update() callback stops
  // overwriting the form from system state, so running a reconstruction or
  // open-loop simulation — which triggers a state update — cannot reset the
  // parameters the user is testing. It is reset only when the page is
  // re-created (navigation / page refresh) or after the user commits or
  // reverts parameters.
  let userEditing = false;
  paramInputs.forEach((inp) => {
    inp.addEventListener('input', () => { userEditing = true; });
  });

  // -----------------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------------

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

  // Populate all parameter fields from current system state.
  // Model params come from the active temperature_filtered sensor (authoritative
  // source for the live thermal model). Stochastic params come from the
  // controller_config sensor. Horizon falls back to the last sysid run or the
  // default of 6 h.
  function populateFromState(slug, st) {
    const filteredAttrs = st[filteredEntityId(slug)]?.attributes;
    if (filteredAttrs) {
      if (filteredAttrs.thermal_mass != null) thermalMassInput.value = filteredAttrs.thermal_mass;
      if (filteredAttrs.r_external != null) rExternalInput.value = filteredAttrs.r_external;
    }

    const configAttrs = st[CONFIG_ENTITY]?.attributes || {};
    if (configAttrs.sigma_w != null) sigmaWInput.value = configAttrs.sigma_w;
    if (configAttrs.sigma_v != null) sigmaVInput.value = configAttrs.sigma_v;
    if (configAttrs.sigma_b != null) sigmaBInput.value = configAttrs.sigma_b;

    const sysidAttrs = st[sysidEntityId(slug)]?.attributes;
    if (sysidAttrs?.horizon_hours != null) horizonInput.value = sysidAttrs.horizon_hours;
  }

  // After an ML dry-run the coordinator writes identified C and R into
  // sysid_results and fires async_update_listeners(), so the sysid sensor
  // attributes carry the freshly estimated values.  Populate only the model
  // parameter fields from those attributes.
  function populateModelFromSysid(slug, st) {
    const sysidAttrs = st[sysidEntityId(slug)]?.attributes;
    if (!sysidAttrs) return;
    if (sysidAttrs.thermal_mass != null) thermalMassInput.value = sysidAttrs.thermal_mass;
    if (sysidAttrs.r_external != null) rExternalInput.value = sysidAttrs.r_external;
  }

  function renderEkfResults(slug, st) {
    const attrs = st[sysidEntityId(slug)]?.attributes || {};
    updateKpiCard(kpiEkfRmse, { value: attrs.rmse != null ? formatNumber(attrs.rmse, 3) + ' °C' : '—' });
    updateKpiCard(kpiEkfMae, { value: attrs.mae != null ? formatNumber(attrs.mae, 3) + ' °C' : '—' });
    buildEkfChart(ekfChart, attrs.simulation);
  }

  function renderOlResults(slug, st) {
    const attrs = st[openLoopEntityId(slug)]?.attributes || {};
    updateKpiCard(kpiOlRmse, { value: attrs.open_loop_rmse != null ? formatNumber(attrs.open_loop_rmse, 3) + ' °C' : '—' });
    updateKpiCard(kpiOlMae, { value: attrs.open_loop_mae != null ? formatNumber(attrs.open_loop_mae, 3) + ' °C' : '—' });
    buildOlChart(olChart, attrs.simulation);
  }

  // Compute the [min, max] timestamp (ms) spanned by a simulation series so the
  // input/disturbance plots can be locked to the same x-range as the fit plot.
  function simTimeRange(simulation) {
    if (!Array.isArray(simulation) || simulation.length === 0) return null;
    let xMin = Infinity;
    let xMax = -Infinity;
    for (const s of simulation) {
      const t = new Date(s.time).getTime();
      if (isNaN(t)) continue;
      if (t < xMin) xMin = t;
      if (t > xMax) xMax = t;
    }
    if (!isFinite(xMin) || !isFinite(xMax)) return null;
    return { xMin, xMax };
  }

  // Fetch the recorded heating input and disturbances for this room over the
  // given horizon and render them below the corresponding fit plot.  These come
  // straight from the recorder — the same source the buffer is rebuilt from —
  // so they show exactly the signals that drove the reconstruction/open-loop.
  async function renderAuxPlots(inputsChart, disturbChart, horizonHours, xRange) {
    const powerEntity = room.entities?.['heating_power_measured'];
    const solarEntity = room.entities?.['solar_gain_measured'];
    const outdoorEntity = 'sensor.heating_assistant_outdoor_temperature_measured';
    const ids = [powerEntity, solarEntity, outdoorEntity].filter(Boolean);
    if (ids.length === 0) return;

    const hours = horizonHours > 0 ? horizonHours : DEFAULTS.horizon_hours;
    let hist = {};
    try {
      hist = await connection.getHistory(ids, hours);
    } catch (err) {
      return;
    }

    const powerPts = historyToDataPoints(hist[powerEntity] || []);
    const solarPts = historyToDataPoints(hist[solarEntity] || []);
    const outdoorPts = historyToDataPoints(hist[outdoorEntity] || []);
    const xLimits = xRange || {};

    inputsChart.render(
      [makeDataset('Heating Power', powerPts, '#ffb74d', { borderWidth: 2, stepped: true })],
      { ...xLimits },
    );
    disturbChart.render(
      [
        makeDataset('Outdoor Temp', outdoorPts, '#90a4ae', { borderWidth: 2, yAxisID: 'y' }),
        makeDataset('Solar Gain', solarPts, '#fff176', { borderWidth: 2, yAxisID: 'y2' }),
      ],
      { ...xLimits },
    );
  }

  function renderEkfAux() {
    const attrs = latestState[sysidEntityId(roomSlug)]?.attributes || {};
    const horizon = attrs.horizon_hours != null
      ? Number(attrs.horizon_hours)
      : parseFloat(horizonInput.value);
    return renderAuxPlots(
      ekfInputsChart, ekfDisturbChart, horizon, simTimeRange(attrs.simulation),
    );
  }

  function renderOlAux() {
    const attrs = latestState[openLoopEntityId(roomSlug)]?.attributes || {};
    const horizon = attrs.horizon_hours != null
      ? Number(attrs.horizon_hours)
      : parseFloat(horizonInput.value);
    return renderAuxPlots(
      olInputsChart, olDisturbChart, horizon, simTimeRange(attrs.simulation),
    );
  }

  function renderParamHistory(st) {
    historyListEl.innerHTML = '';
    const config = st[CONFIG_ENTITY]?.attributes || {};

    // parameter_history is a LIST of full-system snapshots (most recent first).
    // Each entry: { rooms: { "room_slug": { thermal_mass, r_external, ... } },
    //               estimated_at, source, rmse? }
    // Rooms are keyed by the configured room name (slug format, e.g. "living_room").
    const allHistory = config.parameter_history || [];
    if (allHistory.length === 0) {
      historyListEl.innerHTML = '<span class="tuning-section__desc">No history available.</span>';
      return;
    }

    const table = document.createElement('table');
    table.className = 'param-history-table';
    table.innerHTML = `
      <thead>
        <tr>
          <th>#</th><th>Date</th>
          <th>Thermal Mass</th><th>R External</th><th>RMSE</th><th></th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    const tbody = table.querySelector('tbody');
    for (let i = 0; i < allHistory.length; i++) {
      const entry = allHistory[i];
      // Per-room params are keyed by the configured room name (slug format).
      const roomData = entry.rooms?.[roomSlug] || {};
      const thermalMass = roomData.thermal_mass;
      const rExternal = roomData.r_external;
      const date = entry.estimated_at
        ? new Date(entry.estimated_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : '—';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${i + 1}</td>
        <td>${date}</td>
        <td>${thermalMass != null ? formatMass(thermalMass) : '—'}</td>
        <td>${rExternal != null ? formatNumber(rExternal, 4) : '—'}</td>
        <td>${entry.rmse != null ? formatNumber(entry.rmse, 3) + ' °C' : '—'}</td>
        <td><button class="btn btn--ghost btn--sm" data-revert="${i}">Revert</button></td>
      `;
      tbody.appendChild(tr);
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'param-history-table-wrapper';
    wrapper.appendChild(table);
    historyListEl.appendChild(wrapper);

    table.querySelectorAll('[data-revert]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const idx = parseInt(btn.dataset.revert, 10);
        btn.disabled = true;
        btn.textContent = '…';
        try {
          await hass.callService('heating_assistant', 'revert_parameters', {
            room_name: roomSlug,
            history_index: idx,
          });
          // The reverted set is now the applied one; resume state syncing so
          // the form updates to reflect it.
          userEditing = false;
          btn.textContent = '✓';
        } catch (err) {
          btn.textContent = 'ERR';
        }
      });
    });
  }

  // -----------------------------------------------------------------------
  // Button interactions
  // -----------------------------------------------------------------------

  // Run Auto-Identification: dry-run ML estimation, then populate model
  // parameter fields with the identified C and R from the sysid sensor.
  btnAutoIdentify.addEventListener('click', async () => {
    setStatus(actionStatusEl, 'Running identification…', 'running');
    btnAutoIdentify.disabled = true;
    try {
      await hass.callService('heating_assistant', 'estimate_parameters_ml', {
        apply_parameters: false,
      });
      // The coordinator updates sysid_results and fires async_update_listeners()
      // before the service call resolves.  Allow ~800 ms for the HA websocket
      // state event to arrive and update latestState via the update() callback.
      await new Promise((res) => setTimeout(res, 800));
      populateModelFromSysid(roomSlug, latestState);
      // Loaded values are pending review; protect them from state-sync resets.
      userEditing = true;
      setStatus(
        actionStatusEl,
        'Identified parameters loaded — review above, then click “Apply Parameters” to commit.',
        '',
      );
    } catch (err) {
      setStatus(actionStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnAutoIdentify.disabled = false;
  });

  // Apply Parameters: persist model params + stochastic params to the system.
  btnApplyParams.addEventListener('click', async () => {
    setStatus(actionStatusEl, 'Applying parameters…', 'running');
    btnApplyParams.disabled = true;
    try {
      await hass.callService('heating_assistant', 'store_identified_parameters', {
        room_name: roomSlug,
        thermal_mass: parseFloat(thermalMassInput.value),
        r_external: parseFloat(rExternalInput.value),
        source: 'manual',
      });
      await hass.callService('heating_assistant', 'update_estimation_params', {
        sigma_w: parseFloat(sigmaWInput.value),
        sigma_v: parseFloat(sigmaVInput.value),
        sigma_b: parseFloat(sigmaBInput.value),
      });
      // Edits are now the applied parameters; resume syncing the form from
      // system state so it reflects the authoritative committed values.
      userEditing = false;
      setStatus(actionStatusEl, 'Parameters applied and stored in history.', 'success');
    } catch (err) {
      setStatus(actionStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnApplyParams.disabled = false;
  });

  // Reset to Defaults: fill all fields with factory defaults, no service call.
  btnResetDefaults.addEventListener('click', () => {
    thermalMassInput.value = DEFAULTS.thermal_mass;
    rExternalInput.value = DEFAULTS.r_external;
    sigmaWInput.value = DEFAULTS.sigma_w;
    sigmaVInput.value = DEFAULTS.sigma_v;
    sigmaBInput.value = DEFAULTS.sigma_b;
    horizonInput.value = DEFAULTS.horizon_hours;
    // Defaults are pending review; protect them from state-sync resets.
    userEditing = true;
    setStatus(actionStatusEl, 'Default values loaded — click “Apply Parameters” to commit.', '');
  });

  // EKF Reconstruction: run with the current parameter field values.
  btnSysid.addEventListener('click', async () => {
    setStatus(ekfStatusEl, 'Running EKF reconstruction…', 'running');
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
      // Let the websocket state event with the fresh results arrive, then plot
      // the temperature fit and the input/disturbance signals over its horizon.
      await new Promise((res) => setTimeout(res, 800));
      renderEkfResults(roomSlug, latestState);
      await renderEkfAux();
      setStatus(ekfStatusEl, 'Complete.', '');
    } catch (err) {
      setStatus(ekfStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnSysid.disabled = false;
    btnOpenLoop.disabled = false;
  });

  // Open-Loop Simulation: run with the current horizon field value.
  btnOpenLoop.addEventListener('click', async () => {
    setStatus(olStatusEl, 'Running open-loop simulation…', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await hass.callService('heating_assistant', 'run_open_loop_simulation', {
        room_name: roomSlug,
        segment_length: 30,
        horizon_hours: parseFloat(horizonInput.value),
      });
      await new Promise((res) => setTimeout(res, 800));
      renderOlResults(roomSlug, latestState);
      await renderOlAux();
      setStatus(olStatusEl, 'Complete.', '');
    } catch (err) {
      setStatus(olStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnSysid.disabled = false;
    btnOpenLoop.disabled = false;
  });

  // -----------------------------------------------------------------------
  // Initial render
  // -----------------------------------------------------------------------
  populateFromState(roomSlug, state);
  renderEkfResults(roomSlug, state);
  renderOlResults(roomSlug, state);
  renderParamHistory(state);
  // Populate the input/disturbance plots from existing results (if any) without
  // blocking the initial render.
  renderEkfAux();
  renderOlAux();

  return {
    update(newState) {
      latestState = newState;
      // Only sync the form from system state before the user has begun a manual
      // identification process and while no field is focused. Once the user has
      // edited a parameter, running a reconstruction / open-loop simulation (or
      // any other state update) must not reset their values — that should only
      // happen on navigation or page refresh, which re-creates this page.
      const rootNode = container.getRootNode();
      const focused = (rootNode instanceof ShadowRoot ? rootNode : document).activeElement;
      if (!userEditing && !paramInputs.some((inp) => inp === focused)) {
        populateFromState(roomSlug, newState);
      }
      renderEkfResults(roomSlug, newState);
      renderOlResults(roomSlug, newState);
      renderParamHistory(newState);
    },
    destroy() {
      ekfChart.destroy();
      ekfInputsChart.destroy();
      ekfDisturbChart.destroy();
      olChart.destroy();
      olInputsChart.destroy();
      olDisturbChart.destroy();
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
  if (val == null || isNaN(val)) return { label: '—', class: '' };
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
    makeDataset('Predicted', predicted, '#4fc3f7', { borderWidth: 2 }),
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
