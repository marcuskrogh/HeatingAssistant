import {
  TimeSeriesChart,
  historyToDataPoints,
  historyToEnabledPoints,
  forecastToDataPoints,
  forecastToEnabledPoints,
  loadChartJs,
} from '../components/time-series-chart.js';
import {
  buildTemperatureChart,
  buildPowerChart,
  buildDisturbanceChart,
} from './room-detail.js';
import { systemEntity } from '../utils.js';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

const PARAM_DEFS = [
  { key: 'update_interval', label: 'Sample Interval', unit: 's', hint: 'Re-planning cadence', step: 30, parse: parseInt },
  { key: 'comfort_offset', label: 'Comfort Offset', unit: '°C', hint: 'Symmetric band around setpoint', step: 0.1, parse: parseFloat },
  { key: 'horizon', label: 'Prediction Horizon', unit: 'steps', hint: 'Control intervals planned ahead', step: 1, parse: parseInt },
  { key: 'tracking_weight', label: 'Tracking Weight', unit: '', hint: 'Setpoint tracking strength (0 = band only)', step: 0.1, parse: parseFloat },
  { key: 'energy_weight', label: 'Energy Weight', unit: '', hint: 'Energy-use penalty', step: 0.01, parse: parseFloat },
  { key: 'energy_price_weight', label: 'Price Sensitivity', unit: '', hint: 'Electricity price cost scaling', step: 0.1, parse: parseFloat },
  { key: 'smoothing_weight', label: 'Output Smoothing', unit: '', hint: 'Penalises rapid output changes', step: 0.05, parse: parseFloat },
  { key: 'soft_constraint_weight', label: 'Comfort Band Penalty (quadratic)', unit: '', hint: 'Quadratic penalty for leaving comfort zone', step: 1, parse: parseFloat },
  { key: 'soft_constraint_linear_weight', label: 'Comfort Band Penalty (linear)', unit: '', hint: 'Linear penalty for comfort-band violations (0 = disabled)', step: 1, parse: parseFloat },
  { key: 'terminal_weight', label: 'Terminal Weight', unit: '', hint: 'End-of-horizon constraint', step: 1, parse: parseFloat },
];

