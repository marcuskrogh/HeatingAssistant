import { formatTemperature, formatPower, entityValue } from '../utils.js';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';
const SP_STEP = 0.5;
const SP_MIN = 5;
const SP_MAX = 30;

export function createRoomTile(room, state, hass) {
  const container = document.createElement('div');
  container.className = 'card card--clickable room-tile';
  container.dataset.room = room.slug;
  container._editing = false;
  container._hass = hass || null;

  container.addEventListener('click', () => {
    if (!container._editing) {
      window.location.hash = `#room/${room.slug}`;
    }
  });

  renderTileContent(container, room, state);
  return container;
}

export function updateRoomTile(container, room, state, hass) {
  if (hass) container._hass = hass;
  if (container._editing) return;
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

  // Schedule summaries from config entity
  const roomSchedules = state[CONFIG_ENTITY]?.attributes?.room_schedules || {};
  const schedData = roomSchedules[room.slug] || roomSchedules[room.name] || null;
  const periods = schedData?.periods || [];

  let schedulesHtml = '';
  if (periods.length > 0) {
    const preview = periods.slice(0, 3);
    const rows = preview.map((p) => {
      const modeHtml = p.mode === 'off'
        ? `<span class="room-tile__sched-off">OFF</span>`
        : `<span class="room-tile__sched-sp">${p.setpoint != null ? p.setpoint + '°' : 'COMFORT'}</span>`;
      return `<div class="room-tile__sched-row">
        <span class="room-tile__sched-name">${p.name || 'Period'}</span>
        <span class="room-tile__sched-time">${p.start}–${p.end}</span>
        ${modeHtml}
      </div>`;
    }).join('');
    const more = periods.length > 3
      ? `<div class="room-tile__sched-more">+${periods.length - 3} more</div>`
      : '';
    schedulesHtml = `<div class="room-tile__schedules">${rows}${more}</div>`;
  }

  container.innerHTML = `
    <span class="room-tile__name">${room.name}</span>
    <div class="room-tile__row">
      <span class="room-tile__temp">${formatTemperature(temp)}</span>
      <div class="room-tile__setpoint-box" title="Click to set temperature">
        <span class="room-tile__setpoint-label">SP</span>
        <span class="room-tile__setpoint-value">${setpoint !== null ? setpoint.toFixed(0) + '°C' : '—'}</span>
        <span class="room-tile__setpoint-edit">✎</span>
      </div>
    </div>
    <div class="room-tile__row">
      <span class="room-tile__power">${formatPower(power)}</span>
      <span class="room-tile__status ${isActive ? 'room-tile__status--active' : 'room-tile__status--idle'}"></span>
    </div>
    ${schedulesHtml}
  `;

  // Wire setpoint box
  const spBox = container.querySelector('.room-tile__setpoint-box');
  if (spBox && container._hass) {
    spBox.addEventListener('click', (e) => {
      e.stopPropagation();
      if (container._editing) return;
      container._editing = true;

      const currentSp = (setpointEntity ? entityValue(state, setpointEntity) : null) ?? 22;
      let selected = Math.max(SP_MIN, Math.min(SP_MAX, Math.round(currentSp / SP_STEP) * SP_STEP));
      const hass = container._hass;

      spBox.innerHTML = `
        <div class="room-tile__sp-editor">
          <button class="room-tile__sp-btn room-tile__sp-down">−</button>
          <span class="room-tile__sp-val">${selected.toFixed(1)}°</span>
          <button class="room-tile__sp-btn room-tile__sp-up">+</button>
          <button class="room-tile__sp-btn room-tile__sp-ok">✓</button>
          <button class="room-tile__sp-btn room-tile__sp-cancel">✗</button>
        </div>
      `;

      const valEl = spBox.querySelector('.room-tile__sp-val');

      spBox.querySelector('.room-tile__sp-down').addEventListener('click', (e) => {
        e.stopPropagation();
        selected = Math.max(SP_MIN, selected - SP_STEP);
        valEl.textContent = selected.toFixed(1) + '°';
      });

      spBox.querySelector('.room-tile__sp-up').addEventListener('click', (e) => {
        e.stopPropagation();
        selected = Math.min(SP_MAX, selected + SP_STEP);
        valEl.textContent = selected.toFixed(1) + '°';
      });

      spBox.querySelector('.room-tile__sp-ok').addEventListener('click', async (e) => {
        e.stopPropagation();
        // Optimistic update
        spBox.innerHTML = `
          <span class="room-tile__setpoint-label">SP</span>
          <span class="room-tile__setpoint-value">${selected.toFixed(0)}°C</span>
          <span class="room-tile__setpoint-edit">✎</span>
        `;
        container._editing = false;
        try {
          await hass.callService('climate', 'set_temperature', {
            entity_id: `climate.heating_assistant_${room.slug}`,
            temperature: selected,
          });
        } catch (_) {}
      });

      spBox.querySelector('.room-tile__sp-cancel').addEventListener('click', (e) => {
        e.stopPropagation();
        container._editing = false;
        renderTileContent(container, room, state);
      });
    });
  }
}
