import { updateUiSettings } from '../ha-services.js?v=93';
import {
  configPageShell, sectionCard, actionsBar, setStatus, numberField, paramGrid, loadingNode,
} from './config-ui.js?v=93';

// Display settings
// ---------------------------------------------------------------------------

function renderDisplay(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    title: 'DISPLAY & PLOTS',
    description: 'How much history and forecast the room charts show. Decoupled from the controller horizon.',
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const ui = (cfg && cfg.ui_settings) || {};
    const working = {
      plot_history_hours: ui.plot_history_hours,
      plot_forecast_hours: ui.plot_forecast_hours,
    };

    body.innerHTML = '';
    const actions = actionsBar('Apply Changes');
    body.appendChild(actions);

    const card = sectionCard(
      'Room chart windows',
      'These control only the industrial dashboard plots — never the controller. '
      + 'The prediction horizon is independent of the MPC controller horizon: if you '
      + 'plot further ahead than the controller plans, the final actuation is held flat '
      + 'and the temperature is simulated forward to fill the window.',
    );
    card.appendChild(paramGrid(
      numberField(working, 'plot_history_hours', 'History window', {
        step: 1, unit: 'h', min: 1,
        hint: 'How far back the measured history is drawn.',
      }),
      numberField(working, 'plot_forecast_hours', 'Forecast horizon', {
        step: 1, unit: 'h', min: 0,
        hint: '0 = match the controller horizon. Larger extends the plot past it.',
      }),
    ));
    body.appendChild(card);

    const statusEl = actions.querySelector('[data-role="status"]');
    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      setStatus(statusEl, 'Applying…', 'running');
      try {
        const data = {};
        if (working.plot_history_hours != null) data.plot_history_hours = Number(working.plot_history_hours);
        if (working.plot_forecast_hours != null) data.plot_forecast_hours = Number(working.plot_forecast_hours);
        await updateUiSettings(hass, data);
        setStatus(statusEl, 'Applied. Reopen a room to see the new window.', 'success');
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
      }
      btn.disabled = false;
    });
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------

export { renderDisplay };