// Must match backend DEFAULT_* constants in const.py
const DEFAULTS = {
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
  desc.textContent = 'Configure MPC controller and window detection parameters. Change values, preview the resulting control plan, then apply when satisfied.';
  container.appendChild(desc);

  const pendingBanner = document.createElement('div');
  pendingBanner.className = 'tuning-pending-banner';
  pendingBanner.hidden = true;
  pendingBanner.textContent = 'The configured parameters differ from what is currently applied. Click Apply Changes to take effect.';
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

  const grid = document.createElement('div');
  grid.className = 'tuning-params-grid tuning-params-grid--wide';
  formSection.appendChild(grid);
  container.appendChild(formSection);

  const inputs = {};
  for (const def of PARAM_DEFS) {
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
      Solve the optimal control problem with the configured MPC parameters above and inspect the planned trajectories per room.
      Preview does not change the live controller.
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
  let previewHistory = null;
  let selectedPreviewRoom = rooms[0]?.slug ?? null;
  let plotSettings = { historyHours: 12, forecastHours: 0 };

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

  function updatePendingIndicators() {
    const pending = hasPendingChanges();
    pendingBanner.hidden = !pending;
    for (const def of ALL_PARAM_DEFS) {
      const input = inputs[def.key] ?? windowInputs[def.key];
      if (!input) continue;
      const configured = def.parse(input.value);
      const applied = appliedConfig?.[def.key] ?? ALL_DEFAULTS[def.key];
      input.classList.toggle('form-input--modified', pending && !valuesEqual(configured, applied));
    }
  }

  function populate(config) {
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

  async function fromWebSocket() {
    const h = liveHass();
    if (typeof h?.callWS !== 'function') return null;
    try {
      const result = await h.callWS({ type: 'heating_assistant/get_controller_config' });
      const cfg = result?.config;
      if (cfg && typeof cfg === 'object' && Object.keys(cfg).length > 0) return cfg;
      console.warn('[TuningPage] WS returned empty config:', result);
    } catch (e) {
      console.error('[TuningPage] WS call failed:', e);
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
      if (applyConfig(await fromWebSocket())) return;
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

  function closeStepSegments(pts) {
    const out = [];
    for (let i = 0; i < pts.length; i++) {
      out.push(pts[i]);
      if (pts[i].y !== null && i + 1 < pts.length && pts[i + 1].y === null) {
        out.push({ x: pts[i + 1].x - 1, y: pts[i].y });
      }
    }
    return out;
  }

  function clampFirstToWindow(pts, windowStart) {
    if (pts.length > 0 && pts[0].x < windowStart) {
      pts[0] = { ...pts[0], x: windowStart };
    }
    return pts;
  }

  function renderPreviewChartsForRoom(roomSlug) {
    const room = rooms.find((r) => r.slug === roomSlug);
    if (!room || !previewPayload || !previewHistory) return;

    const historyHours = plotSettings.historyHours || 12;
    const windowStart = Date.now() - historyHours * 3600 * 1000;
    const history = previewHistory;

    const tempFilteredEntity = room.entities['temperature_filtered'];
    const tempMeasuredEntity = room.entities['temperature_measured'];
    const setpointEntity = room.entities['setpoint'];
    const constraintUpperEntity = room.entities['constraint_upper'];
    const constraintLowerEntity = room.entities['constraint_lower'];
    const powerMeasuredEntity = room.entities['heating_power_measured'];
    const solarMeasuredEntity = room.entities['solar_gain_measured'];
    const outdoorEntity = systemEntity('outdoor_temperature_measured');
    const priceEntity = systemEntity('electricity_price');

    const filteredHistory = historyToDataPoints(history[tempFilteredEntity]);
    const measuredHistory = historyToDataPoints(history[tempMeasuredEntity]);
    const setpointHistory = closeStepSegments(
      clampFirstToWindow(historyToEnabledPoints(history[setpointEntity]), windowStart),
    );
    const constraintUpperHistory = closeStepSegments(
      clampFirstToWindow(historyToEnabledPoints(history[constraintUpperEntity]), windowStart),
    );
    const constraintLowerHistory = closeStepSegments(
      clampFirstToWindow(historyToEnabledPoints(history[constraintLowerEntity]), windowStart),
    );
    const powerHistory = historyToDataPoints(history[powerMeasuredEntity]);
    const solarHistory = historyToDataPoints(history[solarMeasuredEntity]);
    const outdoorHistory = historyToDataPoints(history[outdoorEntity]);
    const priceHistory = historyToDataPoints(history[priceEntity]);

    const roomForecast = previewPayload.rooms?.[roomSlug];
    const forecastData = roomForecast?.forecast || [];
    const priceForecastData = previewPayload.price_forecast || [];

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
      filteredHistory, measuredHistory,
      setpointHistory, setpointForecast,
      tempForecastNonlinear, tempForecastLinearised,
      constraintUpperHistory, constraintUpperForecast,
      constraintLowerHistory, constraintLowerForecast,
      null,
    );
    buildPowerChart(
      previewCharts.power,
      powerHistory,
      powerForecast,
      priceHistory,
      priceForecast,
      roomForecast,
      windowStart,
    );
    buildDisturbanceChart(
      previewCharts.disturb,
      outdoorHistory,
      outdoorForecast,
      solarHistory,
      solarForecast,
    );
  }

  async function runPreview() {
    setPreviewStatus('Computing preview…', 'running');
    btnPreview.disabled = true;
    previewChartsEl.hidden = true;
    roomSelectorEl.hidden = true;
    try {
      await loadChartJs();
      const uiSettings = await connection.getUiSettings();
      if (uiSettings) {
        const h = Number(uiSettings.plot_history_hours);
        if (Number.isFinite(h) && h > 0) plotSettings.historyHours = h;
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
        setPreviewStatus('Outdoor temperature is unavailable — preview needs current weather data.', 'error');
        return;
      }

      const historyEntities = [];
      for (const room of rooms) {
        historyEntities.push(
          room.entities['temperature_filtered'],
          room.entities['temperature_measured'],
          room.entities['setpoint'],
          room.entities['constraint_upper'],
          room.entities['constraint_lower'],
          room.entities['heating_power_measured'],
          room.entities['solar_gain_measured'],
        );
      }
      historyEntities.push(
        systemEntity('outdoor_temperature_measured'),
        systemEntity('electricity_price'),
      );

      previewHistory = await connection.getHistory(
        historyEntities.filter(Boolean),
        plotSettings.historyHours,
      );
      previewPayload = payload;

      if (!selectedPreviewRoom || !previewPayload.rooms?.[selectedPreviewRoom]) {
        selectedPreviewRoom = rooms.find((r) => previewPayload.rooms?.[r.slug])?.slug
          ?? Object.keys(previewPayload.rooms || {})[0]
          ?? null;
      }

      previewChartsEl.hidden = false;
      renderRoomSelector();
      if (selectedPreviewRoom) renderPreviewChartsForRoom(selectedPreviewRoom);
      setPreviewStatus('Preview ready — switch rooms below without recomputing.', 'success');
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
      await hass.callService('heating_assistant', 'update_controller_tuning', mpcData);

      const estData = {};
      for (const def of WINDOW_DEFS) estData[def.key] = def.parse(windowInputs[def.key].value);
      await hass.callService('heating_assistant', 'update_estimation_params', estData);

      applyConfig(await fromWebSocket()) || applyConfig(fromEntityState());
      userEditing = false;
      previewPayload = null;
      previewHistory = null;
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
    const h = Number(s.plot_history_hours);
    if (Number.isFinite(h) && h > 0) plotSettings.historyHours = h;
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
