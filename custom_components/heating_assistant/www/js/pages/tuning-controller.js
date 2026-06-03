import { entityValue, formatNumber } from '../utils.js';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

const PARAM_DEFS = [
  { key: 'update_interval', label: 'Sample Interval', unit: 's', hint: 'Re-planning cadence (60–3600)', step: 30, min: 60, max: 3600, parse: parseInt },
  { key: 'comfort_offset', label: 'Comfort Offset', unit: '°C', hint: 'Symmetric band around setpoint', step: 0.1, min: 0.1, max: 5.0, parse: parseFloat },
  { key: 'horizon', label: 'Prediction Horizon', unit: 'steps', hint: 'Control intervals planned ahead', step: 1, min: 1, max: 480, parse: parseInt },
  { key: 'tracking_weight', label: 'Tracking Weight', unit: '', hint: 'Setpoint tracking strength (0 = band only)', step: 0.1, min: 0, max: 10, parse: parseFloat },
  { key: 'energy_weight', label: 'Energy Weight', unit: '', hint: 'Energy-use penalty', step: 0.01, min: 0, max: 10, parse: parseFloat },
  { key: 'energy_price_weight', label: 'Price Sensitivity', unit: '', hint: 'Electricity price cost scaling', step: 0.1, min: 0, max: 10000, parse: parseFloat },
  { key: 'smoothing_weight', label: 'Output Smoothing', unit: '', hint: 'Penalises rapid output changes', step: 0.05, min: 0, max: 10, parse: parseFloat },
  { key: 'soft_constraint_weight', label: 'Comfort Band Penalty (quadratic)', unit: '', hint: 'Quadratic penalty for leaving comfort zone', step: 1, min: 0, max: 10000, parse: parseFloat },
  { key: 'soft_constraint_linear_weight', label: 'Comfort Band Penalty (linear)', unit: '', hint: 'Linear penalty for comfort-band violations (0 = disabled)', step: 1, min: 0, max: 1000000, parse: parseFloat },
  { key: 'terminal_weight', label: 'Terminal Weight', unit: '', hint: 'End-of-horizon constraint', step: 1, min: 1, max: 10000, parse: parseFloat },
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
  { key: 'window_open_debounce', label: 'Window Open Debounce', unit: 's', hint: 'Debounce before confirming window open (0–3600)', step: 10, min: 0, max: 3600, parse: parseInt },
  { key: 'window_open_close_settle', label: 'Window Close Settle', unit: 's', hint: 'Settle time after window closes before resuming (0–3600)', step: 10, min: 0, max: 3600, parse: parseInt },
  { key: 'window_open_q_inflation', label: 'Uncertainty Multiplier', unit: '×', hint: 'Covariance inflation when window is open (1–1000)', step: 1, min: 1, max: 1000, parse: parseFloat },
];

// Must match backend DEFAULT_WINDOW_* constants in const.py
const WINDOW_DEFAULTS = {
  window_open_debounce: 60,
  window_open_close_settle: 30,
  window_open_q_inflation: 10.0,
};

export function renderControllerTuning(container, rooms, state, connection, hass) {
  return renderTuningIndex(container, rooms, state, connection, hass);
}

// ---------------------------------------------------------------------------
// Index view — MPC params + Window Configuration
// ---------------------------------------------------------------------------

