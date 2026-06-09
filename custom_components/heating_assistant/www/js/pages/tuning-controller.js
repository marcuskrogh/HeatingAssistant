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

// Online internal-gain estimation (augmented-state parameter estimation).
// Exposed as the original OU parameters: κ (mean-reversion rate) and σ_g
// (diffusion coefficient) of the per-room internal-gain deviation SDE
//     dΔg = −κ·Δg·dt + σ_g·dW
// The backend stores the reparametrised form (τ_g [h], s_∞ [W]); conversion
// is applied on read and write so the backend API is unchanged.
const GAIN_DEFS = [
  { key: 'gain_kappa_per_hour', label: 'Reversion Rate κ', unit: '1/h', hint: 'OU mean-reversion rate; κ = 1/τ (e.g. 0.042 ≈ 24 h time constant)', step: 0.001, min: 0.006, max: 10, parse: parseFloat },
  { key: 'gain_sigma_watts_per_sqrth', label: 'Diffusion σ_g', unit: 'W/√h', hint: 'OU noise intensity (σ_g = s∞·√(2κ)); controls how fast the estimate may change', step: 0.1, min: 0, max: 5000, parse: parseFloat },
];

// Defaults in UI units (κ [1/h], σ_g [W/√h]) derived from backend defaults:
//   τ_g = 24 h → κ = 1/24 ≈ 0.04167 1/h
//   s_∞ = 200 W → σ_g = 200·√(2/24) ≈ 57.74 W/√h
const GAIN_DEFAULTS = {
  gain_estimator_enabled: true,
  gain_kappa_per_hour: 1.0 / 24.0,
  gain_sigma_watts_per_sqrth: 200.0 * Math.sqrt(2.0 / 24.0),
};

// Convert backend storage units (τ_g [h], s_∞ [W]) → UI units (κ [1/h], σ_g [W/√h]).
function gainBackendToUI(tau_h, s_inf) {
  const kappa_h = 1.0 / tau_h;
  return { kappa_h, sigma: s_inf * Math.sqrt(2.0 * kappa_h) };
}

// Convert UI units (κ [1/h], σ_g [W/√h]) → backend storage units (τ_g [h], s_∞ [W]).
function gainUIToBackend(kappa_h, sigma) {
  const tau_h = 1.0 / kappa_h;
  return { tau_h, s_inf: sigma / Math.sqrt(2.0 * kappa_h) };
}

export function renderControllerTuning(container, _rooms, _state, connection, hass) {
  return renderTuningIndex(container, connection, hass);
}

