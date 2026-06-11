import { TimeSeriesChart, makeDataset, loadChartJs, createSparkline, historyToDataPoints } from '../components/time-series-chart.js';
import { createKpiCard, updateKpiCard } from '../components/kpi-card.js';
import { entityValue, formatNumber, systemEntity } from '../utils.js';

// Default parameter values — must match backend DEFAULT_* constants in const.py
const DEFAULTS = {
  sigma_w: 0.1,
  sigma_v: 0.5,
  thermal_mass: 5000000,
  r_external: 0.05,
  internal_gain: 0,
  solar_scale: 1.0,
  c_air_fraction: 0.05,
  r_aw_fraction: 0.05,
  heater_scale: 1.0,
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
      Auto-Identification fills the fields below with estimates — nothing is applied until you click Apply Parameters.
    </p>
    <div class="tuning-actions">
      <button class="btn btn--accent tuning-actions__btn" id="btn-auto-identify">Run Auto-Identification</button>
      <button class="btn btn--primary tuning-actions__btn" id="btn-apply-params">Apply Parameters</button>
      <button class="btn btn--secondary tuning-actions__btn" id="btn-reset-defaults">Reset to Defaults</button>
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
          <div class="form-group__header">
            <label class="form-label" for="param-thermal-mass">Thermal Mass (C)</label>
            <button class="param-lock-btn" data-param="thermal_mass" title="Lock: hold fixed during auto-identification">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-thermal-mass"
            step="100000" min="10000" value="${DEFAULTS.thermal_mass}">
          <span class="form-hint">J/K &mdash; thermal storage capacity of the room</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-r-external">Thermal Resistance (R<sub>ext</sub>)</label>
            <button class="param-lock-btn" data-param="r_external" title="Lock: hold fixed during auto-identification">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-r-external"
            step="0.001" min="0.0001" value="${DEFAULTS.r_external}">
          <span class="form-hint">K/W &mdash; envelope resistance to outdoor</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-internal-gain">Internal Gain (Q<sub>int</sub>)</label>
            <button class="param-lock-btn" data-param="internal_gain" title="Lock: hold fixed during auto-identification">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-internal-gain"
            step="10" value="${DEFAULTS.internal_gain}">
          <span class="form-hint">W &mdash; constant internal heat (people, appliances)</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-solar-scale">Solar Scale</label>
            <button class="param-lock-btn" data-param="solar_scale" title="Lock: hold fixed during auto-identification">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-solar-scale"
            step="0.01" min="0" value="${DEFAULTS.solar_scale}">
          <span class="form-hint">&times; &mdash; multiplier on modelled solar gain (1.0 = model)</span>
        </div>
      </div>
    </div>

    <div class="params-subsection">
      <div class="params-subsection__title">Envelope Split (2R2C)</div>
      <div class="tuning-params-grid">
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-c-air-fraction">Air-node Mass Fraction</label>
            <button class="param-lock-btn" data-param="c_air_fraction" title="Lock: hold fixed during auto-identification">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-c-air-fraction"
            step="0.001" min="0" max="1" value="${DEFAULTS.c_air_fraction}">
          <span class="form-hint">0&ndash;1 &mdash; share of mass on the fast air node</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-r-aw-fraction">Air&ndash;Wall Resistance Fraction</label>
            <button class="param-lock-btn" data-param="r_aw_fraction" title="Lock: hold fixed during auto-identification">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-r-aw-fraction"
            step="0.001" min="0" max="1" value="${DEFAULTS.r_aw_fraction}">
          <span class="form-hint">0&ndash;1 &mdash; fraction of conductive-path resistance on the air&harr;wall film (infiltration excluded)</span>
        </div>
      </div>
    </div>

    <div class="params-subsection" id="heater-scales-subsection">
      <div class="params-subsection__title">Heater Power Scales</div>
      <div class="tuning-params-grid" id="heater-scales-list"></div>
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
      </div>
    </div>

    <div class="params-subsection params-subsection--last">
      <div class="params-subsection__title">Identification Window</div>
      <div class="window-mode-toggle">
        <button class="window-mode-btn window-mode-btn--active" id="window-mode-recent" type="button">Recent Horizon</button>
        <button class="window-mode-btn" id="window-mode-custom" type="button">Custom Date Range</button>
      </div>
      <div id="window-panel-recent" class="tuning-params-grid">
        <div class="form-group">
          <label class="form-label" for="param-horizon">Horizon</label>
          <input class="form-input" type="number" id="param-horizon"
            step="0.5" min="0.5" max="72" value="${DEFAULTS.horizon_hours}">
          <span class="form-hint">hours &mdash; history window ending at the most recent record</span>
        </div>
      </div>
      <div id="window-panel-custom" class="tuning-params-grid" style="display:none">
        <div class="form-group">
          <label class="form-label" for="param-window-start">Window Start</label>
          <input class="form-input form-input--datetime" type="datetime-local" id="param-window-start">
          <span class="form-hint">Start of the identification window (local time)</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="param-window-end">Window End</label>
          <input class="form-input form-input--datetime" type="datetime-local" id="param-window-end">
          <span class="form-hint">End of the identification window (local time)</span>
        </div>
        <div class="form-group window-preset-row">
          <span class="form-hint">Quick presets:</span>
          <div class="window-presets">
            <button class="btn btn--ghost btn--sm" data-preset="1h" type="button">Last 1 h</button>
            <button class="btn btn--ghost btn--sm" data-preset="6h" type="button">Last 6 h</button>
            <button class="btn btn--ghost btn--sm" data-preset="12h" type="button">Last 12 h</button>
            <button class="btn btn--ghost btn--sm" data-preset="24h" type="button">Last 24 h</button>
          </div>
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
      Simulate the thermal model using the parameters and horizon configured above.
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
    <p class="tuning-section__desc" style="margin-bottom:12px">Previously applied parameter sets.</p>
    <div id="param-history-list"></div>
  `;
  container.appendChild(historySection);

  const historyListEl = historySection.querySelector('#param-history-list');

  // -----------------------------------------------------------------------
  // Input references
  // -----------------------------------------------------------------------
  const thermalMassInput = container.querySelector('#param-thermal-mass');
  const rExternalInput = container.querySelector('#param-r-external');
  const internalGainInput = container.querySelector('#param-internal-gain');
  const solarScaleInput = container.querySelector('#param-solar-scale');
  const cAirFractionInput = container.querySelector('#param-c-air-fraction');
  const rAwFractionInput = container.querySelector('#param-r-aw-fraction');
  const sigmaWInput = container.querySelector('#param-sigma-w');
  const sigmaVInput = container.querySelector('#param-sigma-v');
  const horizonInput = container.querySelector('#param-horizon');
  const windowModeRecentBtn = container.querySelector('#window-mode-recent');
  const windowModeCustomBtn = container.querySelector('#window-mode-custom');
  const windowPanelRecent = container.querySelector('#window-panel-recent');
  const windowPanelCustom = container.querySelector('#window-panel-custom');
  const windowStartInput = container.querySelector('#param-window-start');
  const windowEndInput = container.querySelector('#param-window-end');
  const btnAutoIdentify = container.querySelector('#btn-auto-identify');
  const btnApplyParams = container.querySelector('#btn-apply-params');
  const btnResetDefaults = container.querySelector('#btn-reset-defaults');
  const btnSysid = container.querySelector('#btn-sysid');
  const btnOpenLoop = container.querySelector('#btn-open-loop');
  const actionStatusEl = container.querySelector('#action-status');
  const ekfStatusEl = container.querySelector('#ekf-status');
  const olStatusEl = container.querySelector('#ol-status');

  let latestState = state;

  // Per-source heater-scale inputs, keyed by source name. Built lazily from the
  // sources configured in this room (read from the config sensor, which may
  // load asynchronously) so each heater scale is an ordinary editable parameter
  // applied together with the rest.
  const heaterScaleInputs = {};
  let heaterInputsBuilt = false;

  // All user-editable parameter fields (heater-scale inputs are appended once
  // they are built by ensureHeaterScaleInputs).
  const paramInputs = [
    thermalMassInput, rExternalInput, internalGainInput, solarScaleInput,
    cAirFractionInput, rAwFractionInput,
    sigmaWInput, sigmaVInput, horizonInput,
  ];

  // SVG icons for the lock button — open (unlocked) and closed (locked).
  const _ICON_OPEN = `<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>`;
  const _ICON_LOCKED = `<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`;

  // Per-room localStorage key so each room's locks persist independently.
  const _LOCKS_KEY = `heating_assistant_sysid_locks_v1_${roomSlug}`;

  function _saveLocks() {
    try { localStorage.setItem(_LOCKS_KEY, JSON.stringify([...lockedParams])); } catch (_) {}
  }

  function _loadLocks() {
    try {
      const raw = localStorage.getItem(_LOCKS_KEY);
      return raw ? new Set(JSON.parse(raw)) : new Set();
    } catch (_) { return new Set(); }
  }

  // Tracks locked parameters (those held fixed during auto-identification).
  // Keys for room params: 'thermal_mass', 'r_external', etc.
  // Keys for heater scales: 'heater_scale:<source_name>'.
  // Initialised from localStorage so locks survive page reloads.
  const lockedParams = _loadLocks();

  // Apply visual lock state to a button+input pair without toggling.
  function _applyLockVisual(paramKey, inputEl, btnEl) {
    const locked = lockedParams.has(paramKey);
    inputEl.classList.toggle('form-input--locked', locked);
    btnEl.classList.toggle('param-lock-btn--locked', locked);
    if (locked) {
      btnEl.innerHTML = `${_ICON_LOCKED} Fixed`;
      btnEl.title = 'Unlock: allow auto-identification to vary this parameter';
    } else {
      btnEl.innerHTML = `${_ICON_OPEN} Fix`;
      btnEl.title = 'Lock: hold fixed during auto-identification';
    }
  }

  function toggleLock(paramKey, inputEl, btnEl) {
    if (lockedParams.has(paramKey)) {
      lockedParams.delete(paramKey);
    } else {
      lockedParams.add(paramKey);
    }
    _applyLockVisual(paramKey, inputEl, btnEl);
    _saveLocks();
  }

  // Wire up lock buttons for the static parameter fields in paramsCard,
  // and restore any persisted locked state immediately.
  paramsCard.querySelectorAll('.param-lock-btn').forEach((btn) => {
    const paramKey = btn.dataset.param;
    const input = btn.closest('.form-group').querySelector('.form-input');
    _applyLockVisual(paramKey, input, btn);
    btn.addEventListener('click', () => toggleLock(paramKey, input, btn));
  });

  // Build the locked_params payload for the estimate_parameters_ml service call.
  // Per-room params are keyed by room slug; heater scales by source name.
  function buildLockedParams() {
    const result = {};
    const roomParamInputs = {
      thermal_mass: thermalMassInput,
      r_external: rExternalInput,
      internal_gain: internalGainInput,
      solar_scale: solarScaleInput,
      c_air_fraction: cAirFractionInput,
      r_aw_fraction: rAwFractionInput,
    };
    for (const [param, inp] of Object.entries(roomParamInputs)) {
      if (lockedParams.has(param)) {
        result[param] = { [roomSlug]: parseFloat(inp.value) };
      }
    }
    for (const [srcName, inp] of Object.entries(heaterScaleInputs)) {
      if (lockedParams.has(`heater_scale:${srcName}`)) {
        result.heater_scales = result.heater_scales || {};
        result.heater_scales[srcName] = parseFloat(inp.value);
      }
    }
    return Object.keys(result).length > 0 ? result : undefined;
  }

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
  // Window mode: 'recent' (horizon-based) or 'custom' (explicit date range)
  // -----------------------------------------------------------------------
  const _WINDOW_MODE_KEY = `heating_assistant_sysid_window_mode_v1_${roomSlug}`;
  const _WINDOW_START_KEY = `heating_assistant_sysid_window_start_v1_${roomSlug}`;
  const _WINDOW_END_KEY = `heating_assistant_sysid_window_end_v1_${roomSlug}`;

  let windowMode = (() => {
    try { return localStorage.getItem(_WINDOW_MODE_KEY) || 'recent'; } catch (_) { return 'recent'; }
  })();

  // Format a Date as the value string for <input type="datetime-local">.
  // That format is "YYYY-MM-DDTHH:MM" in local time (no timezone suffix).
  function _toDatetimeLocal(date) {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function _initWindowDefaults() {
    const storedStart = (() => { try { return localStorage.getItem(_WINDOW_START_KEY); } catch (_) { return null; } })();
    const storedEnd   = (() => { try { return localStorage.getItem(_WINDOW_END_KEY);   } catch (_) { return null; } })();
    if (storedStart) {
      windowStartInput.value = storedStart;
    } else {
      const defaultStart = new Date(Date.now() - parseFloat(horizonInput.value) * 3600_000);
      windowStartInput.value = _toDatetimeLocal(defaultStart);
    }
    if (storedEnd) {
      windowEndInput.value = storedEnd;
    } else {
      windowEndInput.value = _toDatetimeLocal(new Date());
    }
  }

  function _applyWindowMode(mode) {
    windowMode = mode;
    try { localStorage.setItem(_WINDOW_MODE_KEY, mode); } catch (_) {}
    if (mode === 'custom') {
      windowModeRecentBtn.classList.remove('window-mode-btn--active');
      windowModeCustomBtn.classList.add('window-mode-btn--active');
      windowPanelRecent.style.display = 'none';
      windowPanelCustom.style.display = '';
    } else {
      windowModeCustomBtn.classList.remove('window-mode-btn--active');
      windowModeRecentBtn.classList.add('window-mode-btn--active');
      windowPanelCustom.style.display = 'none';
      windowPanelRecent.style.display = '';
    }
  }

  _initWindowDefaults();
  _applyWindowMode(windowMode);

  windowModeRecentBtn.addEventListener('click', () => _applyWindowMode('recent'));
  windowModeCustomBtn.addEventListener('click', () => _applyWindowMode('custom'));

  // Persist datetime values on change so they survive navigation.
  windowStartInput.addEventListener('change', () => {
    try { localStorage.setItem(_WINDOW_START_KEY, windowStartInput.value); } catch (_) {}
    userEditing = true;
  });
  windowEndInput.addEventListener('change', () => {
    try { localStorage.setItem(_WINDOW_END_KEY, windowEndInput.value); } catch (_) {}
    userEditing = true;
  });

  // Quick-preset buttons (Last 1h / 6h / 12h / 24h).
  windowPanelCustom.querySelectorAll('[data-preset]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const hours = parseFloat(btn.dataset.preset);
      const now = new Date();
      const start = new Date(now.getTime() - hours * 3600_000);
      windowStartInput.value = _toDatetimeLocal(start);
      windowEndInput.value = _toDatetimeLocal(now);
      try {
        localStorage.setItem(_WINDOW_START_KEY, windowStartInput.value);
        localStorage.setItem(_WINDOW_END_KEY, windowEndInput.value);
      } catch (_) {}
      userEditing = true;
    });
  });

  // -----------------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------------

  function setStatus(el, text, type = '') {
    el.textContent = text;
    el.className = 'tuning-actions__status';
    if (type) el.classList.add(`tuning-actions__status--${type}`);
  }

  // Collect every parameter field currently shown in the panel into the
  // service-data payload used by the EKF reconstruction and open-loop
  // simulation.  Per-room thermal-model parameters are keyed as
  // ``<param>_<room_slug>`` (matching the backend's _extract_sim_room_params);
  // the per-source heater scales are passed as a ``heater_scales`` map.  This
  // makes both simulations reflect exactly what the user has entered, with no
  // need to click Apply first.
  function collectSimParams() {
    const heaterScales = {};
    for (const [srcName, inp] of Object.entries(heaterScaleInputs)) {
      const val = parseFloat(inp.value);
      if (isFinite(val)) heaterScales[srcName] = val;
    }
    const params = {
      room_name: roomSlug,
      sigma_w: parseFloat(sigmaWInput.value),
      sigma_v: parseFloat(sigmaVInput.value),
      [`thermal_mass_${roomSlug}`]: parseFloat(thermalMassInput.value),
      [`r_external_${roomSlug}`]: parseFloat(rExternalInput.value),
      [`internal_gain_${roomSlug}`]: parseFloat(internalGainInput.value),
      [`solar_scale_${roomSlug}`]: parseFloat(solarScaleInput.value),
      [`c_air_fraction_${roomSlug}`]: parseFloat(cAirFractionInput.value),
      [`r_aw_fraction_${roomSlug}`]: parseFloat(rAwFractionInput.value),
    };

    if (windowMode === 'custom' && windowStartInput.value && windowEndInput.value) {
      // Convert datetime-local (local time) to UNIX timestamp.
      const startTs = new Date(windowStartInput.value).getTime() / 1000;
      const endTs   = new Date(windowEndInput.value).getTime() / 1000;
      if (isFinite(startTs) && isFinite(endTs) && startTs < endTs) {
        params.window_start = startTs;
        params.window_end   = endTs;
      }
    } else {
      params.horizon_hours = parseFloat(horizonInput.value);
    }

    if (Object.keys(heaterScales).length) params.heater_scales = heaterScales;
    return params;
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
  // source for the live thermal model). Stochastic params and the identification
  // horizon come from the controller_config sensor (persisted by Apply Parameters).
  function populateFromState(slug, st) {
    const filteredAttrs = st[filteredEntityId(slug)]?.attributes;
    if (filteredAttrs) {
      if (filteredAttrs.thermal_mass != null) thermalMassInput.value = filteredAttrs.thermal_mass;
      if (filteredAttrs.r_external != null) rExternalInput.value = filteredAttrs.r_external;
      if (filteredAttrs.internal_gain != null) internalGainInput.value = filteredAttrs.internal_gain;
      if (filteredAttrs.solar_scale != null) solarScaleInput.value = filteredAttrs.solar_scale;
      if (filteredAttrs.c_air_fraction != null) cAirFractionInput.value = filteredAttrs.c_air_fraction;
      if (filteredAttrs.r_aw_fraction != null) rAwFractionInput.value = filteredAttrs.r_aw_fraction;
    }

    const configAttrs = st[CONFIG_ENTITY]?.attributes || {};
    if (configAttrs.sigma_w != null) sigmaWInput.value = configAttrs.sigma_w;
    if (configAttrs.sigma_v != null) sigmaVInput.value = configAttrs.sigma_v;
    // Horizon is now persisted in the config entity (set by Apply Parameters).
    // Fall back to sysid sensor's last-run horizon if the config hasn't been
    // saved yet (e.g. on first use before Apply Parameters is clicked).
    if (configAttrs.identification_horizon_hours != null) {
      horizonInput.value = configAttrs.identification_horizon_hours;
    } else {
      const sysidAttrs = st[sysidEntityId(slug)]?.attributes;
      if (sysidAttrs?.horizon_hours != null) horizonInput.value = sysidAttrs.horizon_hours;
    }

    ensureHeaterScaleInputs(st);
    // Seed each heater-scale input from the currently applied power scale.
    const currentScales = configAttrs.current_heater_scales || {};
    for (const [srcName, inp] of Object.entries(heaterScaleInputs)) {
      const info = currentScales[srcName];
      if (info && info.power_scale != null) inp.value = info.power_scale;
    }
  }

  // The heat sources in this room are read from the config sensor's
  // ``current_heater_scales`` map ({source: {room_slug, power_scale}}).  Build
  // one ordinary editable input per source the first time that map is
  // available; nothing special, just another row in the parameter list.
  function ensureHeaterScaleInputs(st) {
    if (heaterInputsBuilt) return;
    const listEl = container.querySelector('#heater-scales-list');
    if (!listEl) return;
    const configAttrs = st[CONFIG_ENTITY]?.attributes || {};
    const currentScales = configAttrs.current_heater_scales || {};
    const sources = Object.entries(currentScales).filter(
      ([, info]) => info.room_slug === roomSlug
    );
    if (sources.length === 0) {
      // Config not loaded yet (or no heaters) — try again on the next update.
      listEl.innerHTML = '<span class="form-hint">No heaters configured for this room.</span>';
      return;
    }
    listEl.innerHTML = '';
    for (const [srcName, info] of sources) {
      const inputId = `param-heater-scale-${srcName.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
      const group = document.createElement('div');
      group.className = 'form-group';
      group.innerHTML = `
        <div class="form-group__header">
          <label class="form-label" for="${inputId}">${srcName}</label>
          <button class="param-lock-btn" title="Lock: hold fixed during auto-identification">Fix</button>
        </div>
        <input class="form-input" type="number" id="${inputId}"
          step="0.01" min="0" value="${info.power_scale ?? DEFAULTS.heater_scale}">
        <span class="form-hint">&times; &mdash; heater power scale (1.0 = rated power)</span>
      `;
      listEl.appendChild(group);
      const inp = group.querySelector('input');
      const lockBtn = group.querySelector('.param-lock-btn');
      heaterScaleInputs[srcName] = inp;
      inp.addEventListener('input', () => { userEditing = true; });
      const heaterKey = `heater_scale:${srcName}`;
      _applyLockVisual(heaterKey, inp, lockBtn);
      lockBtn.addEventListener('click', () => toggleLock(heaterKey, inp, lockBtn));
      paramInputs.push(inp);
    }
    heaterInputsBuilt = true;
  }

  // After an ML dry-run the coordinator writes the full identified parameter
  // set into sysid_results and fires async_update_listeners(), so the sysid
  // sensor attributes carry the freshly estimated values.  Populate every
  // parameter field (including the per-source heater scales) from them.
  function populateModelFromSysid(slug, st) {
    const sysidAttrs = st[sysidEntityId(slug)]?.attributes;
    if (!sysidAttrs) return;
    if (sysidAttrs.thermal_mass != null && !lockedParams.has('thermal_mass'))
      thermalMassInput.value = sysidAttrs.thermal_mass;
    if (sysidAttrs.r_external != null && !lockedParams.has('r_external'))
      rExternalInput.value = sysidAttrs.r_external;
    if (sysidAttrs.internal_gain != null && !lockedParams.has('internal_gain'))
      internalGainInput.value = sysidAttrs.internal_gain;
    if (sysidAttrs.solar_scale != null && !lockedParams.has('solar_scale'))
      solarScaleInput.value = sysidAttrs.solar_scale;
    if (sysidAttrs.c_air_fraction != null && !lockedParams.has('c_air_fraction'))
      cAirFractionInput.value = sysidAttrs.c_air_fraction;
    if (sysidAttrs.r_aw_fraction != null && !lockedParams.has('r_aw_fraction'))
      rAwFractionInput.value = sysidAttrs.r_aw_fraction;

    ensureHeaterScaleInputs(st);
    const identifiedScales = sysidAttrs.heater_scales || {};
    for (const [srcName, scale] of Object.entries(identifiedScales)) {
      if (heaterScaleInputs[srcName] != null && scale != null
          && !lockedParams.has(`heater_scale:${srcName}`)) {
        heaterScaleInputs[srcName].value = scale;
      }
    }
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

    let hist = {};
    try {
      if (xRange?.xMin != null && xRange?.xMax != null) {
        hist = await connection.getHistoryRange(ids, new Date(xRange.xMin), new Date(xRange.xMax));
      } else {
        const hours = horizonHours > 0 ? horizonHours : DEFAULTS.horizon_hours;
        hist = await connection.getHistory(ids, hours);
      }
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

  // Derive the xRange to pass to renderAuxPlots based on the current window
  // mode so the input/disturbance plots align with the fit chart.
  function _effectiveXRange(simAttrs) {
    const fromSim = simTimeRange(simAttrs?.simulation);
    if (fromSim) return fromSim;
    // Fall back to the configured window.
    if (windowMode === 'custom' && windowStartInput.value && windowEndInput.value) {
      const xMin = new Date(windowStartInput.value).getTime();
      const xMax = new Date(windowEndInput.value).getTime();
      if (isFinite(xMin) && isFinite(xMax) && xMin < xMax) return { xMin, xMax };
    }
    return null;
  }

  function _effectiveHorizon(attrs) {
    if (attrs?.horizon_hours != null) return Number(attrs.horizon_hours);
    if (windowMode === 'custom' && windowStartInput.value && windowEndInput.value) {
      const ms = new Date(windowEndInput.value) - new Date(windowStartInput.value);
      return ms / 3_600_000;
    }
    return parseFloat(horizonInput.value);
  }

  function renderEkfAux() {
    const attrs = latestState[sysidEntityId(roomSlug)]?.attributes || {};
    return renderAuxPlots(
      ekfInputsChart, ekfDisturbChart, _effectiveHorizon(attrs), _effectiveXRange(attrs),
    );
  }

  function renderOlAux() {
    const attrs = latestState[openLoopEntityId(roomSlug)]?.attributes || {};
    return renderAuxPlots(
      olInputsChart, olDisturbChart, _effectiveHorizon(attrs), _effectiveXRange(attrs),
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
          <th>Thermal Mass</th><th>R External</th><th>Int. Gain</th><th>Solar</th><th>RMSE</th><th></th>
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
      const internalGain = roomData.internal_gain;
      const solarScale = roomData.solar_scale;
      const date = entry.estimated_at
        ? new Date(entry.estimated_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : '—';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${i + 1}</td>
        <td>${date}</td>
        <td>${thermalMass != null ? formatMass(thermalMass) : '—'}</td>
        <td>${rExternal != null ? formatNumber(rExternal, 4) : '—'}</td>
        <td>${internalGain != null ? formatNumber(internalGain, 0) + ' W' : '—'}</td>
        <td>${solarScale != null ? formatNumber(solarScale, 2) + '×' : '—'}</td>
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
      const lp = buildLockedParams();
      const windowData = {};
      if (windowMode === 'custom' && windowStartInput.value && windowEndInput.value) {
        const startTs = new Date(windowStartInput.value).getTime() / 1000;
        const endTs   = new Date(windowEndInput.value).getTime() / 1000;
        if (isFinite(startTs) && isFinite(endTs) && startTs < endTs) {
          windowData.window_start = startTs;
          windowData.window_end   = endTs;
        }
      } else {
        windowData.horizon_hours = parseFloat(horizonInput.value);
      }
      await hass.callService('heating_assistant', 'estimate_parameters_ml', {
        apply_parameters: false,
        ...windowData,
        ...(lp ? { locked_params: lp } : {}),
      });
      // The coordinator updates sysid_results and fires async_update_listeners()
      // before the service call resolves.  Allow ~800 ms for the HA websocket
      // state event to arrive and update latestState via the update() callback.
      await new Promise((res) => setTimeout(res, 800));
      populateModelFromSysid(roomSlug, latestState);
      // Loaded values are pending review; protect them from state-sync resets.
      userEditing = true;
      setStatus(actionStatusEl, 'Loaded — review the fields below, then click Apply Parameters.', '');
    } catch (err) {
      setStatus(actionStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnAutoIdentify.disabled = false;
  });

  // Apply Parameters: persist the full model parameter set (including the
  // per-source heater scales) plus the stochastic params to the system.
  btnApplyParams.addEventListener('click', async () => {
    setStatus(actionStatusEl, 'Applying parameters…', 'running');
    btnApplyParams.disabled = true;
    try {
      const heaterScales = {};
      for (const [srcName, inp] of Object.entries(heaterScaleInputs)) {
        const val = parseFloat(inp.value);
        if (isFinite(val)) heaterScales[srcName] = val;
      }
      await hass.callService('heating_assistant', 'store_identified_parameters', {
        room_name: roomSlug,
        thermal_mass: parseFloat(thermalMassInput.value),
        r_external: parseFloat(rExternalInput.value),
        internal_gain: parseFloat(internalGainInput.value),
        solar_scale: parseFloat(solarScaleInput.value),
        c_air_fraction: parseFloat(cAirFractionInput.value),
        r_aw_fraction: parseFloat(rAwFractionInput.value),
        ...(Object.keys(heaterScales).length ? { heater_scales: heaterScales } : {}),
        source: 'manual',
      });
      await hass.callService('heating_assistant', 'update_estimation_params', {
        sigma_w: parseFloat(sigmaWInput.value),
        sigma_v: parseFloat(sigmaVInput.value),
        identification_horizon_hours: parseFloat(horizonInput.value),
      });
      // Edits are now the applied parameters; resume syncing the form from
      // system state so it reflects the authoritative committed values.
      userEditing = false;
      setStatus(actionStatusEl, 'Applied.', 'success');
    } catch (err) {
      setStatus(actionStatusEl, 'Error: ' + (err.message || err), 'error');
    }
    btnApplyParams.disabled = false;
  });

  // Reset to Defaults: fill all fields with factory defaults, no service call.
  btnResetDefaults.addEventListener('click', () => {
    thermalMassInput.value = DEFAULTS.thermal_mass;
    rExternalInput.value = DEFAULTS.r_external;
    internalGainInput.value = DEFAULTS.internal_gain;
    solarScaleInput.value = DEFAULTS.solar_scale;
    cAirFractionInput.value = DEFAULTS.c_air_fraction;
    rAwFractionInput.value = DEFAULTS.r_aw_fraction;
    sigmaWInput.value = DEFAULTS.sigma_w;
    sigmaVInput.value = DEFAULTS.sigma_v;
    horizonInput.value = DEFAULTS.horizon_hours;
    for (const inp of Object.values(heaterScaleInputs)) inp.value = DEFAULTS.heater_scale;
    // Defaults are pending review; protect them from state-sync resets.
    userEditing = true;
    setStatus(actionStatusEl, 'Defaults loaded.', '');
  });

  // EKF Reconstruction: run with the current parameter field values.
  btnSysid.addEventListener('click', async () => {
    setStatus(ekfStatusEl, 'Running EKF reconstruction…', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await hass.callService('heating_assistant', 'run_sysid_simulation', collectSimParams());
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

  // Open-Loop Simulation: run with the current parameter field values so the
  // chart reflects the model the user is currently evaluating, not the last
  // applied set.
  btnOpenLoop.addEventListener('click', async () => {
    setStatus(olStatusEl, 'Running open-loop simulation…', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await hass.callService('heating_assistant', 'run_open_loop_simulation', {
        ...collectSimParams(),
        segment_length: 30,
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
      } else {
        // Even while the user is editing, make sure the per-source heater-scale
        // inputs get built once the config sensor (which lists the room's
        // sources) becomes available.  This never overwrites existing values.
        ensureHeaterScaleInputs(newState);
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
  const predictedWall = [];
  const wallCovUpper = [];
  const wallCovLower = [];

  for (const entry of simulation) {
    const t = new Date(entry.time).getTime();
    if (isNaN(t)) continue;
    if (entry.measured != null) measured.push({ x: t, y: entry.measured });
    if (entry.predicted != null) predicted.push({ x: t, y: entry.predicted });
    if (entry.cov_upper != null) covUpper.push({ x: t, y: entry.cov_upper });
    if (entry.cov_lower != null) covLower.push({ x: t, y: entry.cov_lower });
    if (entry.predicted_wall != null) predictedWall.push({ x: t, y: entry.predicted_wall });
    if (entry.wall_cov_upper != null) wallCovUpper.push({ x: t, y: entry.wall_cov_upper });
    if (entry.wall_cov_lower != null) wallCovLower.push({ x: t, y: entry.wall_cov_lower });
  }

  const datasets = [
    makeDataset('Measured (air)', measured, '#e57373', {
      borderWidth: 0, pointRadius: 2, pointHoverRadius: 4,
      pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
      showLine: false,
    }),
    makeDataset('Predicted (air)', predicted, '#4fc3f7', { borderWidth: 2 }),
    makeDataset('Above 2σ (air)', covUpper, 'rgba(79,195,247,0.25)', {
      borderWidth: 0, pointRadius: 0, fill: false,
    }),
    makeDataset('Below 2σ (air)', covLower, 'rgba(79,195,247,0.25)', {
      borderWidth: 0, pointRadius: 0,
      fill: '-1', backgroundColor: 'rgba(79,195,247,0.10)',
    }),
  ];

  if (predictedWall.length > 0) {
    datasets.push(
      makeDataset('Predicted (wall)', predictedWall, '#a5d6a7', { borderWidth: 2, borderDash: [4, 3] }),
    );
    if (wallCovUpper.length > 0) {
      datasets.push(
        makeDataset('Above 2σ (wall)', wallCovUpper, 'rgba(165,214,167,0.25)', {
          borderWidth: 0, pointRadius: 0, fill: false,
        }),
        makeDataset('Below 2σ (wall)', wallCovLower, 'rgba(165,214,167,0.25)', {
          borderWidth: 0, pointRadius: 0,
          fill: '-1', backgroundColor: 'rgba(165,214,167,0.10)',
        }),
      );
    }
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

  for (const entry of simulation) {
    const t = new Date(entry.time).getTime();
    if (isNaN(t)) continue;
    if (entry.measured != null) measured.push({ x: t, y: entry.measured });
    if (entry.predicted != null) predicted.push({ x: t, y: entry.predicted });
    if (entry.predicted_wall != null) predictedWall.push({ x: t, y: entry.predicted_wall });
  }

  const datasets = [
    makeDataset('Measured (air)', measured, '#e57373', {
      borderWidth: 0, pointRadius: 2, pointHoverRadius: 4,
      pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
      showLine: false,
    }),
    makeDataset('Predicted (air)', predicted, '#4fc3f7', { borderWidth: 2 }),
  ];

  if (predictedWall.length > 0) {
    datasets.push(
      makeDataset('Predicted (wall)', predictedWall, '#a5d6a7', { borderWidth: 2, borderDash: [4, 3] }),
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
      if (p.y < yMin) yMin = p.y;
      if (p.y > yMax) yMax = p.y;
    }
  }
  if (!isFinite(yMin) || !isFinite(yMax)) return { yMin: undefined, yMax: undefined };
  const margin = (yMax - yMin) * 0.05 || 0.5;
  return { yMin: yMin - margin, yMax: yMax + margin };
}
