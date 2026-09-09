import {
  TimeSeriesChart,
  forecastToDataPoints,
  forecastToEnabledPoints,
  loadChartJs,
} from '../components/time-series-chart.js?v=157';
import { CHART_HEIGHT_PRIMARY, CHART_HEIGHT_SECONDARY } from '../components/chart-theme.js?v=157';
import {
  buildTemperatureChart,
  buildPowerChart,
  buildDisturbanceChart,
} from '../charts/mpc-preview-charts.js?v=140';
import {
  updateControllerTuning,
  updateEstimationParams,
} from '../ha-services.js?v=124';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

const MPC_MODE_LINEAR = 'linear';
const MPC_MODE_NMPC = 'nmpc';

const MODE_CARDS = [
  {
    key: MPC_MODE_LINEAR,
    name: 'Linear',
    body: 'Updates heater commands on a quadratic program each sample. Faster to solve; the house model is linearised, so strong nonlinearities are approximated.',
  },
  {
    key: MPC_MODE_NMPC,
    name: 'Nonlinear',
    body: 'Updates heater commands on a nonlinear program each sample — the same receding-horizon problem as Linear, without linearising the house model. Heavier compute.',
  },
];

const SHARED_LIVE_PARAM_DEFS = [
  { key: 'comfort_offset', label: 'Comfort offset', unit: '°C', hint: 'Half-width of the comfort band around the setpoint. Both planners try to stay inside this band.', step: 0.1, parse: parseFloat },
  { key: 'energy_price_weight', label: 'Price sensitivity', unit: '', hint: 'How strongly a high electricity price pushes the plan toward less electrical heat (0 = ignore price).', step: 0.1, parse: parseFloat },
  { key: 'smoothing_weight', label: 'Output smoothing', unit: '', hint: 'Penalty on changing the heater command from one interval to the next.', step: 0.05, parse: parseFloat },
  { key: 'soft_constraint_weight', label: 'Comfort-band penalty', unit: '', hint: 'How hard leaving the comfort band is penalised. Larger values fight harder to stay inside.', step: 1, parse: parseFloat },
];

const LINEAR_LIVE_PARAM_DEFS = [
  { key: 'tracking_weight', label: 'Setpoint pull', unit: '', hint: 'How hard the linear planner pulls toward the exact setpoint while already inside the band (0 = stay inside the band only).', step: 0.1, parse: parseFloat },
  { key: 'energy_weight', label: 'Heater-effort penalty', unit: '', hint: 'Penalty on heater command size itself, separate from electricity price.', step: 0.01, parse: parseFloat },
  { key: 'soft_constraint_linear_weight', label: 'Outside-band linear penalty', unit: '', hint: 'Extra steadily growing penalty the further outside the band (0 = off).', step: 1, parse: parseFloat },
  { key: 'terminal_weight', label: 'End-of-horizon weight', unit: '', hint: 'How strongly the last predicted step should still be near the setpoint (must be at least 1).', step: 1, parse: parseFloat },
];

const SHARED_RESTART_PARAM_DEFS = [
  { key: 'update_interval', label: 'Sample interval', unit: 's', hint: 'How often heater commands are applied (default 900 s).', step: 60, parse: parseFloat },
];

const HIDDEN_TIMING_PARAM_DEFS = [
  { key: 'horizon', label: '', unit: '', hint: '', step: 1, parse: parseInt },
  { key: 'nmpc_horizon_h', label: '', unit: '', hint: '', step: 1, parse: parseFloat },
  { key: 'nmpc_period', label: '', unit: '', hint: '', step: 900, parse: parseFloat },
  { key: 'nmpc_fast_substeps', label: '', unit: '', hint: '', step: 1, parse: parseInt },
];

const LIVE_PARAM_DEFS = [...SHARED_LIVE_PARAM_DEFS, ...LINEAR_LIVE_PARAM_DEFS];
const RESTART_PARAM_DEFS = [
  ...SHARED_RESTART_PARAM_DEFS,
  ...HIDDEN_TIMING_PARAM_DEFS,
];
const PARAM_DEFS = [...LIVE_PARAM_DEFS, ...RESTART_PARAM_DEFS];

