import { formatTemperature, formatPower, entityValue } from '../utils.js';

export function createRoomTile(room, state) {
  const container = document.createElement('div');
  container.className = 'card card--clickable room-tile';
  container.dataset.room = room.slug;

  container.addEventListener('click', () => {
    window.location.hash = `#room/${room.slug}`;
  });

  renderTileContent(container, room, state);
  return container;
}

export function updateRoomTile(container, room, state) {
  renderTileContent(container, room, state);
}

function renderTileContent(container, room, state) {
  const tempEntity = room.entities['temperature_filtered'] || room.entities['temperature_measured'];
  const powerEntity = room.entities['heating_power_measured'];
  const setpointEntity = room.entities['setpoint'];

  const temp = tempEntity ? entityValue(state, tempEntity) : null;
  const power = powerEntity ? entityValue(state, powerEntity) : null;
  const setpoint = setpointEntity ? entityValue(state, setpointEntity) : null;
  const isActive = power !== null && power > 0;

  container.innerHTML = `
    <span class="room-tile__name">${room.name}</span>
    <div class="room-tile__row">
      <span class="room-tile__temp">${formatTemperature(temp)}</span>
      <span class="room-tile__setpoint">→ ${setpoint !== null ? setpoint.toFixed(0) + '°C' : '—'}</span>
    </div>
    <div class="room-tile__row">
      <span class="room-tile__power">${formatPower(power)}</span>
      <span class="room-tile__status ${isActive ? 'room-tile__status--active' : 'room-tile__status--idle'}"></span>
    </div>
  `;
}
