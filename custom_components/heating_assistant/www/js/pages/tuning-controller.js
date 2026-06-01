import { entityValue, formatNumber } from '../utils.js';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

const PARAM_DEFS = [
  { key: 'update_interval', label: 'Sample Interval', unit: 's', hint: 'Re-planning cadence (60\u20133600)', step: 30, min: 60, max: 3600, parse: parseInt },
  { key: 'comfort_offset', label: 'Comfort Offset', unit: '\u00b0C', hint: 'Symmetric band around setpoint', step: 0.1, min: 0.1, max: 5.0, parse: parseFloat },
  { key: 'horizon', label: 'Prediction Horizon', unit: 'steps', hint: 'Control intervals planned ahead', step: 1, min: 1, max: 480, parse: parseInt },
  { key: 'tracking_weight', label: 'Tracking Weight', unit: '', hint: 'Setpoint tracking strength (0 = band only)', step: 0.1, min: 0, max: 10, parse: parseFloat },
  { key: 'energy_weight', label: 'Energy Weight', unit: '', hint: 'Energy-use penalty', step: 0.01, min: 0, max: 10, parse: parseFloat },
  { key: 'energy_price_weight', label: 'Price Sensitivity', unit: '', hint: 'Electricity price cost scaling', step: 0.1, min: 0, max: 10000, parse: parseFloat },
  { key: 'smoothing_weight', label: 'Output Smoothing', unit: '', hint: 'Penalises rapid output changes', step: 0.05, min: 0, max: 10, parse: parseFloat },
  { key: 'soft_constraint_weight', label: 'Comfort Band Penalty (quadratic)', unit: '', hint: 'Quadratic penalty for leaving comfort zone', step: 1, min: 0, max: 10000, parse: parseFloat },
  { key: 'soft_constraint_linear_weight', label: 'Comfort Band Penalty (linear)', unit: '', hint: 'Linear penalty for comfort-band violations (0 = disabled)', step: 1, min: 0, max: 1000000, parse: parseFloat },
  { key: 'terminal_weight', label: 'Terminal Weight', unit: '', hint: 'End-of-horizon constraint', step: 1, min: 1, max: 10000, parse: parseFloat },
];

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

export function renderControllerTuning(container, rooms, state, connection, hass) {
  return renderTuningIndex(container, rooms, state, connection, hass);
}

// ---------------------------------------------------------------------------
// Index view — MPC params
// ---------------------------------------------------------------------------

function renderTuningIndex(container, rooms, state, connection, hass) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'CONTROLLER TUNING';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Configure MPC controller parameters. These determine how the controller balances comfort, energy use, and responsiveness. Changes take effect immediately.';
  container.appendChild(desc);

  // --- Parameter form ---
  const formSection = document.createElement('div');
  formSection.className = 'card tuning-section';

  const grid = document.createElement('div');
  grid.className = 'tuning-params-grid tuning-params-grid--wide';

  const activeConfig = state[CONFIG_ENTITY]?.attributes || {};
  const inputs = {};
  for (const def of PARAM_DEFS) {
    const val = activeConfig[def.key] ?? DEFAULTS[def.key];
    const group = document.createElement('div');
    group.className = 'form-group';
    group.innerHTML = `
      <label class="form-label" for="ctrl-${def.key}">${def.label}</label>
      <input class="form-input" type="number" id="ctrl-${def.key}"
        step="${def.step}" min="${def.min}" max="${def.max}"
        value="${val}">
      <span class="form-hint">${def.unit ? def.unit + ' \u2014 ' : ''}${def.hint}</span>
    `;
    grid.appendChild(group);
    inputs[def.key] = group.querySelector('input');
  }

  formSection.appendChild(grid);

  const actionsRow = document.createElement('div');
  actionsRow.className = 'tuning-actions';
  actionsRow.style.marginTop = '20px';
  actionsRow.innerHTML = `
    <button class="btn btn--primary" id="btn-apply-ctrl">Apply Changes</button>
    <button class="btn btn--ghost" id="btn-reset-ctrl">Reset to Defaults</button>
    <span class="tuning-actions__status" id="ctrl-status"></span>
  `;
  formSection.appendChild(actionsRow);
  container.appendChild(formSection);

  // --- Wire up controller params ---
  const btnApply = container.querySelector('#btn-apply-ctrl');
  const btnReset = container.querySelector('#btn-reset-ctrl');
  const statusEl = container.querySelector('#ctrl-status');

  function setStatus(text, type = '') {
    statusEl.textContent = text;
    statusEl.className = 'tuning-actions__status';
    if (type) statusEl.classList.add(`tuning-actions__status--${type}`);
  }

  function populateFromState(st) {
    const config = st[CONFIG_ENTITY]?.attributes || {};
    for (const def of PARAM_DEFS) {
      if (config[def.key] != null) {
        inputs[def.key].value = config[def.key];
      }
    }
  }

  btnApply.addEventListener('click', async () => {
    setStatus('Applying\u2026', 'running');
    btnApply.disabled = true;
    try {
      const data = {};
      for (const def of PARAM_DEFS) {
        data[def.key] = def.parse(inputs[def.key].value);
      }
      await hass.callService('heating_assistant', 'update_controller_tuning', data);
      setStatus('Applied successfully.', 'success');
    } catch (err) {
      setStatus('Error: ' + (err.message || err), 'error');
    }
    btnApply.disabled = false;
  });

  btnReset.addEventListener('click', () => {
    for (const def of PARAM_DEFS) {
      inputs[def.key].value = DEFAULTS[def.key];
    }
    setStatus('Reset to defaults.', '');
  });

  populateFromState(state);

  return {
    update(newState) {
      const focused = document.activeElement;
      const isEditing = Object.values(inputs).some((inp) => inp === focused);
      if (!isEditing) {
        populateFromState(newState);
      }
    },
    destroy() {},
  };
}
