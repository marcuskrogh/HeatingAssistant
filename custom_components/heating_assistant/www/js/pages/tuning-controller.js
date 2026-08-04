import {
  TimeSeriesChart,
  forecastToDataPoints,
  forecastToEnabledPoints,
  loadChartJs,
} from '../components/time-series-chart.js?v=116';
import {
  buildTemperatureChart,
  buildPowerChart,
  buildDisturbanceChart,
} from '../charts/mpc-preview-charts.js?v=116';
import {
  updateControllerTuning,
  updateEstimationParams,
} from '../ha-services.js?v=116';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

const MODE_DEF = {
  key: 'mpc_mode',
  label: 'MPC Mode',
  hint: 'Linear is faster and robust; non-linear can improve model fidelity/accuracy at higher compute cost.',
  parse: String,
};

const MODE_OPTIONS = [
  { value: 'linear', label: 'Linear' },
  { value: 'non-linear', label: 'Non-linear' },
];

// Parameters that take effect on the live controller without rebuilding its structure.
const LIVE_PARAM_DEFS = [
  { key: 'comfort_offset', label: 'Comfort Offset', unit: '°C', hint: 'Symmetric band around setpoint', step: 0.1, parse: parseFloat },
  { key: 'tracking_weight', label: 'Tracking Weight', unit: '', hint: 'Setpoint tracking strength (0 = band only)', step: 0.1, parse: parseFloat },
  { key: 'energy_weight', label: 'Energy Weight', unit: '', hint: 'Energy-use penalty', step: 0.01, parse: parseFloat },
  { key: 'energy_price_weight', label: 'Price Sensitivity', unit: '', hint: 'Electricity price cost scaling', step: 0.1, parse: parseFloat },
  { key: 'smoothing_weight', label: 'Output Smoothing', unit: '', hint: 'Penalises rapid output changes', step: 0.05, parse: parseFloat },
  { key: 'soft_constraint_weight', label: 'Comfort Band Penalty (quadratic)', unit: '', hint: 'Quadratic penalty for leaving comfort zone', step: 1, parse: parseFloat },
  { key: 'soft_constraint_linear_weight', label: 'Comfort Band Penalty (linear)', unit: '', hint: 'Linear penalty for comfort-band violations (0 = disabled)', step: 1, parse: parseFloat },
  { key: 'terminal_weight', label: 'Terminal Weight', unit: '', hint: 'End-of-horizon constraint', step: 1, parse: parseFloat },
];

// Changing these rebuilds the MPC problem (sample interval / prediction horizon).
const RESTART_PARAM_DEFS = [
  { key: 'update_interval', label: 'Sample Interval', unit: 's', hint: 'Re-planning cadence — changing this rebuilds the controller', step: 30, parse: parseInt },
  { key: 'horizon', label: 'Prediction Horizon', unit: 'steps', hint: 'Control intervals planned ahead — changing this rebuilds the controller', step: 1, parse: parseInt },
];

const PARAM_DEFS = [MODE_DEF, ...LIVE_PARAM_DEFS, ...RESTART_PARAM_DEFS];
const RESTART_CHANGE_DEFS = [MODE_DEF, ...RESTART_PARAM_DEFS];

// Must match backend DEFAULT_* constants in const.py
const DEFAULTS = {
  mpc_mode: 'linear',
  update_interval: 900,
  comfort_offset: 2.0,
  horizon: 100,
  tracking_weight: 0.0,
  energy_weight: 0.01,
  energy_price_weight: 1.0,
  smoothing_weight: 0.1,
  soft_constraint_weight: 10.0,
  soft_constraint_linear_weight: 0.0,
  terminal_weight: 100.0,
};

const WINDOW_DEFS = [
  { key: 'window_open_debounce', label: 'Window Open Debounce', unit: 's', hint: 'Debounce before confirming window open', step: 10, parse: parseInt },
  { key: 'window_open_close_settle', label: 'Window Close Settle', unit: 's', hint: 'Settle time after window closes before resuming', step: 10, parse: parseInt },
  { key: 'window_open_q_inflation', label: 'Uncertainty Multiplier', unit: '×', hint: 'Covariance inflation when window is open', step: 1, parse: parseFloat },
];

