import { setPanelHash } from '../panel-hash.js?v=112';
import {
  el, configPageShell, configListHeader, loadingNode,
} from './config-ui.js?v=112';

// Rooms — list
// ---------------------------------------------------------------------------

function renderRoomList(container, connection, hass) {
  const { body } = configPageShell(container, {
    backLabel: 'CONFIGURATION',
    backHash: '#config',
    description: 'Thermal model, comfort setpoints, sensors, windows and inter-room connections for each room.',
  });
  container.insertBefore(
    configListHeader('ROOMS', '+ Add Room', () => {
      setPanelHash('#config/rooms/new');
    }),
    body,
  );
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const list = (cfg && cfg.rooms) || [];
    body.innerHTML = '';
    if (list.length === 0) {
      body.appendChild(el('div', 'config-empty',
        'No rooms configured yet. Click <strong>+ Add Room</strong> to create one.'));
    }
    const grid = el('div', 'config-list-grid');
    list.forEach((room, i) => {
      const card = el('div', 'card card--clickable config-list-card');
      const topLevelSources = (cfg.heat_sources || []).filter((s) => s.room === room.name).length;
      const roomLevelSources = (room.heat_sources || []).length;
      const sources = topLevelSources + roomLevelSources;
      const sensorCount = (room.temp_sensors || []).length || (room.temp_sensor ? 1 : 0);
      const connectionCount = (room.connections || []).length;
      card.innerHTML = `
        <div class="config-list-card__name">${room.name || 'Room ' + (i + 1)}</div>
        <div class="config-list-card__meta">
          <span>${sensorCount} sensor(s)</span>
          <span>${(room.windows || []).length} window(s)</span>
          <span>${connectionCount} connection(s)</span>
          <span>${sources} heater(s)</span>
        </div>
        <div class="config-landing-card__chevron">›</div>
      `;
      card.addEventListener('click', () => { setPanelHash(`#config/rooms/${i}`); });
      grid.appendChild(card);
    });
    body.appendChild(grid);
  });

  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------

export { renderRoomList };
