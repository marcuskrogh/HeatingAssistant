import {
  updateSystemConfig, updateSystemParams,
} from '../ha-services.js?v=118';
import {
  configPageShell, sectionCard, actionsBar, setStatus, numberField, paramGrid,
  loadingNode, entitySelectorField, advancedSubsection,
} from './config-ui.js?v=118';

// System Parameters
// ---------------------------------------------------------------------------

function renderSystemParams(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    title: 'SYSTEM PARAMETERS',
    description: 'Data retention, history depth and other system-level settings that control how the integration stores and manages data.',
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const sp = (cfg && cfg.system_params) || {};
    const working = {
      identification_history_days: sp.identification_history_days,
    };

    body.innerHTML = '';
    const actions = actionsBar('Apply Changes');
    body.appendChild(actions);

    const histCard = sectionCard(
      'Identification history',
      'Controls how much past observation data the integration keeps on disk. '
      + 'Each day of operation produces one JSONL file; files older than the '
      + 'retention window are deleted automatically once per day. '
      + 'Longer retention means more data is available for system identification, '
      + 'at the cost of a small amount of extra storage (roughly 1–2 MB per day).',
    );
    histCard.appendChild(paramGrid(
      numberField(working, 'identification_history_days', 'History retention', {
        step: 1, unit: 'days', min: 7,
        hint: 'How many days of JSONL observation files to keep. Default: 90.',
      }),
    ));
    body.appendChild(histCard);

    const statusEl = actions.querySelector('[data-role="status"]');
    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      setStatus(statusEl, 'Applying…', 'running');
      try {
        const data = {};
        if (working.identification_history_days != null) {
          data.identification_history_days = Math.round(Number(working.identification_history_days));
        }
        await updateSystemParams(hass, data);
        setStatus(statusEl, 'Applied.', 'success');
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
      }
      btn.disabled = false;
    });
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Environment & Site
// ---------------------------------------------------------------------------

function renderSystem(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    title: 'ENVIRONMENT',
    description: 'Recommended outdoor signals and electricity price for forecasts and optimisation.',
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const sys = (cfg && cfg.system) || {};
    const working = { ...sys };
    // Solar GHI option removed from the Environment UI (SWD-271).
    delete working.solar_radiation_entity;

    body.innerHTML = '';
    const actions = actionsBar('Apply Changes');
    body.appendChild(actions);

    const envCard = sectionCard(
      'Recommended sensors',
      'Start with a weather entity (forecast + outdoor temperature for your location) '
      + 'and an electricity price sensor for price-aware control. '
      + 'A dedicated outdoor temperature sensor is optional if you want a clearer local reading.',
    );
    envCard.appendChild(paramGrid(
      entitySelectorField(container, hass, working, 'price_entity', 'Electricity price', ['sensor'], {
        hint: 'Recommended. Hourly / spot market price sensor (e.g. Nord Pool).',
      }),
      entitySelectorField(container, hass, working, 'weather_entity', 'Weather forecast', ['weather'], {
        hint: 'Recommended. Provides outdoor temperature and forecast for your location — the lowest-friction outdoor signal.',
      }),
    ));
    const outdoorOpt = advancedSubsection(envCard, 'Optional: outdoor temperature sensor');
    outdoorOpt.appendChild(paramGrid(
      entitySelectorField(container, hass, working, 'outdoor_temp_entity', 'Outdoor temperature', ['sensor'], {
        hint: 'Optional. Use when you have a local outdoor sensor and want a clearer reading than the weather entity alone.',
      }),
    ));
    body.appendChild(envCard);

    const statusEl = actions.querySelector('[data-role="status"]');
    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      setStatus(statusEl, 'Applying…', 'running');
      try {
        const data = {
          outdoor_temp_entity: working.outdoor_temp_entity || '',
          weather_entity: working.weather_entity || '',
          // Always clear — option removed from the UI (SWD-271).
          solar_radiation_entity: '',
          price_entity: working.price_entity || '',
        };
        await updateSystemConfig(hass, data);
        setStatus(statusEl, 'Applied.', 'success');
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
      }
      btn.disabled = false;
    });
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------
// Generic add/remove list editor used for windows and connections
// ---------------------------------------------------------------------------

export { renderSystem, renderSystemParams };