// Must match backend DEFAULT_* constants in const.py
const DEFAULTS = {
  mpc_mode: MPC_MODE_NMPC,
  nmpc_period: 900,
  nmpc_fast_substeps: 1,
  nmpc_horizon_h: 36,
  update_interval: 900,
  comfort_offset: 2.0,
  horizon: 144,
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
  desc.textContent = 'Choose a planner, then Apply Changes to put it live. Shared weights keep their values when you switch. Timing knobs for the unused planner stay stored.';
  container.appendChild(desc);

  let selectedMode = MPC_MODE_NMPC;
  let appliedConfig = null;

  const modeSection = document.createElement('div');
  modeSection.className = 'tuning-mode-grid';
  modeSection.setAttribute('role', 'group');
  modeSection.setAttribute('aria-label', 'Planner');
  const modeButtons = {};
  for (const card of MODE_CARDS) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tuning-mode-card';
    btn.dataset.mode = card.key;
    btn.addEventListener('click', () => {
      selectedMode = card.key;
      syncModeCards();
      syncModeParamVisibility();
      userEditing = true;
      updatePendingIndicators();
    });
    modeButtons[card.key] = btn;
    modeSection.appendChild(btn);
  }
  container.appendChild(modeSection);

  function coerceMode(value) {
    return String(value || '').toLowerCase() === MPC_MODE_LINEAR
      ? MPC_MODE_LINEAR
      : MPC_MODE_NMPC;
  }

  function liveMode() {
    return coerceMode(appliedConfig?.mpc_mode ?? DEFAULTS.mpc_mode);
  }

  function syncModeCards() {
    const applied = liveMode();
    for (const card of MODE_CARDS) {
      const btn = modeButtons[card.key];
      const isLive = card.key === applied;
      const isDraft = card.key === selectedMode;
      btn.classList.toggle('tuning-mode-card--in-use', isLive);
      btn.classList.toggle('tuning-mode-card--selected', isDraft);
      btn.classList.toggle('tuning-mode-card--draft', isDraft && !isLive);
      btn.setAttribute('aria-pressed', isDraft ? 'true' : 'false');
      let badge = '';
      let badgeClass = 'tuning-mode-card__badge';
      if (isLive) {
        badge = 'In use';
        badgeClass += ' tuning-mode-card__badge--in-use';
      } else if (isDraft) {
        badge = 'Apply to switch';
        badgeClass += ' tuning-mode-card__badge--draft';
      }
      btn.innerHTML = `
        <div class="tuning-mode-card__head">
          <span class="tuning-mode-card__radio" aria-hidden="true"></span>
          <div class="tuning-mode-card__titles">
            <div class="tuning-mode-card__name">${card.name}</div>
            <div class="tuning-mode-card__subtitle">model predictive control</div>
          </div>
          <span class="${badgeClass}">${badge}</span>
        </div>
        <p class="tuning-mode-card__body">${card.body}</p>
      `;
    }
  }

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
    return subsection;
  }

  appendParamSubsection(
    'Shared with both planners',
    'Comfort band, electricity price, command smoothing, and the band-exit penalty. Applied on the next planning cycle after you save.',
    SHARED_LIVE_PARAM_DEFS,
  );
  const linearLiveSubsection = appendParamSubsection(
    'Linear MPC cost weights',
    'Extra terms on the linear quadratic program. The nonlinear planner does not use these.',
    LINEAR_LIVE_PARAM_DEFS,
  );
  const linearSubsection = appendParamSubsection(
    'Timing',
    'Sample interval and look-ahead. Both planners use the same receding-horizon grid. Changing these rebuilds the planner when you Apply Changes.',
    SHARED_RESTART_PARAM_DEFS,
  );
  const lookAheadGroup = document.createElement('div');
  lookAheadGroup.className = 'form-group';
  lookAheadGroup.innerHTML = `
    <label class="form-label" for="ctrl-look_ahead_h">Look-ahead</label>
    <input class="form-input" type="number" id="ctrl-look_ahead_h" step="1" value="">
    <span class="form-hint">h — How far the plan covers (default 36 h). Must be a whole number of sample intervals.</span>
  `;
  linearSubsection.querySelector('.tuning-params-grid')?.appendChild(lookAheadGroup)
    || linearSubsection.appendChild(lookAheadGroup);
  const lookAheadInput = lookAheadGroup.querySelector('input');
  for (const def of HIDDEN_TIMING_PARAM_DEFS) {
    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.id = `ctrl-${def.key}`;
    linearSubsection.appendChild(hidden);
    inputs[def.key] = hidden;
  }
  container.appendChild(formSection);

  function syncModeParamVisibility() {
    linearLiveSubsection.hidden = selectedMode !== MPC_MODE_LINEAR;
  }

  syncModeCards();
  syncModeParamVisibility();

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
      Run a one-off solve with the values on this page, including a drafted planner.
      Apply Changes is what switches the live solver.
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

  let previewPayload = null;
  let selectedPreviewRoom = rooms[0]?.slug ?? null;
  let plotSettings = { forecastHours: 0 };

  const previewChartOpts = { forecastOnly: true };

  const previewCharts = {
    temp: new TimeSeriesChart(previewChartsEl.querySelector('[data-chart="temp"]'), {
      title: 'TEMPERATURE',
      yLabel: '\u00b0C',
      height: CHART_HEIGHT_PRIMARY,
    }),
    power: new TimeSeriesChart(previewChartsEl.querySelector('[data-chart="power"]'), {
      title: 'HEATING POWER & PRICE',
      yLabel: 'kW',
      y2: true,
      y2Label: 'Price',
      height: CHART_HEIGHT_SECONDARY,
    }),
    disturb: new TimeSeriesChart(previewChartsEl.querySelector('[data-chart="disturb"]'), {
      title: 'DISTURBANCES',
      yLabel: '\u00b0C',
      y2: true,
      y2Label: 'kW',
      height: CHART_HEIGHT_SECONDARY,
    }),
  };

  function collectConfiguredConfig() {
    syncTimingAliases();
    const cfg = { mpc_mode: selectedMode };
    for (const def of PARAM_DEFS) cfg[def.key] = def.parse(inputs[def.key].value);
    for (const def of WINDOW_DEFS) cfg[def.key] = def.parse(windowInputs[def.key].value);
    return cfg;
  }

  function collectMpcParams() {
    syncTimingAliases();
    const mpcData = { mpc_mode: selectedMode };
    for (const def of PARAM_DEFS) mpcData[def.key] = def.parse(inputs[def.key].value);
    return mpcData;
  }

  function hasPendingChanges() {
    if (!appliedConfig) return false;
    const configured = collectConfiguredConfig();
    if (coerceMode(configured.mpc_mode) !== coerceMode(appliedConfig.mpc_mode)) {
      return true;
    }
    return ALL_PARAM_DEFS.some((def) => !valuesEqual(
      configured[def.key],
      appliedConfig[def.key] ?? ALL_DEFAULTS[def.key],
    ));
  }

  function hasPendingRestartChanges() {
    if (!appliedConfig) return false;
    const configured = collectConfiguredConfig();
    if (coerceMode(configured.mpc_mode) !== coerceMode(appliedConfig.mpc_mode)) {
      return true;
    }
    if (RESTART_PARAM_DEFS.some((def) => !valuesEqual(
      configured[def.key],
      appliedConfig[def.key] ?? ALL_DEFAULTS[def.key],
    ))) {
      return true;
    }
    if (lookAheadInput && appliedConfig) {
      return !valuesEqual(Number(lookAheadInput.value), appliedLookAheadHours(appliedConfig));
    }
    return false;
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
      ? 'Unsaved changes include the planner mode or timing — applying will rebuild the controller.'
      : 'Unsaved changes — penalty weights and window settings apply to the live controller on the next cycle.';
  }

  function appliedLookAheadHours(config) {
    const storedH = Number(config.nmpc_horizon_h);
    if (Number.isFinite(storedH) && storedH > 0) return storedH;
    const dt = Number(config.update_interval ?? DEFAULTS.update_interval);
    const horizon = Number(config.horizon ?? DEFAULTS.horizon);
    return dt > 0 ? horizon * dt / 3600 : DEFAULTS.nmpc_horizon_h;
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
    if (lookAheadInput && appliedConfig) {
      const hours = Number(lookAheadInput.value);
      lookAheadInput.classList.toggle(
        'form-input--modified',
        pending && Number.isFinite(hours) && !valuesEqual(hours, appliedLookAheadHours(appliedConfig)),
      );
    }
  }

  function fillLookAhead() {
    if (!lookAheadInput) return;
    const hours = Number(inputs.nmpc_horizon_h?.value);
    if (Number.isFinite(hours) && hours > 0) {
      lookAheadInput.value = String(hours);
      return;
    }
    const dt = Number(inputs.update_interval?.value);
    const horizon = Number(inputs.horizon?.value);
    if (dt > 0 && Number.isFinite(horizon) && horizon >= 1) {
      lookAheadInput.value = String(horizon * dt / 3600);
    }
  }

  function syncTimingAliases() {
    const dt = Number(inputs.update_interval?.value);
    const hours = Number(lookAheadInput?.value);
    if (!(dt > 0) || !(hours > 0) || !inputs.horizon) return;
    const steps = hours * 3600 / dt;
    const rounded = Math.round(steps);
    if (Math.abs(steps - rounded) > 1e-6 || rounded < 1) return;
    inputs.horizon.value = String(rounded);
    if (inputs.nmpc_horizon_h) inputs.nmpc_horizon_h.value = String(hours);
    if (inputs.nmpc_period) inputs.nmpc_period.value = String(dt);
    if (inputs.nmpc_fast_substeps) inputs.nmpc_fast_substeps.value = '1';
  }

  function timingError() {
    const dt = Number(inputs.update_interval?.value);
    const hours = Number(lookAheadInput?.value);
    if (!(dt > 0) || !(hours > 0)) {
      return 'Sample interval and look-ahead must be positive.';
    }
    const steps = hours * 3600 / dt;
    if (Math.abs(steps - Math.round(steps)) > 1e-6 || Math.round(steps) < 1) {
      return 'Look-ahead must be a whole number of sample intervals.';
    }
    return null;
  }

  function populate(config) {
    selectedMode = coerceMode(config.mpc_mode ?? DEFAULTS.mpc_mode);
    syncModeCards();
    syncModeParamVisibility();
    for (const def of PARAM_DEFS) {
      const val = config[def.key];
      if (val !== undefined && val !== null) inputs[def.key].value = val;
    }
    for (const def of WINDOW_DEFS) {
      const val = config[def.key];
      if (val !== undefined && val !== null) windowInputs[def.key].value = val;
    }
    fillLookAhead();
    syncTimingAliases();
    updatePendingIndicators();
  }

  function populateDefaults() {
    selectedMode = DEFAULTS.mpc_mode;
    syncModeCards();
    syncModeParamVisibility();
    for (const def of PARAM_DEFS) inputs[def.key].value = DEFAULTS[def.key];
    for (const def of WINDOW_DEFS) windowInputs[def.key].value = WINDOW_DEFAULTS[def.key];
    fillLookAhead();
    syncTimingAliases();
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
        if (a.tracking_weight !== undefined && (a.nmpc_period !== undefined || a.update_interval !== undefined)) return a;
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
    lookAheadInput,
  ];

  let userEditing = false;
  allParamInputs.forEach((inp) => {
    if (!inp) return;
    inp.addEventListener('input', () => {
      userEditing = true;
      syncTimingAliases();
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
    const timingErr = timingError();
    if (timingErr) {
      setPreviewStatus(timingErr, 'error');
      return;
    }
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
    const timingErr = timingError();
    if (timingErr) {
      setStatus(timingErr, 'error');
      return;
    }
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
