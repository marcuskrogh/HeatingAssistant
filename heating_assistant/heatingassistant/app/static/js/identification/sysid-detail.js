import { TimeSeriesChart, makeDataset, historyToDataPoints } from '../components/time-series-chart.js?v=124';
import { createKpiCard, updateKpiCard } from '../components/kpi-card.js?v=124';
import { createCollapsible } from '../components/collapsible.js?v=124';
import { formatNumber, modelFitLabel } from '../utils.js?v=124';
import { setPanelHash } from '../panel-hash.js?v=124';
import {
  createDataset,
  deleteDataset,
  deleteParameterHistory,
  estimateParametersMl,
  runOpenLoopSimulation,
  runSysidSimulation,
  storeIdentifiedParameters,
  updateEstimationParams,
} from '../ha-services.js?v=124';
import { DEFAULTS, CONFIG_ENTITY, valuesEqual } from './sysid-shared.js?v=124';
import { setupDatasetsAndExperiments, buildEkfChart, buildOlChart, formatMass } from './sysid-datasets.js?v=130';

export function renderIdentificationDetail(container, roomSlug, rooms, state, connection, hass) {
  const room = rooms.find((r) => r.slug === roomSlug);
  if (!room) {
    container.innerHTML = `<div class="loading">Room not found: ${roomSlug}</div>`;
    return { update() {}, destroy() {} };
  }

  container.innerHTML = '';

  // Back navigation
  const nav = document.createElement('button');
  nav.className = 'nav-back';
  nav.innerHTML = '<span class="nav-back__arrow">←</span> PARAMETER ESTIMATION';
  nav.addEventListener('click', () => { setPanelHash('#parameter-estimation'); });
  container.appendChild(nav);

  const header = document.createElement('div');
  header.className = 'room-header';
  header.innerHTML = `<h2 class="room-header__title">${room.name}</h2>`;
  container.appendChild(header);

  const pendingBanner = document.createElement('div');
  pendingBanner.className = 'tuning-pending-banner tuning-pending-banner--actions';
  pendingBanner.hidden = true;
  container.appendChild(pendingBanner);

  // -----------------------------------------------------------------------
  // Section 1: Action buttons (top of page)
  // -----------------------------------------------------------------------
  const actionsCard = document.createElement('div');
  actionsCard.className = 'card tuning-section';
  actionsCard.innerHTML = `
    <div class="tuning-section__title">Actions</div>
    <p class="tuning-section__desc">
      Edit the fields below, then click Apply Parameters to activate them. Use the Stored Datasets section to run automatic parameter estimation.
    </p>
    <div class="tuning-actions">
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
    <div class="tuning-section__title">Parameter Estimation Parameters</div>

    <div class="params-subsection">
      <div class="params-subsection__title">Model Parameters</div>
      <div class="tuning-params-grid">
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-thermal-mass">Thermal Mass (C)</label>
            <button class="param-lock-btn" data-param="thermal_mass" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-thermal-mass"
            step="100000" min="10000" value="${DEFAULTS.thermal_mass}">
          <span class="form-hint">J/K &mdash; thermal storage capacity of the room</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-r-external">Thermal Resistance (R<sub>ext</sub>)</label>
            <button class="param-lock-btn" data-param="r_external" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-r-external"
            step="0.001" min="0.0001" value="${DEFAULTS.r_external}">
          <span class="form-hint">K/W &mdash; envelope resistance to outdoor</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-internal-gain">Internal Gain (Q<sub>int</sub>)</label>
            <button class="param-lock-btn" data-param="internal_gain" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-internal-gain"
            step="10" value="${DEFAULTS.internal_gain}">
          <span class="form-hint">W &mdash; constant internal heat (people, appliances)</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-solar-scale">Solar Scale</label>
            <button class="param-lock-btn" data-param="solar_scale" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
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
            <button class="param-lock-btn" data-param="c_air_fraction" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-c-air-fraction"
            step="0.001" min="0" max="1" value="${DEFAULTS.c_air_fraction}">
          <span class="form-hint">0&ndash;1 &mdash; share of mass on the fast air node</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-r-aw-fraction">Air&ndash;Wall Resistance Fraction</label>
            <button class="param-lock-btn" data-param="r_aw_fraction" title="Lock: hold fixed during parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-r-aw-fraction"
            step="0.001" min="0" max="1" value="${DEFAULTS.r_aw_fraction}">
          <span class="form-hint">0&ndash;1 &mdash; fraction of conductive-path resistance on the air&harr;wall film (infiltration excluded)</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-t-wall-initial">Wall Initial Temp (T<sub>wall,0</sub>)</label>
            <button class="param-lock-btn" data-param="t_wall_initial" title="Lock: hold fixed during parameter estimation">Fix</button>
          </div>
          <input class="form-input form-input--readonly" type="text" id="param-t-wall-initial"
            readonly value="&mdash;" tabindex="-1">
          <span class="form-hint">&deg;C &mdash; identified envelope temperature at window start (populated after parameter estimation; lock to hold fixed)</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-ua-open">Open-contact UA</label>
          </div>
          <input class="form-input form-input--readonly" type="text" id="param-ua-open"
            readonly value="&mdash;" tabindex="-1">
          <span class="form-hint">W/K &mdash; extra outdoor exchange while a window or door contact is open (populated after parameter estimation)</span>
        </div>
      </div>
    </div>

    <div class="params-subsection" id="inter-room-r-subsection" hidden>
      <div class="params-subsection__title">Inter-Room Connections</div>
      <p class="params-subsection__desc">
        Thermal resistances between this room and neighbours, estimated during parameter estimation when enough cross-room excitation is present.
      </p>
      <div class="tuning-params-grid" id="inter-room-r-list"></div>
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
            step="0.001" min="0.000001" value="${DEFAULTS.sigma_w}">
          <span class="form-hint">K/&radic;s &mdash; model adaptation rate</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="param-sigma-v">Sensor Noise (&sigma;<sub>v</sub>)</label>
          <input class="form-input" type="number" id="param-sigma-v"
            step="0.001" min="0.000001" value="${DEFAULTS.sigma_v}">
          <span class="form-hint">K &mdash; expected temperature sensor noise</span>
        </div>
      </div>
    </div>

    <div class="params-subsection">
      <div class="params-subsection__title">Parameter Estimation Window</div>
      <div class="window-mode-toggle">
        <button class="window-mode-btn window-mode-btn--active" id="window-mode-recent" type="button">Recent Horizon</button>
        <button class="window-mode-btn" id="window-mode-custom" type="button">Custom Date Range</button>
      </div>
      <div id="window-panel-recent" class="tuning-params-grid">
        <div class="form-group">
          <label class="form-label" for="param-horizon">Horizon</label>
          <input class="form-input" type="number" id="param-horizon"
            step="0.5" min="0.5" value="${DEFAULTS.horizon_hours}">
          <span class="form-hint">hours &mdash; history window ending at the most recent record (up to parameter-estimation history retention)</span>
        </div>
      </div>
      <div id="window-panel-custom" class="window-datetime-panel" style="display:none">
        <div class="window-datetime-inputs">
          <div class="form-group">
            <label class="form-label" for="param-window-start">Window Start</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="datetime-local" id="param-window-start">
            </div>
            <span class="form-hint">Start of the parameter-estimation window (local time)</span>
          </div>
          <div class="form-group">
            <label class="form-label" for="param-window-end">Window End</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="datetime-local" id="param-window-end">
            </div>
            <span class="form-hint">End of the parameter-estimation window (local time)</span>
          </div>
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

    <div class="params-subsection params-subsection--last" id="ds-save-mount"></div>
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
      Two complementary fit tests over the parameter-estimation window configured above.
      <strong>One-step EKF reconstruction</strong> measures short-horizon tracking with Kalman
      correction at each timestep; <strong>multi-step open-loop simulation</strong> is a free-run
      drift test with no measurement feedback. A good model should score well on both.
    </p>
    <div class="grid-kpi" id="fit-comparison-kpis"></div>
    <div class="fit-comparison__hints">
      <span class="form-hint">EKF RMSE: one-step ahead with Kalman correction each timestep</span>
      <span class="form-hint">Open-loop RMSE: multi-step free-run drift without measurement feedback</span>
    </div>
  `;
  container.appendChild(validationIntro);

  const fitComparisonGrid = validationIntro.querySelector('#fit-comparison-kpis');
  const kpiCompareEkfRmse = createKpiCard({ value: '—', label: 'EKF RMSE (one-step)', unit: '' });
  const kpiCompareOlRmse = createKpiCard({ value: '—', label: 'Open-loop RMSE (multi-step)', unit: '' });
  fitComparisonGrid.appendChild(kpiCompareEkfRmse);
  fitComparisonGrid.appendChild(kpiCompareOlRmse);

  // Each validation section is laid out top-to-bottom as:
  //   button → fit KPIs → temperature plot → heating-input plot → disturbance plot
  // so the action sits directly above the plots it produces.
  function buildValidationSection({ title, desc, btnId, btnClass, btnLabel, statusId, kpiId }) {
    const section = document.createElement('div');
    section.className = 'card tuning-section';
    section.innerHTML = `
      <div class="tuning-section__title">${title}</div>
      <p class="tuning-section__desc">${desc}</p>
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

  // ---- One-step EKF reconstruction section ----
  const ekfSection = buildValidationSection({
    title: 'One-Step EKF Reconstruction',
    desc: 'Re-runs the Extended Kalman Filter over the window, resetting state with each new measurement. Evaluates one-step prediction accuracy &mdash; a tight fit here means the model tracks short-term dynamics well.',
    btnId: 'btn-sysid', btnClass: 'btn--primary', btnLabel: 'Run One-Step EKF Reconstruction',
    statusId: 'ekf-status', kpiId: 'ekf-kpis',
  });

  const ekfKpiGrid = ekfSection.querySelector('#ekf-kpis');
  const kpiEkfRmse = createKpiCard({ value: '—', label: 'RMSE (one-step)', unit: '' });
  const kpiEkfMae = createKpiCard({ value: '—', label: 'MAE', unit: '' });
  ekfKpiGrid.appendChild(kpiEkfRmse);
  ekfKpiGrid.appendChild(kpiEkfMae);

  const ekfChart = new TimeSeriesChart(ekfSection.querySelector('[data-chart="temp"]'), {
    title: 'ONE-STEP EKF RECONSTRUCTION', yLabel: '°C', height: 260,
  });
  const ekfInputsChart = new TimeSeriesChart(ekfSection.querySelector('[data-chart="inputs"]'), {
    title: 'HEATING INPUT', yLabel: 'W', height: 180,
  });
  const ekfDisturbChart = new TimeSeriesChart(ekfSection.querySelector('[data-chart="disturb"]'), {
    title: 'DISTURBANCES', yLabel: '°C', y2: true, y2Label: 'W', height: 180,
  });

  // ---- Multi-step open-loop simulation section ----
  const olSection = buildValidationSection({
    title: 'Multi-Step Open-Loop Simulation',
    desc: 'Integrates the model forward from the window start without measurement corrections &mdash; a stress test for drift over many timesteps. A good open-loop fit confirms parameters generalise beyond per-step Kalman corrections.',
    btnId: 'btn-open-loop', btnClass: 'btn--primary', btnLabel: 'Run Multi-Step Open-Loop Simulation',
    statusId: 'ol-status', kpiId: 'ol-kpis',
  });

  const olKpiGrid = olSection.querySelector('#ol-kpis');
  const kpiOlRmse = createKpiCard({ value: '—', label: 'RMSE (multi-step)', unit: '' });
  const kpiOlMae = createKpiCard({ value: '—', label: 'MAE', unit: '' });
  olKpiGrid.appendChild(kpiOlRmse);
  olKpiGrid.appendChild(kpiOlMae);

  const olChart = new TimeSeriesChart(olSection.querySelector('[data-chart="temp"]'), {
    title: 'MULTI-STEP OPEN-LOOP SIMULATION', yLabel: '°C', height: 260,
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
  // Collapsed by default (it can grow long) using the shared collapsible
  // primitive — same expand/collapse design as the Experiment Scheduler.
  const historySection = document.createElement('div');
  historySection.className = 'card tuning-section';
  const historyCollapsible = createCollapsible({ title: 'Applied Model History', open: false });
  historyCollapsible.body.innerHTML = `
    <p class="tuning-section__desc" style="margin:0 0 12px">
      Previously applied parameter sets. Load one back into the fields above to
      review and re-apply it, or delete entries you no longer need.
    </p>
    <div id="param-history-list"></div>
  `;
  historySection.appendChild(historyCollapsible.element);
  // historySection is appended after setupDatasetsAndExperiments() so that
  // Stored Datasets appears above Applied Model History in the page.

  const historyListEl = historyCollapsible.element.querySelector('#param-history-list');

  // -----------------------------------------------------------------------
  // Input references
  // -----------------------------------------------------------------------
  const thermalMassInput = container.querySelector('#param-thermal-mass');
  const rExternalInput = container.querySelector('#param-r-external');
  const internalGainInput = container.querySelector('#param-internal-gain');
  const solarScaleInput = container.querySelector('#param-solar-scale');
  const cAirFractionInput = container.querySelector('#param-c-air-fraction');
  const rAwFractionInput = container.querySelector('#param-r-aw-fraction');
  const tWallInitialInput = container.querySelector('#param-t-wall-initial');
  const uaOpenInput = container.querySelector('#param-ua-open');
  const interRoomRSubsection = container.querySelector('#inter-room-r-subsection');
  const interRoomRList = container.querySelector('#inter-room-r-list');
  const sigmaWInput = container.querySelector('#param-sigma-w');
  const sigmaVInput = container.querySelector('#param-sigma-v');
  const horizonInput = container.querySelector('#param-horizon');
  const windowModeRecentBtn = container.querySelector('#window-mode-recent');
  const windowModeCustomBtn = container.querySelector('#window-mode-custom');
  const windowPanelRecent = container.querySelector('#window-panel-recent');
  const windowPanelCustom = container.querySelector('#window-panel-custom');
  const windowStartInput = container.querySelector('#param-window-start');
  const windowEndInput = container.querySelector('#param-window-end');
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

  // Tracks locked parameters (those held fixed during parameter estimation).
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
      btnEl.title = 'Unlock: allow parameter estimation to vary this parameter';
    } else {
      btnEl.innerHTML = `${_ICON_OPEN} Fix`;
      btnEl.title = 'Lock: hold fixed during parameter estimation';
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
    if (lockedParams.has('t_wall_initial')) {
      const twVal = parseFloat(tWallInitialInput.value);
      if (isFinite(twVal)) {
        result.t_wall_initial = { [roomSlug]: twVal };
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

  // Tracks whether the user has begun a manual parameter-estimation process (i.e.
  // changed any parameter). Once true, the reactive update() callback stops
  // overwriting the form from system state, so running a reconstruction or
  // open-loop simulation — which triggers a state update — cannot reset the
  // parameters the user is testing. It is reset only when the page is
  // re-created (navigation / page refresh) or after the user commits or
  // reverts parameters.
  let userEditing = false;
  let appliedParams = null;

  function captureAppliedParams(st) {
    const filteredAttrs = st[filteredEntityId(roomSlug)]?.attributes || {};
    const configAttrs = st[CONFIG_ENTITY]?.attributes || {};
    const historyRooms = Array.isArray(configAttrs.parameter_history)
      ? (configAttrs.parameter_history[0]?.rooms || {})
      : {};
    const historyAttrs = historyRooms[roomSlug] || {};
    const modelAttrs = { ...historyAttrs, ...filteredAttrs };
    const heaterScales = {};
    const currentScales = configAttrs.current_heater_scales || {};
    for (const [srcName, info] of Object.entries(currentScales)) {
      if (info.room_slug === roomSlug && info.power_scale != null) {
        heaterScales[srcName] = info.power_scale;
      }
    }
    appliedParams = {
      thermal_mass: modelAttrs.thermal_mass ?? DEFAULTS.thermal_mass,
      r_external: modelAttrs.r_external ?? DEFAULTS.r_external,
      internal_gain: modelAttrs.internal_gain ?? DEFAULTS.internal_gain,
      solar_scale: modelAttrs.solar_scale ?? DEFAULTS.solar_scale,
      c_air_fraction: modelAttrs.c_air_fraction ?? DEFAULTS.c_air_fraction,
      r_aw_fraction: modelAttrs.r_aw_fraction ?? DEFAULTS.r_aw_fraction,
      sigma_w: configAttrs.sigma_w ?? DEFAULTS.sigma_w,
      sigma_v: configAttrs.sigma_v ?? DEFAULTS.sigma_v,
      horizon_hours: configAttrs.parameter_estimation_horizon_hours ?? DEFAULTS.horizon_hours,
      heater_scales: heaterScales,
    };
  }

  function collectCurrentParams() {
    const heaterScales = {};
    for (const [srcName, inp] of Object.entries(heaterScaleInputs)) {
      const val = parseFloat(inp.value);
      if (isFinite(val)) heaterScales[srcName] = val;
    }
    return {
      thermal_mass: parseFloat(thermalMassInput.value),
      r_external: parseFloat(rExternalInput.value),
      internal_gain: parseFloat(internalGainInput.value),
      solar_scale: parseFloat(solarScaleInput.value),
      c_air_fraction: parseFloat(cAirFractionInput.value),
      r_aw_fraction: parseFloat(rAwFractionInput.value),
      sigma_w: parseFloat(sigmaWInput.value),
      sigma_v: parseFloat(sigmaVInput.value),
      horizon_hours: parseFloat(horizonInput.value),
      heater_scales: heaterScales,
    };
  }

  function paramsDiffer(current, applied) {
    if (!applied) return false;
    const scalarKeys = [
      'thermal_mass', 'r_external', 'internal_gain', 'solar_scale',
      'c_air_fraction', 'r_aw_fraction', 'sigma_w', 'sigma_v', 'horizon_hours',
    ];
    if (scalarKeys.some((key) => !valuesEqual(current[key], applied[key]))) return true;
    const appliedScales = applied.heater_scales || {};
    const currentScales = current.heater_scales || {};
    const scaleKeys = new Set([...Object.keys(appliedScales), ...Object.keys(currentScales)]);
    return [...scaleKeys].some(
      (key) => !valuesEqual(currentScales[key], appliedScales[key]),
    );
  }

  function hasPendingChanges() {
    return paramsDiffer(collectCurrentParams(), appliedParams);
  }

  function updatePendingBanner(pending) {
    pendingBanner.hidden = !pending;
    pendingBanner.replaceChildren();
    if (!pending) return;
    const text = document.createElement('span');
    text.textContent = 'Unsaved parameter changes — click Apply Parameters to save, or revert to the last applied values.';
    const revertBtn = document.createElement('button');
    revertBtn.type = 'button';
    revertBtn.className = 'btn btn--ghost btn--sm';
    revertBtn.textContent = 'Revert changes';
    revertBtn.addEventListener('click', revertToApplied);
    pendingBanner.append(text, revertBtn);
  }

  function updatePendingIndicators() {
    const pending = hasPendingChanges();
    updatePendingBanner(pending);
    if (!appliedParams) return;
    const current = collectCurrentParams();
    const scalarFields = [
      [thermalMassInput, 'thermal_mass'],
      [rExternalInput, 'r_external'],
      [internalGainInput, 'internal_gain'],
      [solarScaleInput, 'solar_scale'],
      [cAirFractionInput, 'c_air_fraction'],
      [rAwFractionInput, 'r_aw_fraction'],
      [sigmaWInput, 'sigma_w'],
      [sigmaVInput, 'sigma_v'],
      [horizonInput, 'horizon_hours'],
    ];
    for (const [input, key] of scalarFields) {
      input.classList.toggle(
        'form-input--modified',
        pending && !valuesEqual(current[key], appliedParams[key]),
      );
    }
    for (const [srcName, inp] of Object.entries(heaterScaleInputs)) {
      inp.classList.toggle(
        'form-input--modified',
        pending && !valuesEqual(
          current.heater_scales[srcName],
          appliedParams.heater_scales?.[srcName],
        ),
      );
    }
  }

  function revertToApplied() {
    if (!appliedParams) return;
    thermalMassInput.value = appliedParams.thermal_mass;
    rExternalInput.value = appliedParams.r_external;
    internalGainInput.value = appliedParams.internal_gain;
    solarScaleInput.value = appliedParams.solar_scale;
    cAirFractionInput.value = appliedParams.c_air_fraction;
    rAwFractionInput.value = appliedParams.r_aw_fraction;
    sigmaWInput.value = appliedParams.sigma_w;
    sigmaVInput.value = appliedParams.sigma_v;
    horizonInput.value = appliedParams.horizon_hours;
    for (const [srcName, inp] of Object.entries(heaterScaleInputs)) {
      const scale = appliedParams.heater_scales?.[srcName];
      if (scale != null) inp.value = scale;
    }
    userEditing = false;
    updatePendingIndicators();
    setStatus(actionStatusEl, '', '');
  }

  paramInputs.forEach((inp) => {
    inp.addEventListener('input', () => {
      userEditing = true;
      updatePendingIndicators();
    });
  });

  // When a stored dataset is selected for identification, its id is sent with
  // the EKF reconstruction / open-loop / parameter estimation calls so they run
  // over the dataset's permanently snapshotted records. Cleared whenever the
  // user edits the parameter-estimation window manually so the selection stays
  // explicit. ``renderDatasetSelection`` is assigned by the dataset section
  // setup below; it updates the "using dataset …" note.
  let selectedDatasetId = null;
  let renderDatasetSelection = () => {};
  const setSelectedDataset = (id, label) => {
    selectedDatasetId = id;
    renderDatasetSelection(label || '');
    refreshAuxFromWindow();
  };
  const clearSelectedDataset = () => {
    if (selectedDatasetId) {
      selectedDatasetId = null;
      renderDatasetSelection('');
      refreshAuxFromWindow();
    }
  };

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

  windowModeRecentBtn.addEventListener('click', () => { _applyWindowMode('recent'); clearSelectedDataset(); });
  windowModeCustomBtn.addEventListener('click', () => _applyWindowMode('custom'));

  // Persist datetime values on change so they survive navigation.
  windowStartInput.addEventListener('change', () => {
    try { localStorage.setItem(_WINDOW_START_KEY, windowStartInput.value); } catch (_) {}
    userEditing = true;
    clearSelectedDataset();
  });
  windowEndInput.addEventListener('change', () => {
    try { localStorage.setItem(_WINDOW_END_KEY, windowEndInput.value); } catch (_) {}
    userEditing = true;
    clearSelectedDataset();
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
      clearSelectedDataset();
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
    if (selectedDatasetId) params.dataset_id = selectedDatasetId;
    if (lockedParams.has('t_wall_initial')) {
      const twVal = parseFloat(tWallInitialInput.value);
      if (isFinite(twVal)) {
        params[`t_wall_initial_${roomSlug}`] = twVal;
        params.t_wall_locked = true;
      }
    }
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
  // Fall back to parameter_history[0] when filtered attrs are missing (e.g. older
  // App builds that never published thermal attrs on temperature_filtered).
  function populateFromState(slug, st) {
    const filteredAttrs = st[filteredEntityId(slug)]?.attributes || {};
    const configAttrs = st[CONFIG_ENTITY]?.attributes || {};
    const historyRooms = Array.isArray(configAttrs.parameter_history)
      ? (configAttrs.parameter_history[0]?.rooms || {})
      : {};
    const historyAttrs = historyRooms[slug] || {};
    const modelAttrs = { ...historyAttrs, ...filteredAttrs };

    if (modelAttrs.thermal_mass != null) thermalMassInput.value = modelAttrs.thermal_mass;
    if (modelAttrs.r_external != null) rExternalInput.value = modelAttrs.r_external;
    if (modelAttrs.internal_gain != null) internalGainInput.value = modelAttrs.internal_gain;
    if (modelAttrs.solar_scale != null) solarScaleInput.value = modelAttrs.solar_scale;
    if (modelAttrs.c_air_fraction != null) cAirFractionInput.value = modelAttrs.c_air_fraction;
    if (modelAttrs.r_aw_fraction != null) rAwFractionInput.value = modelAttrs.r_aw_fraction;

    if (configAttrs.sigma_w != null) sigmaWInput.value = configAttrs.sigma_w;
    if (configAttrs.sigma_v != null) sigmaVInput.value = configAttrs.sigma_v;
    // Horizon is now persisted in the config entity (set by Apply Parameters).
    // Fall back to sysid sensor's last-run horizon if the config hasn't been
    // saved yet (e.g. on first use before Apply Parameters is clicked).
    if (configAttrs.parameter_estimation_horizon_hours != null) {
      horizonInput.value = configAttrs.parameter_estimation_horizon_hours;
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
    if (!userEditing) {
      captureAppliedParams(st);
    }
    updatePendingIndicators();
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
    // current_heater_scales is null/undefined until the config sensor reports.
    // Once it's an object (even {}) the config is loaded.
    const rawScales = configAttrs.current_heater_scales;
    if (rawScales == null) {
      // Config not loaded yet — try again on the next update.
      return;
    }
    const currentScales = rawScales;
    const sources = Object.entries(currentScales).filter(
      ([, info]) => info.room_slug === roomSlug
    );
    if (sources.length === 0) {
      // Config is loaded but no heaters for this room — mark as done so we
      // don't keep re-running on every state update.
      listEl.innerHTML = '<span class="form-hint">No heaters configured for this room.</span>';
      heaterInputsBuilt = true;
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
          <button class="param-lock-btn" title="Lock: hold fixed during parameter estimation">Fix</button>
        </div>
        <input class="form-input" type="number" id="${inputId}"
          step="0.01" min="0" value="${info.power_scale ?? DEFAULTS.heater_scale}">
        <span class="form-hint">&times; &mdash; heater power scale (1.0 = rated power)</span>
      `;
      listEl.appendChild(group);
      const inp = group.querySelector('input');
      const lockBtn = group.querySelector('.param-lock-btn');
      heaterScaleInputs[srcName] = inp;
      inp.addEventListener('input', () => {
        userEditing = true;
        updatePendingIndicators();
      });
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
  function formatRmseKpi(val) {
    return val != null ? formatNumber(val, 3) + ' °C' : '—';
  }

  function renderIdentifiedExtras(slug, st) {
    const sysidAttrs = st[sysidEntityId(slug)]?.attributes || {};
    if (sysidAttrs.t_wall_initial != null && !lockedParams.has('t_wall_initial')) {
      tWallInitialInput.value = formatNumber(sysidAttrs.t_wall_initial, 2);
    } else if (sysidAttrs.t_wall_initial == null && !lockedParams.has('t_wall_initial')) {
      tWallInitialInput.value = '—';
    }
    if (sysidAttrs.ua_open != null) {
      uaOpenInput.value = formatNumber(sysidAttrs.ua_open, 2);
    } else {
      uaOpenInput.value = '—';
    }

    const connections = sysidAttrs.estimated_inter_room_r;
    if (connections && typeof connections === 'object' && Object.keys(connections).length > 0) {
      interRoomRSubsection.hidden = false;
      interRoomRList.innerHTML = Object.entries(connections).map(([pair, rVal]) => {
        const [roomA, roomB] = pair.split(':');
        const other = roomA === room.name ? roomB
          : (roomB === room.name ? roomA : pair.replace(':', ' \u2194 '));
        const inputId = `inter-room-r-${pair.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
        return `
          <div class="form-group">
            <label class="form-label" for="${inputId}">${other}</label>
            <input class="form-input form-input--readonly" type="text" id="${inputId}"
              readonly value="${formatNumber(rVal, 4)}" tabindex="-1">
            <span class="form-hint">K/W &mdash; thermal resistance to ${other}</span>
          </div>`;
      }).join('');
    } else {
      interRoomRSubsection.hidden = true;
      interRoomRList.innerHTML = '';
    }
  }

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
    renderIdentifiedExtras(slug, st);
  }

  function renderEkfResults(slug, st) {
    const attrs = st[sysidEntityId(slug)]?.attributes || {};
    const rmseStr = formatRmseKpi(attrs.rmse);
    updateKpiCard(kpiEkfRmse, { value: rmseStr });
    updateKpiCard(kpiCompareEkfRmse, { value: rmseStr });
    updateKpiCard(kpiEkfMae, { value: attrs.mae != null ? formatNumber(attrs.mae, 3) + ' °C' : '—' });
    buildEkfChart(ekfChart, attrs.simulation);
    applySimulatedTw0(attrs);
  }

  function renderOlResults(slug, st) {
    const attrs = st[openLoopEntityId(slug)]?.attributes || {};
    const rmseStr = formatRmseKpi(attrs.open_loop_rmse);
    updateKpiCard(kpiOlRmse, { value: rmseStr });
    updateKpiCard(kpiCompareOlRmse, { value: rmseStr });
    updateKpiCard(kpiOlMae, { value: attrs.open_loop_mae != null ? formatNumber(attrs.open_loop_mae, 3) + ' °C' : '—' });
    buildOlChart(olChart, attrs.simulation);
    applySimulatedTw0(attrs);
  }

  function applySimulatedTw0(attrs) {
    if (lockedParams.has('t_wall_initial')) return;
    if (attrs?.t_wall_initial == null) return;
    tWallInitialInput.value = formatNumber(attrs.t_wall_initial, 2);
  }

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

  // Plot heater power and disturbances from identification history (dataset or
  // selected window). Simulation attributes are used only after a completed
  // run; window/dataset changes fetch getPeInputs so stale sim series cannot
  // hide the current window.
  function isoSeriesToPoints(series) {
    if (!Array.isArray(series)) return [];
    return series.map((entry) => {
      const t = new Date(entry.time).getTime();
      const y = Number(entry.value);
      if (!isFinite(t) || !isFinite(y)) return null;
      return { x: t, y };
    }).filter(Boolean);
  }

  function peInputOpts() {
    if (selectedDatasetId) return { roomSlug, datasetId: selectedDatasetId };
    if (windowMode === 'custom' && windowStartInput.value && windowEndInput.value) {
      const startTs = new Date(windowStartInput.value).getTime() / 1000;
      const endTs = new Date(windowEndInput.value).getTime() / 1000;
      if (isFinite(startTs) && isFinite(endTs) && startTs < endTs) {
        return { roomSlug, windowStart: startTs, windowEnd: endTs };
      }
    }
    const hrs = parseFloat(horizonInput.value);
    return { roomSlug, horizonHours: isFinite(hrs) ? hrs : DEFAULTS.horizon_hours };
  }

  function paintAuxCharts(inputsChart, disturbChart, series, xRange) {
    const powerPts = isoSeriesToPoints(series?.heating_power);
    const outdoorPts = isoSeriesToPoints(series?.outdoor_temp);
    const solarPts = isoSeriesToPoints(series?.solar_gain);
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

  async function renderAuxPlots(inputsChart, disturbChart, horizonHours, xRange, simAttrs) {
    if (simAttrs && Array.isArray(simAttrs.heating_power) && simAttrs.heating_power.length) {
      paintAuxCharts(inputsChart, disturbChart, simAttrs, xRange);
      return;
    }
    if (connection && typeof connection.getPeInputs === 'function') {
      try {
        const series = await connection.getPeInputs(peInputOpts());
        if (series && Array.isArray(series.heating_power) && series.heating_power.length) {
          paintAuxCharts(inputsChart, disturbChart, series, xRange);
          return;
        }
      } catch (err) {
        // Fall through to HA recorder history.
      }
    }

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

    paintAuxCharts(inputsChart, disturbChart, {
      heating_power: (hist[powerEntity] || []).map((entry) => ({
        time: entry.last_changed || entry.last_updated,
        value: entry.s !== undefined ? entry.s : entry.state,
      })),
      outdoor_temp: (hist[outdoorEntity] || []).map((entry) => ({
        time: entry.last_changed || entry.last_updated,
        value: entry.s !== undefined ? entry.s : entry.state,
      })),
      solar_gain: (hist[solarEntity] || []).map((entry) => ({
        time: entry.last_changed || entry.last_updated,
        value: entry.s !== undefined ? entry.s : entry.state,
      })),
    }, xRange);
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

  function renderEkfAux(useSimAttrs = true) {
    const attrs = latestState[sysidEntityId(roomSlug)]?.attributes || {};
    return renderAuxPlots(
      ekfInputsChart,
      ekfDisturbChart,
      _effectiveHorizon(attrs),
      _effectiveXRange(useSimAttrs ? attrs : null),
      useSimAttrs ? attrs : null,
    );
  }

  function renderOlAux(useSimAttrs = true) {
    const attrs = latestState[openLoopEntityId(roomSlug)]?.attributes || {};
    return renderAuxPlots(
      olInputsChart,
      olDisturbChart,
      _effectiveHorizon(attrs),
      _effectiveXRange(useSimAttrs ? attrs : null),
      useSimAttrs ? attrs : null,
    );
  }

  function refreshAuxFromWindow() {
    renderEkfAux(false);
    renderOlAux(false);
  }

  // Populate the editable parameter fields from a stored history entry's room
  // data, for review before Apply. Mirrors the dataset "Load" affordance but for
  // parameter snapshots — it never applies anything to the live model.
  function loadParamsFromHistory(roomData) {
    if (roomData.thermal_mass != null) thermalMassInput.value = roomData.thermal_mass;
    if (roomData.r_external != null) rExternalInput.value = roomData.r_external;
    if (roomData.internal_gain != null) internalGainInput.value = roomData.internal_gain;
    if (roomData.solar_scale != null) solarScaleInput.value = roomData.solar_scale;
    if (roomData.c_air_fraction != null) cAirFractionInput.value = roomData.c_air_fraction;
    if (roomData.r_aw_fraction != null) rAwFractionInput.value = roomData.r_aw_fraction;
    // Loaded values are pending review; protect them from state-sync resets.
    userEditing = true;
    updatePendingIndicators();
    setStatus(actionStatusEl, 'Loaded from history — review the fields above, then click Apply Parameters.', '');
  }

  function renderParamHistory(st) {
    const config = st[CONFIG_ENTITY]?.attributes || {};

    // parameter_history is a LIST of full-system snapshots (most recent first).
    // Each entry: { rooms: { "room_slug": { thermal_mass, r_external, ... } },
    //               estimated_at, source, rmse? }
    const allHistory = config.parameter_history || [];
    historyCollapsible.setBadge(allHistory.length ? `${allHistory.length}` : '');
    if (allHistory.length === 0) {
      historyListEl.innerHTML = '<span class="tuning-section__desc">No applied parameter sets yet.</span>';
      return;
    }

    const stat = (label, value) => `<span class="store-stat"><span class="store-stat__k">${label}</span><span class="store-stat__v">${value}</span></span>`;

    historyListEl.innerHTML = `<div class="store-list">${allHistory.map((entry, i) => {
      const roomData = entry.rooms?.[roomSlug] || {};
      const date = entry.estimated_at
        ? new Date(entry.estimated_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : '—';
      const src = (entry.source || 'manual').toLowerCase();
      const srcLabel = src === 'ml' ? 'ML' : (src.charAt(0).toUpperCase() + src.slice(1));
      const mass = roomData.thermal_mass != null ? formatMass(roomData.thermal_mass) : '—';
      const rExt = roomData.r_external != null ? formatNumber(roomData.r_external, 4) : '—';
      const gain = roomData.internal_gain != null ? `${formatNumber(roomData.internal_gain, 0)} W` : '—';
      const solar = roomData.solar_scale != null ? `${formatNumber(roomData.solar_scale, 2)}×` : '—';
      const rmse = entry.rmse != null ? `${formatNumber(entry.rmse, 3)} °C` : '—';
      const hasRoom = roomData.thermal_mass != null;
      return `
        <div class="store-row store-row--param" data-idx="${i}">
          <div class="store-row__main">
            <div class="store-row__name">
              <span class="store-row__index">#${i + 1}</span>
              <span class="store-row__title">${date}</span>
              <span class="store-row__tag store-row__tag--${src === 'ml' ? 'accent' : (src === 'reverted' ? 'warn' : '')}">${srcLabel}</span>
            </div>
            <div class="store-row__meta">
              ${stat('C', mass)}${stat('R', rExt)}${stat('Q', gain)}${stat('Solar', solar)}${stat('RMSE', rmse)}
            </div>
          </div>
          <div class="store-row__actions">
            <button class="btn btn--sm btn--ghost" data-load="${i}" ${hasRoom ? '' : 'disabled'}>Load</button>
            <button class="btn btn--ghost btn--sm store-row__del" data-del="${i}">Delete</button>
          </div>
        </div>`;
    }).join('')}</div>`;

    historyListEl.querySelectorAll('[data-load]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const entry = allHistory[parseInt(btn.dataset.load, 10)];
        if (entry) loadParamsFromHistory(entry.rooms?.[roomSlug] || {});
      });
    });
    historyListEl.querySelectorAll('[data-del]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (!window.confirm('Delete this parameter history entry? This cannot be undone.')) return;
        btn.disabled = true;
        try {
          await deleteParameterHistory(hass, parseInt(btn.dataset.del, 10));
          // The config sensor updates and update() re-renders the list.
        } catch (err) {
          btn.disabled = false;
        }
      });
    });
  }

  // -----------------------------------------------------------------------
  // Button interactions
  // -----------------------------------------------------------------------

  // Shared parameter estimation routine: runs a dry-run ML estimation over the
  // data described by ``idData`` (a window, horizon, single dataset_id or a
  // list of dataset_ids), then populates the parameter fields from the result
  // for review. Used by the datasets section's "Run on Selected" button.
  // Returns true on success.
  async function runAutoIdentification(idData, statusEl) {
    setStatus(statusEl, 'Running parameter estimation…', 'running');
    try {
      const lp = buildLockedParams();
      await estimateParametersMl(hass, {
        apply_parameters: false,
        ...idData,
        ...(lp ? { locked_params: lp } : {}),
      });
      // The coordinator updates sysid_results and fires async_update_listeners()
      // before the service call resolves.  Allow ~800 ms for the HA websocket
      // state event to arrive and update latestState via the update() callback.
      await new Promise((res) => setTimeout(res, 800));
      populateModelFromSysid(roomSlug, latestState);
      // Loaded values are pending review; protect them from state-sync resets.
      userEditing = true;
      updatePendingIndicators();
      setStatus(statusEl, 'Loaded — review the fields below, then click Apply Parameters.', '');
      return true;
    } catch (err) {
      setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
      return false;
    }
  }

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
      const uaOpen = parseFloat(uaOpenInput.value);
      await storeIdentifiedParameters(hass, {
        room_name: roomSlug,
        thermal_mass: parseFloat(thermalMassInput.value),
        r_external: parseFloat(rExternalInput.value),
        internal_gain: parseFloat(internalGainInput.value),
        solar_scale: parseFloat(solarScaleInput.value),
        c_air_fraction: parseFloat(cAirFractionInput.value),
        r_aw_fraction: parseFloat(rAwFractionInput.value),
        ...(Number.isFinite(uaOpen) ? { ua_open: uaOpen } : {}),
        ...(Object.keys(heaterScales).length ? { heater_scales: heaterScales } : {}),
        source: 'manual',
      });
      await updateEstimationParams(hass, {
        sigma_w: parseFloat(sigmaWInput.value),
        sigma_v: parseFloat(sigmaVInput.value),
        parameter_estimation_horizon_hours: parseFloat(horizonInput.value),
      });
      // Edits are now the applied parameters; resume syncing the form from
      // system state so it reflects the authoritative committed values.
      userEditing = false;
      appliedParams = collectCurrentParams();
      updatePendingIndicators();
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
    updatePendingIndicators();
    setStatus(actionStatusEl, 'Defaults loaded.', '');
  });

  // EKF Reconstruction: run with the current parameter field values.
  btnSysid.addEventListener('click', async () => {
    setStatus(ekfStatusEl, 'Running one-step EKF reconstruction…', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await runSysidSimulation(hass, collectSimParams());
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
    setStatus(olStatusEl, 'Running multi-step open-loop simulation…', 'running');
    btnSysid.disabled = true;
    btnOpenLoop.disabled = true;
    try {
      await runOpenLoopSimulation(hass, {
        ...collectSimParams(),
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
  // Section 5: Stored datasets
  // -----------------------------------------------------------------------
  const refreshHandles = setupDatasetsAndExperiments({
    container, paramsCard, room, roomSlug, hass, connection,
    windowStartInput, windowEndInput, horizonInput, applyWindowMode: _applyWindowMode,
    getWindowMode: () => windowMode,
    toDatetimeLocal: _toDatetimeLocal,
    setSelectedDataset, clearSelectedDataset,
    onDatasetSelectionRenderer: (fn) => { renderDatasetSelection = fn; },
    getSelectedDatasetId: () => selectedDatasetId,
    runAutoIdentification,
    onAuxRefresh: refreshAuxFromWindow,
  });
  const refreshCoverage = () => {
    if (refreshHandles && typeof refreshHandles.refreshCoverage === 'function') {
      refreshHandles.refreshCoverage();
    }
  };
  windowStartInput.addEventListener('change', refreshCoverage);
  windowEndInput.addEventListener('change', refreshCoverage);
  horizonInput.addEventListener('change', refreshCoverage);
  windowModeRecentBtn.addEventListener('click', refreshCoverage);
  windowModeCustomBtn.addEventListener('click', refreshCoverage);
  windowStartInput.addEventListener('change', refreshAuxFromWindow);
  windowEndInput.addEventListener('change', refreshAuxFromWindow);
  horizonInput.addEventListener('change', refreshAuxFromWindow);
  windowModeRecentBtn.addEventListener('click', refreshAuxFromWindow);
  windowModeCustomBtn.addEventListener('click', refreshAuxFromWindow);

  // Stored Datasets section is already appended inside setupDatasetsAndExperiments();
  // append Applied Model History here so it appears below Stored Datasets.
  container.appendChild(historySection);

  // -----------------------------------------------------------------------
  // Initial render
  // -----------------------------------------------------------------------
  populateFromState(roomSlug, state);
  renderIdentifiedExtras(roomSlug, state);
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
      // parameter-estimation process and while no field is focused. Once the user has
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
        updatePendingIndicators();
      }
      renderEkfResults(roomSlug, newState);
      renderOlResults(roomSlug, newState);
      renderIdentifiedExtras(roomSlug, newState);
      renderParamHistory(newState);
    },
    destroy() {
      ekfChart.destroy();
      ekfInputsChart.destroy();
      ekfDisturbChart.destroy();
      olChart.destroy();
      olInputsChart.destroy();
      olDisturbChart.destroy();
      if (refreshHandles && refreshHandles.destroy) refreshHandles.destroy();
    },
  };
}