function renderTuningIndex(container, rooms, initialState, connection, hass) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'CONTROLLER TUNING';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Configure MPC controller and window detection parameters. Edit values below and click Apply Changes to send them to the system. Reset to Defaults fills the boxes with factory defaults without saving — you still need to click Apply Changes to commit them.';
  container.appendChild(desc);

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
        step="${def.step}" min="${def.min}" max="${def.max}" value="">
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
        step="${def.step}" min="${def.min}" max="${def.max}" value="">
      <span class="form-hint">${def.unit ? def.unit + ' — ' : ''}${def.hint}</span>
    `;
    windowGrid.appendChild(group);
    windowInputs[def.key] = group.querySelector('input');
  }

  // --- Unified action bar ---
  const actionsRow = document.createElement('div');
  actionsRow.className = 'tuning-actions';
  actionsRow.innerHTML = `
    <button class="btn btn--accent tuning-actions__btn" id="btn-apply-all">Apply Changes</button>
    <button class="btn btn--secondary tuning-actions__btn" id="btn-reset-all">Reset to Defaults</button>
    <span class="tuning-actions__status" id="tuning-status"></span>
  `;
  container.appendChild(actionsRow);

  const btnApply = container.querySelector('#btn-apply-all');
  const btnReset = container.querySelector('#btn-reset-all');
  const statusEl = container.querySelector('#tuning-status');

  function setStatus(text, type = '') {
    statusEl.textContent = text;
    statusEl.className = 'tuning-actions__status';
    if (type) statusEl.classList.add(`tuning-actions__status--${type}`);
  }

  // Populate inputs from a live config object — never falls back to defaults.
  function populate(config) {
    for (const def of PARAM_DEFS) {
      const val = config[def.key];
      if (val !== undefined && val !== null) inputs[def.key].value = val;
    }
    for (const def of WINDOW_DEFS) {
      const val = config[def.key];
      if (val !== undefined && val !== null) windowInputs[def.key].value = val;
    }
  }

  // Fill every box with factory defaults — only used by the Reset button.
  function populateDefaults() {
    for (const def of PARAM_DEFS) {
      inputs[def.key].value = DEFAULTS[def.key];
    }
    for (const def of WINDOW_DEFS) {
      windowInputs[def.key].value = WINDOW_DEFAULTS[def.key];
    }
  }

  // Scan a state snapshot for the config entity.  Requires BOTH tracking_weight
  // AND update_interval to avoid matching MPCPerformanceSensor (has "horizon"
  // but not "update_interval").
  function configFromStateSnapshot(snapshot) {
    if (!snapshot) return null;
    const direct = snapshot[CONFIG_ENTITY];
    if (direct?.attributes?.tracking_weight !== undefined &&
        direct?.attributes?.update_interval !== undefined) {
      return direct.attributes;
    }
    for (const [id, s] of Object.entries(snapshot)) {
      if (id.startsWith('sensor.heating_assistant_') && s?.attributes) {
        const a = s.attributes;
        if (a.tracking_weight !== undefined && a.update_interval !== undefined) {
          return a;
        }
      }
    }
    return null;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let loaded = false; // true once real backend values have been shown
  let destroyed = false; // halt the retry loop when navigating away

  // Try every available state snapshot for the config entity's attributes.
  // On a page refresh/reload of a running system, HA delivers the full state
  // snapshot on connect, so this is the most reliable source and needs no
  // round-trip.  Returns true when values were applied.
  function tryEntityState() {
    const cfg =
      configFromStateSnapshot(initialState) ||
      configFromStateSnapshot(hass?.states || {}) ||
      configFromStateSnapshot(connection._hass?.states || {});
    if (cfg) {
      populate(cfg);
      loaded = true;
      setStatus('');
      return true;
    }
    return false;
  }

  // Confirm via the authoritative WebSocket command, which reads directly from
  // the coordinator.  Returns true when values were applied.
  async function tryWebSocket() {
    try {
      const wsConfig = await connection.getControllerConfig();
      if (wsConfig && Object.keys(wsConfig).length > 0) {
        populate(wsConfig);
        loaded = true;
        setStatus('');
        return true;
      }
    } catch (e) {
      console.warn('[TuningPage] WS config fetch failed:', e);
    }
    return false;
  }

  // Load the current values.  Strategy: take the instant entity-state read
  // first (reliable on reload), then poll the authoritative WebSocket command
  // with backoff to cover the window where the integration is still starting
  // up and neither source is ready yet.  Defaults are NEVER used here — inputs
  // are left unchanged on failure, and the live update() handler below keeps
  // recovering from state_changed events afterwards.
  async function loadConfig() {
    tryEntityState();
    if (!loaded) setStatus('Loading current values…', 'running');

    const delays = [0, 300, 700, 1500, 3000];
    for (let i = 0; i < delays.length; i++) {
      if (destroyed) return;
      if (delays[i]) await sleep(delays[i]);
      if (destroyed) return;
      if (await tryWebSocket()) return;
      if (tryEntityState()) return;
    }

    if (!loaded) {
      setStatus('Could not load current values — check that the integration is running.', 'error');
    }
  }

  // Lightweight refresh used on live state_changed events to stay in sync
  // without spamming the WebSocket on every event.
  function populateFromState(snapshot) {
    const cfg = configFromStateSnapshot(snapshot);
    if (cfg) populate(cfg);
  }

  btnApply.addEventListener('click', async () => {
    setStatus('Applying…', 'running');
    btnApply.disabled = true;
    try {
      const mpcData = {};
      for (const def of PARAM_DEFS) {
        mpcData[def.key] = def.parse(inputs[def.key].value);
      }
      await hass.callService('heating_assistant', 'update_controller_tuning', mpcData);

      const windowData = {};
      for (const def of WINDOW_DEFS) {
        windowData[def.key] = def.parse(windowInputs[def.key].value);
      }
      await hass.callService('heating_assistant', 'update_estimation_params', windowData);

      // Re-read authoritative config so boxes reflect what the controller now
      // holds.  apply_tuning_updates runs synchronously in the service call, so
      // a single WS round-trip is enough here.
      await tryWebSocket();
      setStatus('Applied successfully.', 'success');
    } catch (err) {
      setStatus('Error: ' + (err.message || err), 'error');
    }
    btnApply.disabled = false;
  });

  // Reset only fills boxes with defaults — does NOT call any service.
  btnReset.addEventListener('click', () => {
    populateDefaults();
    setStatus('Default values loaded — click Apply Changes to save.', '');
  });

  loadConfig();

  return {
    update(newState) {
      // Resolve focus correctly inside a shadow DOM — document.activeElement
      // returns the shadow host, not the focused input inside the shadow root.
      const rootNode = container.getRootNode();
      const focused = (rootNode instanceof ShadowRoot ? rootNode : document).activeElement;
      const allInputs = [...Object.values(inputs), ...Object.values(windowInputs)];
      if (!allInputs.some((inp) => inp === focused)) {
        populateFromState(newState);
      }
    },
    destroy() {
      destroyed = true; // halt any in-flight retry backoff loop
    },
  };
}
