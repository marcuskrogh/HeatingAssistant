import {
  updateSystemParams,
} from '../ha-services.js?v=134';
import {
  configPageShell, sectionCard, actionsBar, setStatus, numberField, paramGrid,
  loadingNode,
} from './config-ui.js?v=134';

function renderAdvanced(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    title: 'ADVANCED',
    description: 'Deeper tuning knobs. More settings will land here over time.',
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const sp = (cfg && cfg.system_params) || {};
    const capS = Number(sp.pe_max_compute_s);
    const working = {
      pe_max_compute_min: Number.isFinite(capS) && capS > 0 ? capS / 60.0 : 1,
    };

    body.innerHTML = '';
    const actions = actionsBar('Apply Changes');
    body.appendChild(actions);

    const card = sectionCard(
      'Parameter estimation compute',
      'Stops a parameter estimation run that exceeds this wall-clock limit. '
      + 'Parameters from a timed-out run are not applied. Use a shorter dataset '
      + 'or fewer stored sets if you hit the limit, or raise this value.',
    );
    card.appendChild(paramGrid(
      numberField(working, 'pe_max_compute_min', 'PE max compute time', {
        step: 0.25, unit: 'min', min: 0.25,
        hint: 'Default: 1 minute. Stored as seconds in system parameters.',
      }),
    ));
    body.appendChild(card);

    const statusEl = actions.querySelector('[data-role="status"]');
    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      setStatus(statusEl, 'Applying…', 'running');
      try {
        const minutes = Math.max(0.25, Number(working.pe_max_compute_min));
        await updateSystemParams(hass, { pe_max_compute_s: minutes * 60.0 });
        setStatus(statusEl, 'Applied.', 'success');
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
      }
      btn.disabled = false;
    });
  });

  return { update() {}, destroy() {} };
}

export { renderAdvanced };