function renderTuningIndex(container, connection, hass) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'CONTROLLER TUNING';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Configure MPC controller and window detection parameters. Edit values below and click Apply Changes to send them to the system. Reset to Defaults fills the boxes with factory defaults without saving — you still need to click Apply Changes to commit them.';
  container.appendChild(desc);

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

  // --- Internal-Gain Estimation section ---
  const gainSection = document.createElement('div');
  gainSection.className = 'card tuning-section';

  const gainTitle = document.createElement('div');
  gainTitle.className = 'tuning-section__title';
  gainTitle.textContent = 'Internal-Gain Estimation';
  gainSection.appendChild(gainTitle);

  const gainDesc = document.createElement('p');
  gainDesc.className = 'tuning-section__desc';
  gainDesc.textContent = "Online-estimate each room's internal heat gain (people, appliances, lighting) as an augmented filter state. When enabled, the controller learns a slowly-varying gain deviation from the configured nominal and feeds it forward. The two parameters shape how aggressively and how far the estimate is allowed to move.";
  gainSection.appendChild(gainDesc);

  // Enable / disable toggle.
  const gainToggleRow = document.createElement('div');
  gainToggleRow.className = 'tuning-toggle';
  gainToggleRow.innerHTML = `
    <span class="tuning-toggle__label">Estimation:</span>
    <button type="button" class="tuning-toggle__btn" id="gain-toggle"></button>
  `;
  gainSection.appendChild(gainToggleRow);
  const gainToggleBtn = gainToggleRow.querySelector('#gain-toggle');

  let gainEnabled = GAIN_DEFAULTS.gain_estimator_enabled;
  function renderGainToggle() {
    gainToggleBtn.textContent = gainEnabled ? 'ENABLED' : 'DISABLED';
    gainToggleBtn.className =
      'tuning-toggle__btn ' +
      (gainEnabled ? 'tuning-toggle__btn--on' : 'tuning-toggle__btn--off');
  }
  renderGainToggle();

  const gainGrid = document.createElement('div');
  gainGrid.className = 'tuning-params-grid';
  gainSection.appendChild(gainGrid);
  container.appendChild(gainSection);

  const gainInputs = {};
  for (const def of GAIN_DEFS) {
    const group = document.createElement('div');
    group.className = 'form-group';
    group.innerHTML = `
      <label class="form-label" for="gain-${def.key}">${def.label}</label>
      <input class="form-input" type="number" id="gain-${def.key}"
        step="${def.step}" min="${def.min}" max="${def.max}" value="">
      <span class="form-hint">${def.unit ? def.unit + ' — ' : ''}${def.hint}</span>
    `;
    gainGrid.appendChild(group);
    gainInputs[def.key] = group.querySelector('input');
  }

  const btnApply = container.querySelector('#btn-apply-all');
  const btnReset = container.querySelector('#btn-reset-all');
  const statusEl = container.querySelector('#tuning-status');

  function setStatus(text, type = '') {
    statusEl.textContent = text;
    statusEl.className = 'tuning-actions__status';
    if (type) statusEl.classList.add(`tuning-actions__status--${type}`);
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
    const tau_h = config.gain_reversion_time_hours;
    const s_inf = config.gain_stationary_std_watts;
    if (tau_h != null && s_inf != null) {
      const { kappa_h, sigma } = gainBackendToUI(tau_h, s_inf);
      gainInputs['gain_kappa_per_hour'].value = kappa_h;
      gainInputs['gain_sigma_watts_per_sqrth'].value = sigma;
    } else if (tau_h != null) {
      gainInputs['gain_kappa_per_hour'].value = 1.0 / tau_h;
    }
    if (config.gain_estimator_enabled !== undefined && config.gain_estimator_enabled !== null) {
      gainEnabled = Boolean(config.gain_estimator_enabled);
      renderGainToggle();
    }
  }

  function populateDefaults() {
    for (const def of PARAM_DEFS) inputs[def.key].value = DEFAULTS[def.key];
    for (const def of WINDOW_DEFS) windowInputs[def.key].value = WINDOW_DEFAULTS[def.key];
    gainInputs['gain_kappa_per_hour'].value = GAIN_DEFAULTS.gain_kappa_per_hour;
    gainInputs['gain_sigma_watts_per_sqrth'].value = GAIN_DEFAULTS.gain_sigma_watts_per_sqrth;
    gainEnabled = GAIN_DEFAULTS.gain_estimator_enabled;
    renderGainToggle();
  }

  // Return the most up-to-date hass object — connection._hass is updated on
  // every set hass() call in the panel element, whereas the `hass` closure
  // variable is the snapshot from when this page was rendered.
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
    populate(cfg);
    setStatus('');
    return true;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let destroyed = false;

  // All user-editable parameter fields (MPC + window config + gain estimation).
  const allParamInputs = [
    ...Object.values(inputs),
    ...Object.values(windowInputs),
    ...Object.values(gainInputs),
  ];

  // Tracks whether the user has begun a manual tuning process (i.e. changed any
  // parameter). Once true, the reactive update() callback stops overwriting the
  // form from system state, so a background state update cannot reset the values
  // the user is editing. It is reset only when the page is re-created
  // (navigation / page refresh) or after the user applies the changes.
  let userEditing = false;
  allParamInputs.forEach((inp) => {
    inp.addEventListener('input', () => { userEditing = true; });
  });

  // Toggling the gain estimator also counts as an edit and flips its state.
  gainToggleBtn.addEventListener('click', () => {
    gainEnabled = !gainEnabled;
    renderGainToggle();
    userEditing = true;
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

  btnApply.addEventListener('click', async () => {
    setStatus('Applying…', 'running');
    btnApply.disabled = true;
    try {
      const mpcData = {};
      for (const def of PARAM_DEFS) mpcData[def.key] = def.parse(inputs[def.key].value);
      await hass.callService('heating_assistant', 'update_controller_tuning', mpcData);

      const estData = {};
      for (const def of WINDOW_DEFS) estData[def.key] = def.parse(windowInputs[def.key].value);
      const kappa_h = parseFloat(gainInputs['gain_kappa_per_hour'].value);
      const sigma = parseFloat(gainInputs['gain_sigma_watts_per_sqrth'].value);
      const { tau_h, s_inf } = gainUIToBackend(kappa_h, sigma);
      estData.gain_reversion_time_hours = tau_h;
      estData.gain_stationary_std_watts = s_inf;
      estData.gain_estimator_enabled = gainEnabled;
      await hass.callService('heating_assistant', 'update_estimation_params', estData);

      applyConfig(await fromWebSocket()) || applyConfig(fromEntityState());
      // Edits are now the applied configuration; resume syncing from state.
      userEditing = false;
      setStatus('Applied successfully.', 'success');
    } catch (err) {
      setStatus('Error: ' + (err.message || err), 'error');
    }
    btnApply.disabled = false;
  });

  btnReset.addEventListener('click', () => {
    populateDefaults();
    // Defaults are pending review; protect them from state-sync resets.
    userEditing = true;
    setStatus('Default values loaded — click Apply Changes to save.', '');
  });

  loadConfig();

  return {
    update() {
      // Once the user has started editing, never overwrite the form from state;
      // values should only reset on navigation or page refresh, which re-creates
      // this page. Also skip while a field is focused (before any edit).
      if (userEditing) return;
      const rootNode = container.getRootNode();
      const focused = (rootNode instanceof ShadowRoot ? rootNode : document).activeElement;
      if (allParamInputs.some((inp) => inp === focused)) return;
      applyConfig(fromEntityState());
    },
    destroy() {
      destroyed = true;
    },
  };
}
