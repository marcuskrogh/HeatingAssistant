/* room-tile.js — design system v2 + schedule data fixes.
 *
 * Changes from v2:
 *  - createRoomTile / updateRoomTile accept an optional scheduleData argument
 *    (pre-fetched from connection.getSchedules()) so tiles never depend on the
 *    entity-state attribute being fresh after a browser reload.
 *  - Active/inactive badge added to the schedule summary section.
 *  - All entity reading, HA service calls, optimistic updates, and the
 *    container._editing guard are fully retained.
 */

import { formatTemperature, formatPower, entityValue } from '../utils.js?v=80';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';
const SP_STEP = 0.5;
const SP_MIN = 5;
const SP_MAX = 30;

export function createRoomTile(room, state, hass, scheduleData) {
  const container = document.createElement('div');
  container.className = 'card card--clickable room-tile';
  container.dataset.room = room.slug;
  container._editing = false;
  container._hass = hass || null;
  container._scheduleData = scheduleData || null;

  container.addEventListener('click', () => {
    if (!container._editing) {
      window.location.hash = `#room/${room.slug}`;
    }
  });

  renderTileContent(container, room, state);
  return container;
}

export function updateRoomTile(container, room, state, hass, scheduleData) {
  if (hass) container._hass = hass;
  if (scheduleData !== undefined) container._scheduleData = scheduleData;
  if (container._editing) return;
  renderTileContent(container, room, state);
}

function renderTileContent(container, room, state) {
  const tempEntity = room.entities['temperature_filtered'] || room.entities['temperature_measured'];
  const powerEntity = room.entities['heating_power_measured'];
  const setpointEntity = room.entities['setpoint'];

  const temp    = tempEntity    ? entityValue(state, tempEntity)    : null;
  const power   = powerEntity   ? entityValue(state, powerEntity)   : null;
  const setpoint = setpointEntity ? entityValue(state, setpointEntity) : null;
  const isActive = power !== null && power > 0;

  // Prefer WebSocket-fetched schedule data (always fresh); fall back to the
  // entity state attribute so the tile still works without the WS fetch.
  let schedData = null;
  if (container._scheduleData) {
    schedData = container._scheduleData[room.slug] || container._scheduleData[room.name] || null;
  }
  if (!schedData) {
    const roomSchedules = state[CONFIG_ENTITY]?.attributes?.room_schedules || {};
    schedData = roomSchedules[room.slug] || roomSchedules[room.name] || null;
  }

  const periods = schedData?.periods || [];
  const schedEnabled = schedData ? (schedData.enabled ?? true) : null;

  let schedulesHtml = '';
  if (periods.length > 0) {
    const badgeCls = schedEnabled === false
      ? 'room-tile__sched-badge room-tile__sched-badge--off'
      : 'room-tile__sched-badge room-tile__sched-badge--on';
    const badgeText = schedEnabled === false ? 'INACTIVE' : 'ACTIVE';

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
    schedulesHtml = `
      <div class="room-tile__schedules">
        <div class="room-tile__sched-header">
          <span class="room-tile__sched-title">SCHEDULE</span>
          <span class="${badgeCls}">${badgeText}</span>
        </div>
        ${rows}${more}
      </div>`;
  }

  // Setpoint display value
  const spDisplay = setpoint !== null ? setpoint.toFixed(1) + '°' : '—';

  container.innerHTML = `
    <span class="room-tile__name">${room.name}</span>

    <div class="room-tile__row">
      <span class="room-tile__temp">${formatTemperature(temp)}</span>
      <span class="room-tile__power-row">
        <span class="room-tile__power">${formatPower(power)}</span>
        <span class="room-tile__status ${isActive ? 'room-tile__status--active' : 'room-tile__status--idle'}"></span>
      </span>
    </div>

    <div class="sp-control" title="Click to adjust setpoint">
      <div class="sp-control__meta">
        <span class="sp-control__label">SETPOINT</span>
        <span class="sp-control__value">${spDisplay}</span>
      </div>
      <span class="sp-control__hint">&#9998; ADJUST</span>
    </div>

    ${schedulesHtml}
  `;

  // Wire new .sp-control click → opens inline stepper
  const spControl = container.querySelector('.sp-control');
  if (spControl && container._hass) {
    spControl.addEventListener('click', (e) => {
      e.stopPropagation();
      if (container._editing) return;
      container._editing = true;

      const currentSp = (setpointEntity ? entityValue(state, setpointEntity) : null) ?? 22;
      let selected = Math.max(SP_MIN, Math.min(SP_MAX, Math.round(currentSp / SP_STEP) * SP_STEP));
      const hass = container._hass;

      // Replace .sp-control with the inline stepper
      spControl.outerHTML = `
        <div class="sp-edit">
          <button class="sp-edit__step sp-edit__down">−</button>
          <div class="sp-edit__readout">
            <span class="sp-edit__val">${selected.toFixed(1)}°</span>
            <span class="sp-edit__unit">TARGET</span>
          </div>
          <button class="sp-edit__step sp-edit__up">+</button>
          <button class="sp-edit__ok">&#10003;</button>
          <button class="sp-edit__cancel">&#10007;</button>
        </div>
      `;

      const spEdit = container.querySelector('.sp-edit');
      const valEl  = spEdit.querySelector('.sp-edit__val');

      function refresh() {
        valEl.textContent = selected.toFixed(1) + '°';
        spEdit.querySelector('.sp-edit__down').disabled = selected <= SP_MIN;
        spEdit.querySelector('.sp-edit__up').disabled   = selected >= SP_MAX;
      }

      spEdit.querySelector('.sp-edit__down').addEventListener('click', (e) => {
        e.stopPropagation();
        selected = Math.max(SP_MIN, selected - SP_STEP);
        refresh();
      });

      spEdit.querySelector('.sp-edit__up').addEventListener('click', (e) => {
        e.stopPropagation();
        selected = Math.min(SP_MAX, selected + SP_STEP);
        refresh();
      });

      spEdit.querySelector('.sp-edit__ok').addEventListener('click', async (e) => {
        e.stopPropagation();
        // Optimistic update: show new value immediately
        spEdit.outerHTML = `
          <div class="sp-control">
            <div class="sp-control__meta">
              <span class="sp-control__label">SETPOINT</span>
              <span class="sp-control__value">${selected.toFixed(1)}°</span>
            </div>
            <span class="sp-control__hint">&#9998; ADJUST</span>
          </div>
        `;
        container._editing = false;
        try {
          await hass.callService('climate', 'set_temperature', {
            entity_id: `climate.heating_assistant_${room.slug}`,
            temperature: selected,
          });
        } catch (_) {}
      });

      spEdit.querySelector('.sp-edit__cancel').addEventListener('click', (e) => {
        e.stopPropagation();
        container._editing = false;
        renderTileContent(container, room, state);
      });
    });
  }
}
