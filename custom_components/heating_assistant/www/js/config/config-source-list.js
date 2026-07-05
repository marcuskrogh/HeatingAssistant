import { setPanelHash } from '../panel-hash.js?v=93';
import {
  updateHeatSources,
  updateRooms,
  updateSystemConfig,
  updateSystemParams,
  updateUiSettings,
} from '../ha-services.js?v=93';
import { ICONS } from './config-icons.js?v=93';
import {
  el,
  schedulePanelNav,
  sectionCard,
  advancedSubsection,
  configListHeader,
  configPageShell,
  actionsBar,
  editorActionsBar,
  setStatus,
  numberField,
  textField,
  selectField,
  paramGrid,
  prettify,
  loadingNode,
  fmt,
  entitySelectorField,
  listEditor,
} from './config-ui.js?v=93';

// Heat sources — list
// ---------------------------------------------------------------------------

function renderSourceList(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    description: 'Electric heaters and heat pumps: capacity, efficiency, COP and the entity each one drives.',
  });
  container.insertBefore(
    configListHeader('HEAT SOURCES', '+ Add Heat Source', () => {
      setPanelHash('#config/sources/new');
    }),
    body,
  );
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const list = (cfg && cfg.heat_sources) || [];
    body.innerHTML = '';
    if (list.length === 0) {
      body.appendChild(el('div', 'config-empty',
        'No heat sources configured yet. Click <strong>+ Add Heat Source</strong> to create one.'));
    }
    const grid = el('div', 'config-list-grid');
    list.forEach((src, i) => {
      const card = el('div', 'card card--clickable config-list-card');
      card.innerHTML = `
        <div class="config-list-card__name">${src.name || 'Source ' + (i + 1)}</div>
        <div class="config-list-card__meta">
          <span>${prettify(src.type || 'electric_heater')}</span>
          <span>Room: ${src.room || '—'}</span>
          <span>${fmt(src.max_power, ' W', '—')}</span>
        </div>
        <div class="config-landing-card__chevron">›</div>
      `;
      card.addEventListener('click', () => { setPanelHash(`#config/sources/${i}`); });
      grid.appendChild(card);
    });
    body.appendChild(grid);
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------

export { renderSourceList };