// Must match backend DEFAULT_WINDOW_* constants in const.py
const WINDOW_DEFAULTS = {
  window_open_debounce: 60,
  window_open_close_settle: 30,
  window_open_q_inflation: 10.0,
};

const ALL_PARAM_DEFS = [...PARAM_DEFS, ...WINDOW_DEFS];
const ALL_DEFAULTS = { ...DEFAULTS, ...WINDOW_DEFAULTS };

export function renderControllerTuning(container, rooms, _state, connection, hass) {
  return renderTuningIndex(container, rooms, connection, hass);
}

function valuesEqual(a, b) {
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) {
    return Math.abs(na - nb) <= 1e-9 * Math.max(1, Math.abs(na), Math.abs(nb));
  }
  return a === b;
}

function renderTuningIndex(container, rooms, connection, hass) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'CONTROLLER TUNING';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Adjust MPC controller and window-detection settings. Preview the planned trajectories before applying changes to the live controller.';
  container.appendChild(desc);

  const pendingBanner = document.createElement('div');
  pendingBanner.className = 'tuning-pending-banner';
  pendingBanner.hidden = true;
  container.appendChild(pendingBanner);

  // --- Unified action bar ---
  const actionsRow = document.createElement('div');
  actionsRow.className = 'tuning-actions';
  actionsRow.innerHTML = `
    <button class="btn btn--primary tuning-actions__btn" id="btn-apply-all">Apply Changes</button>
    <button class="btn btn--secondary tuning-actions__btn" id="btn-reset-all">Reset to Defaults</button>
    <span class="tuning-actions__status" id="tuning-status"></span>
  `;
  container.appendChild(actionsRow);

  // --- MPC Parameter section ---
  const formSection = document.createElement('div');
  formSection.className = 'card tuning-section';

  const mpcTitle = document.createElement('div');
  mpcTitle.className = 'tuning-section__title';
  mpcTitle.textContent = 'MPC Controller Parameters';
  formSection.appendChild(mpcTitle);

  const inputs = {};
  let nonlinearOption = null;
  let modeHint = null;

  const modeGroup = document.createElement('div');
  modeGroup.className = 'form-group';
  modeGroup.innerHTML = `
    <label class="form-label" for="ctrl-${MODE_DEF.key}">${MODE_DEF.label}</label>
    <select class="form-input" id="ctrl-${MODE_DEF.key}">
      ${MODE_OPTIONS.map((opt) => `<option value="${opt.value}">${opt.label}</option>`).join('')}
    </select>
    <span class="form-hint">${MODE_DEF.hint}</span>
    <span class="form-hint" id="ctrl-mpc-mode-ipopt"></span>
  `;
  formSection.appendChild(modeGroup);
  inputs[MODE_DEF.key] = modeGroup.querySelector('select');
  nonlinearOption = inputs[MODE_DEF.key].querySelector('option[value="non-linear"]');
  modeHint = modeGroup.querySelector('#ctrl-mpc-mode-ipopt');

  function appendParamSubsection(title, description, defs) {
    const subsection = document.createElement('div');
    subsection.className = 'params-subsection';
    subsection.innerHTML = `
      <div class="params-subsection__title">${title}</div>
      <p class="tuning-section__desc params-subsection__desc">${description}</p>
    `;
    const grid = document.createElement('div');
    grid.className = 'tuning-params-grid tuning-params-grid--wide';
    subsection.appendChild(grid);
    formSection.appendChild(subsection);
    for (const def of defs) {
      const group = document.createElement('div');
      group.className = 'form-group';
      group.innerHTML = `
        <label class="form-label" for="ctrl-${def.key}">${def.label}</label>
        <input class="form-input" type="number" id="ctrl-${def.key}"
          step="${def.step}" value="">
        <span class="form-hint">${def.unit ? def.unit + ' — ' : ''}${def.hint}</span>
      `;
      grid.appendChild(group);
      inputs[def.key] = group.querySelector('input');
    }
  }

  appendParamSubsection(
    'Live tuning',
    'Penalty weights and comfort band — applied to the running controller on the next planning cycle.',
    LIVE_PARAM_DEFS,
  );
  appendParamSubsection(
    'Restart required',
    'Sample interval and prediction horizon — the MPC problem is rebuilt when these change.',
    RESTART_PARAM_DEFS,
  );
  container.appendChild(formSection);

  // --- Window Configuration section ---
  const windowSection = document.createElement('div');
  windowSection.className = 'card tuning-section';

  const windowTitle = document.createElement('div');
  windowTitle.className = 'tuning-section__title';
  windowTitle.textContent = 'Window Configuration';
  windowSection.appendChild(windowTitle);

  const windowDesc = document.createElement('p');
  windowDesc.className = 'tuning-section__desc';
  windowDesc.textContent = 'Global parameters for window open/close detection. Changes affect all rooms.';
  windowSection.appendChild(windowDesc);

  const windowGrid = document.createElement('div');
  windowGrid.className = 'tuning-params-grid';
  windowSection.appendChild(windowGrid);
  container.appendChild(windowSection);

  const windowInputs = {};
  for (const def of WINDOW_DEFS) {
    const group = document.createElement('div');
    group.className = 'form-group';
    group.innerHTML = `
      <label class="form-label" for="win-${def.key}">${def.label}</label>
      <input class="form-input" type="number" id="win-${def.key}"
        step="${def.step}" value="">
      <span class="form-hint">${def.unit ? def.unit + ' — ' : ''}${def.hint}</span>
    `;
    windowGrid.appendChild(group);
    windowInputs[def.key] = group.querySelector('input');
  }

  // --- Preview section ---
  const previewSection = document.createElement('div');
  previewSection.className = 'card tuning-section';
  previewSection.innerHTML = `
    <div class="tuning-section__title">Controller Preview</div>
    <p class="tuning-section__desc">
      Run a one-off solve with the configured MPC parameters and review the predicted trajectories for each room.
      The live controller is not affected.
    </p>
    <div class="tuning-actions">
      <button class="btn btn--accent tuning-actions__btn" id="btn-preview">Preview Controller Behaviour</button>
      <span class="tuning-actions__status" id="preview-status"></span>
    </div>
    <div class="room-selector" id="preview-room-selector" hidden></div>
    <div class="grid-charts" id="preview-charts" hidden>
      <div data-chart="temp"></div>
      <div data-chart="power"></div>
      <div data-chart="disturb"></div>
    </div>
  `;
  container.appendChild(previewSection);

  const btnApply = container.querySelector('#btn-apply-all');
  const btnReset = container.querySelector('#btn-reset-all');
  const btnPreview = container.querySelector('#btn-preview');
  const statusEl = container.querySelector('#tuning-status');
  const previewStatusEl = container.querySelector('#preview-status');
  const roomSelectorEl = container.querySelector('#preview-room-selector');
  const previewChartsEl = container.querySelector('#preview-charts');

  function setStatus(text, type = '') {
    statusEl.textContent = text;
    statusEl.className = 'tuning-actions__status';
    if (type) statusEl.classList.add(`tuning-actions__status--${type}`);
  }

  function setPreviewStatus(text, type = '') {
    previewStatusEl.textContent = text;
    previewStatusEl.className = 'tuning-actions__status';
    if (type) previewStatusEl.classList.add(`tuning-actions__status--${type}`);
  }

  let appliedConfig = null;
  let previewPayload = null;
  let selectedPreviewRoom = rooms[0]?.slug ?? null;
  let plotSettings = { forecastHours: 0 };

  const previewChartOpts = { forecastOnly: true };

  const previewCharts = {
    temp: new TimeSeriesChart(previewChartsEl.querySelector('[data-chart="temp"]'), {
      title: 'TEMPERATURE',
      yLabel: '\u00b0C',
      height: 240,
    }),
    power: new TimeSeriesChart(previewChartsEl.querySelector('[data-chart="power"]'), {
      title: 'HEATING POWER & PRICE',
      yLabel: 'kW',
      y2: true,
      y2Label: 'Price',
      height: 200,
    }),
    disturb: new TimeSeriesChart(previewChartsEl.querySelector('[data-chart="disturb"]'), {
      title: 'DISTURBANCES',
      yLabel: '\u00b0C',
      y2: true,
      y2Label: 'kW',
      height: 200,
    }),
  };

  function collectConfiguredConfig() {
    const cfg = {};
    for (const def of PARAM_DEFS) cfg[def.key] = def.parse(inputs[def.key].value);
    for (const def of WINDOW_DEFS) cfg[def.key] = def.parse(windowInputs[def.key].value);
    return cfg;
  }

  function collectMpcParams() {
    const mpcData = {};
    for (const def of PARAM_DEFS) mpcData[def.key] = def.parse(inputs[def.key].value);
    return mpcData;
  }

  function hasPendingChanges() {
    if (!appliedConfig) return false;
    const configured = collectConfiguredConfig();
    return ALL_PARAM_DEFS.some((def) => !valuesEqual(
      configured[def.key],
      appliedConfig[def.key] ?? ALL_DEFAULTS[def.key],
    ));
  }

  function hasPendingRestartChanges() {
    if (!appliedConfig) return false;
    const configured = collectConfiguredConfig();
    return RESTART_CHANGE_DEFS.some((def) => !valuesEqual(
      configured[def.key],
      appliedConfig[def.key] ?? ALL_DEFAULTS[def.key],
    ));
  }

  function updatePendingBanner(pending) {
    if (!pending) {
      pendingBanner.hidden = true;
      pendingBanner.classList.remove('tuning-pending-banner--restart');
      pendingBanner.textContent = '';
      return;
    }
    pendingBanner.hidden = false;
    const needsRestart = hasPendingRestartChanges();
    pendingBanner.classList.toggle('tuning-pending-banner--restart', needsRestart);
    pendingBanner.textContent = needsRestart
      ? 'Unsaved changes include MPC mode, sample interval, or prediction horizon — applying will rebuild the MPC controller.'
      : 'Unsaved changes — penalty weights and window settings apply to the live controller on the next cycle.';
  }

  function updatePendingIndicators() {
    const pending = hasPendingChanges();
    updatePendingBanner(pending);
    for (const def of ALL_PARAM_DEFS) {
      const input = inputs[def.key] ?? windowInputs[def.key];
      if (!input) continue;
      const configured = def.parse(input.value);
      const applied = appliedConfig?.[def.key] ?? ALL_DEFAULTS[def.key];
      input.classList.toggle('form-input--modified', pending && !valuesEqual(configured, applied));
    }
  }

  function populate(config) {
    updateModeAvailability(config);
    for (const def of PARAM_DEFS) {
      const val = config[def.key];
      if (val !== undefined && val !== null) inputs[def.key].value = val;
    }
    for (const def of WINDOW_DEFS) {
      const val = config[def.key];
      if (val !== undefined && val !== null) windowInputs[def.key].value = val;
    }
    updatePendingIndicators();
  }

  function populateDefaults() {
    for (const def of PARAM_DEFS) inputs[def.key].value = DEFAULTS[def.key];
    for (const def of WINDOW_DEFS) windowInputs[def.key].value = WINDOW_DEFAULTS[def.key];
    updatePendingIndicators();
  }

  function updateModeAvailability(config) {
    const available = config?.ipopt_available === true;
    if (nonlinearOption) nonlinearOption.disabled = !available;
    if (modeHint) {
      modeHint.textContent = available
        ? 'Non-linear mode is available (capability probe passed on the last restart).'
        : `Non-linear mode needs its solver backend. It will be selectable after a restart probe passes${config?.ipopt_unavailable_reason ? ` (${config.ipopt_unavailable_reason})` : ''}.`;
    }
  }

  function liveHass() {
    return connection._hass ?? hass;
  }

  function fromEntityState() {
    const states = liveHass()?.states ?? {};
    const direct = states[CONFIG_ENTITY];
    if (direct?.attributes?.tracking_weight !== undefined) return direct.attributes;
    for (const [id, s] of Object.entries(states)) {
      if (id.startsWith('sensor.heating_assistant_') && s?.attributes) {
        const a = s.attributes;
        if (a.tracking_weight !== undefined && a.update_interval !== undefined) return a;
      }
    }
    return null;
  }

  async function loadConfigFromConnection() {
    const cfg = await connection.getControllerConfig();
    if (cfg && typeof cfg === 'object' && Object.keys(cfg).length > 0) return cfg;
    if (cfg && typeof cfg === 'object') {
      console.warn('[TuningPage] WS returned empty config');
    }
    return null;
  }

  function applyConfig(cfg) {
    if (!cfg) return false;
    appliedConfig = { ...cfg };
    populate(cfg);
    setStatus('');
    return true;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let destroyed = false;

  const allParamInputs = [
    ...Object.values(inputs),
    ...Object.values(windowInputs),
  ];

  let userEditing = false;
  allParamInputs.forEach((inp) => {
    inp.addEventListener('input', () => {
      userEditing = true;
      updatePendingIndicators();
    });
  });

  async function loadConfig() {
    if (applyConfig(fromEntityState())) return;
    setStatus('Loading current values…', 'running');
    for (const delay of [0, 500, 1000, 2000, 4000]) {
      if (destroyed) return;
      if (delay) await sleep(delay);
      if (destroyed) return;
      if (applyConfig(await loadConfigFromConnection())) return;
      if (applyConfig(fromEntityState())) return;
    }
    setStatus(
      'Could not load current values. Check the browser console (F12) for details and ensure the Heating Assistant integration is running.',
      'error',
    );
  }

  function renderRoomSelector() {
    roomSelectorEl.innerHTML = '';
    if (!previewPayload?.rooms || rooms.length === 0) {
      roomSelectorEl.hidden = true;
      return;
    }
    roomSelectorEl.hidden = false;
    for (const room of rooms) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'room-selector__btn';
      btn.textContent = room.name;
      btn.dataset.slug = room.slug;
      if (room.slug === selectedPreviewRoom) btn.classList.add('room-selector__btn--active');
      btn.addEventListener('click', () => {
        selectedPreviewRoom = room.slug;
        renderRoomSelector();
        renderPreviewChartsForRoom(room.slug);
      });
      roomSelectorEl.appendChild(btn);
    }
  }

  function renderPreviewChartsForRoom(roomSlug) {
    const room = rooms.find((r) => r.slug === roomSlug);
    if (!room || !previewPayload) return;

    const roomForecast = previewPayload.rooms?.[roomSlug];
    const forecastData = roomForecast?.forecast || [];
    const priceForecastData = previewPayload.price_forecast || [];
    const forecastStart = forecastData.length
      ? new Date(forecastData[0].time).getTime()
      : Date.now();

    const tempForecastNonlinear = forecastToDataPoints(forecastData, 'temperature');
    const tempForecastLinearised = forecastToDataPoints(forecastData, 'linearised_temperature');
    const setpointForecast = forecastToEnabledPoints(forecastData, 'setpoint');
    const constraintUpperForecast = forecastToEnabledPoints(forecastData, 'constraint_upper');
    const constraintLowerForecast = forecastToEnabledPoints(forecastData, 'constraint_lower');
    const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
    const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
    const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temp');
    const priceForecast = forecastToDataPoints(priceForecastData, 'price');

    buildTemperatureChart(
      previewCharts.temp,
      [], [],
      [], setpointForecast,
      tempForecastNonlinear, tempForecastLinearised,
      [], constraintUpperForecast,
      [], constraintLowerForecast,
      null,
      previewChartOpts,
    );
    buildPowerChart(
      previewCharts.power,
      [],
      powerForecast,
      [],
      priceForecast,
      roomForecast,
      forecastStart,
      previewChartOpts,
    );
    buildDisturbanceChart(
      previewCharts.disturb,
      [],
      outdoorForecast,
      [],
      solarForecast,
      previewChartOpts,
    );
  }

  async function runPreview() {
    setPreviewStatus('Computing control plan…', 'running');
    btnPreview.disabled = true;
    previewChartsEl.hidden = true;
    roomSelectorEl.hidden = true;
    try {
      await loadChartJs();
      const uiSettings = await connection.getUiSettings();
      if (uiSettings) {
        const f = Number(uiSettings.plot_forecast_hours);
        if (Number.isFinite(f)) plotSettings.forecastHours = f;
      }

      const payload = await connection.previewTuningForecast(
        collectMpcParams(),
        plotSettings.forecastHours,
      );
      if (!payload) {
        setPreviewStatus('Preview failed — see browser console for details.', 'error');
        return;
      }
      if (payload.error === 'outdoor_temperature_unavailable') {
        setPreviewStatus('Outdoor temperature is unavailable. A current reading is required to run the preview.', 'error');
        return;
      }

      previewPayload = payload;

      if (!selectedPreviewRoom || !previewPayload.rooms?.[selectedPreviewRoom]) {
        selectedPreviewRoom = rooms.find((r) => previewPayload.rooms?.[r.slug])?.slug
          ?? Object.keys(previewPayload.rooms || {})[0]
          ?? null;
      }

      previewChartsEl.hidden = false;
      renderRoomSelector();
      if (selectedPreviewRoom) renderPreviewChartsForRoom(selectedPreviewRoom);
      setPreviewStatus('Preview complete. Select a room below to view its trajectories.', 'success');
    } catch (err) {
      setPreviewStatus('Error: ' + (err.message || err), 'error');
    } finally {
      btnPreview.disabled = false;
    }
  }

  btnApply.addEventListener('click', async () => {
    setStatus('Applying…', 'running');
    btnApply.disabled = true;
    try {
      const mpcData = collectMpcParams();
      await updateControllerTuning(hass, mpcData);

      const estData = {};
      for (const def of WINDOW_DEFS) estData[def.key] = def.parse(windowInputs[def.key].value);
      await updateEstimationParams(hass, estData);

      applyConfig(await loadConfigFromConnection()) || applyConfig(fromEntityState());
      userEditing = false;
      previewPayload = null;
      previewChartsEl.hidden = true;
      roomSelectorEl.hidden = true;
      setPreviewStatus('');
      setStatus('Applied successfully.', 'success');
    } catch (err) {
      setStatus('Error: ' + (err.message || err), 'error');
    }
    btnApply.disabled = false;
  });

  btnReset.addEventListener('click', () => {
    populateDefaults();
    userEditing = true;
    setStatus('Default values loaded — click Apply Changes to save.', '');
  });

  btnPreview.addEventListener('click', () => { runPreview(); });

  connection.getUiSettings().then((s) => {
    if (!s) return;
    const f = Number(s.plot_forecast_hours);
    if (Number.isFinite(f)) plotSettings.forecastHours = f;
  }).catch(() => {});

  loadConfig();

  return {
    update() {
      if (userEditing) return;
      const rootNode = container.getRootNode();
      const focused = (rootNode instanceof ShadowRoot ? rootNode : document).activeElement;
      if (allParamInputs.some((inp) => inp === focused)) return;
      const entityCfg = fromEntityState();
      if (entityCfg) {
        appliedConfig = { ...entityCfg };
        populate(entityCfg);
      }
    },
    destroy() {
      destroyed = true;
      previewCharts.temp.destroy();
      previewCharts.power.destroy();
      previewCharts.disturb.destroy();
    },
  };
}
