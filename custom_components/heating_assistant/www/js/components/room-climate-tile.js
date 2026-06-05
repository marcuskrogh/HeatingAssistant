/* room-climate-tile.js — Overview-page room card combining climate-card
 * visuals (temperature readout, setpoint stepper, comfort-corridor track)
 * with the schedule summary and a room-name header.
 *
 * The card is clickable: it navigates to #room/<slug>.  Setpoint +/- button
 * clicks and the power toggle are consumed locally (stopPropagation +
 * debounced HA service call) so they never trigger navigation.  Navigation is
 * also blocked while a commit timer is still pending.
 *
 * Two extra behaviours layer on top of the base climate visuals:
 *   1. The currently active schedule period is highlighted with a "NOW" badge,
 *      mirroring the schedules page so the user can see what is running.
 *   2. The room can be turned off from the card (power toggle).  When the room
 *      is off — whether from the user toggle or an active off-schedule — the
 *      card collapses into a clear OFF state that hides the now-irrelevant
 *      setpoint, comfort corridor and temperature marker.
 */

import { entityValue } from '../utils.js';
import { findActivePeriod } from '../schedule-utils.js';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';
const SP_STEP = 0.5;
const SP_MIN = 5;
const SP_MAX = 30;
const COMMIT_DEBOUNCE_MS = 700;
// After toggling power we optimistically hold the new state for a short window
// so the card reacts instantly; backend truth resumes once this elapses.
const POWER_OPTIMISTIC_MS = 6000;
const TRACK_HALF_WIDTH = 3;

function clampSetpoint(v) {
  const snapped = Math.round((v ?? 22) / SP_STEP) * SP_STEP;
  return Math.max(SP_MIN, Math.min(SP_MAX, snapped));
}

function numOrNull(v) {
  if (v === undefined || v === null) return null;
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}

function statusInfo(power) {
  const num = parseFloat(power);
  if (isNaN(num) || Math.abs(num) < 1) {
    return { label: 'IDLE', cls: 'climate-card__status--idle' };
  }
  if (num > 0) return { label: 'HEATING', cls: 'climate-card__status--heating' };
  return { label: 'COOLING', cls: 'climate-card__status--cooling' };
}

function getScheduleData(st, room) {
  if (st.scheduleData) {
    const found = st.scheduleData[room.slug] || st.scheduleData[room.name] || null;
    if (found) return found;
  }
  const roomSchedules = st.state[CONFIG_ENTITY]?.attributes?.room_schedules || {};
  return roomSchedules[room.slug] || roomSchedules[room.name] || null;
}

/** Resolve whether the room is currently off, combining the backend effective
 *  state with the active schedule period (which reacts a touch sooner). */
function resolveBackendOff(st, room, activePeriod) {
  if (activePeriod && activePeriod.mode === 'off') return true;
  const attrs = st.state[CONFIG_ENTITY]?.attributes || {};
  const active = attrs.room_active;
  if (active && room.slug in active) return active[room.slug] === false;
  const enabled = attrs.room_enabled;
  if (enabled && room.slug in enabled) return enabled[room.slug] === false;
  return false;
}

function buildScheduleHtml(schedData, activePeriod) {
  const periods = schedData?.periods || [];
  if (periods.length === 0) return '';

  const schedEnabled = schedData ? (schedData.enabled ?? true) : null;
  const badgeCls = schedEnabled === false
    ? 'room-tile__sched-badge room-tile__sched-badge--off'
    : 'room-tile__sched-badge room-tile__sched-badge--on';
  const badgeText = schedEnabled === false ? 'INACTIVE' : 'ACTIVE';

  const renderRow = (p, isActive) => {
    const mode = p.mode === 'off'
      ? `<span class="room-tile__sched-off">OFF</span>`
      : `<span class="room-tile__sched-sp">${p.setpoint != null ? p.setpoint + '°' : 'COMFORT'}</span>`;
    return `<div class="room-tile__sched-row${isActive ? ' room-tile__sched-row--active' : ''}">
      ${isActive ? '<span class="room-tile__sched-now">NOW</span>' : ''}
      <span class="room-tile__sched-name">${p.name || 'Period'}</span>
      <span class="room-tile__sched-time">${p.start}–${p.end}</span>
      ${mode}
    </div>`;
  };

  // Active period (if the schedule is enabled) is pinned to the top with a NOW
  // badge, mirroring the schedules page.  Remaining periods follow as a preview.
  const showActive = activePeriod && schedEnabled !== false;
  const others = showActive ? periods.filter((p) => p !== activePeriod) : periods.slice();
  const preview = others.slice(0, showActive ? 2 : 3);
  const overflow = others.length - preview.length;

  const activeHtml = showActive ? renderRow(activePeriod, true) : '';
  const rows = preview.map((p) => renderRow(p, false)).join('');
  const more = overflow > 0
    ? `<div class="room-tile__sched-more">+${overflow} more</div>`
    : '';

  return `<div class="room-tile__sched-header">
      <span class="room-tile__sched-title">SCHEDULE</span>
      <span class="${badgeCls}">${badgeText}</span>
    </div>
    ${activeHtml}${rows}${more}`;
}

export function createRoomClimateTile(room, state, hass, scheduleData) {
  const container = document.createElement('div');
  container.className = 'card card--clickable climate-card room-climate-tile';
  container.dataset.room = room.slug;

  const st = {
    state,
    scheduleData: scheduleData || null,
    hass: hass || null,
    temperature: entityValue(state, room.entities['temperature_filtered'] || room.entities['temperature_measured']),
    setpoint: clampSetpoint(entityValue(state, room.entities['setpoint'])),
    power: entityValue(state, room.entities['heating_power_measured']),
    comfortLower: numOrNull(entityValue(state, room.entities['constraint_lower'])),
    comfortUpper: numOrNull(entityValue(state, room.entities['constraint_upper'])),
    editing: false,
    commitTimer: null,
    optimisticOff: null,   // optimistic power override (null = follow backend)
    powerTimer: null,      // clears optimisticOff after POWER_OPTIMISTIC_MS
  };

  container.innerHTML = `
    <div class="climate-card__header">
      <span class="room-climate-tile__name">${room.name}</span>
      <div class="climate-card__header-right">
        <span class="climate-card__status"></span>
        <button class="climate-card__power" aria-label="Turn heating on or off" title="Turn heating on or off">⏻</button>
      </div>
    </div>
    <div class="climate-card__body">
      <div class="climate-card__current">
        <span class="climate-card__current-value"></span>
        <span class="climate-card__current-label">CURRENT</span>
      </div>
      <div class="climate-card__control">
        <button class="climate-card__step climate-card__step--down" aria-label="Lower setpoint">−</button>
        <div class="climate-card__target">
          <span class="climate-card__target-value"></span>
          <span class="climate-card__target-label">TARGET</span>
        </div>
        <button class="climate-card__step climate-card__step--up" aria-label="Raise setpoint">+</button>
      </div>
      <div class="climate-card__off-note">HEATING OFF</div>
    </div>
    <div class="climate-card__track">
      <span class="climate-card__track-comfort"></span>
      <span class="climate-card__track-setpoint"></span>
      <span class="climate-card__track-marker"></span>
    </div>
    <div class="climate-card__track-scale">
      <span class="climate-card__track-min"></span>
      <span class="climate-card__track-comfort-label"></span>
      <span class="climate-card__track-max"></span>
    </div>
    <div class="room-climate-tile__schedules room-tile__schedules" style="display:none"></div>
  `;

  const els = {
    status:       container.querySelector('.climate-card__status'),
    power:        container.querySelector('.climate-card__power'),
    current:      container.querySelector('.climate-card__current-value'),
    target:       container.querySelector('.climate-card__target-value'),
    down:         container.querySelector('.climate-card__step--down'),
    up:           container.querySelector('.climate-card__step--up'),
    comfort:      container.querySelector('.climate-card__track-comfort'),
    marker:       container.querySelector('.climate-card__track-marker'),
    trackMin:     container.querySelector('.climate-card__track-min'),
    trackMax:     container.querySelector('.climate-card__track-max'),
    comfortLabel: container.querySelector('.climate-card__track-comfort-label'),
    schedules:    container.querySelector('.room-climate-tile__schedules'),
  };

  els.down.addEventListener('click', (e) => { e.stopPropagation(); adjust(-SP_STEP); });
  els.up.addEventListener('click', (e) => { e.stopPropagation(); adjust(SP_STEP); });
  els.power.addEventListener('click', (e) => { e.stopPropagation(); togglePower(); });

  container.addEventListener('click', () => {
    if (!st.editing && !st.commitTimer) {
      window.location.hash = `#room/${room.slug}`;
    }
  });

  function scheduleCommit() {
    if (st.commitTimer) clearTimeout(st.commitTimer);
    st.commitTimer = setTimeout(() => {
      st.commitTimer = null;
      st.editing = false;
      if (st.hass) {
        st.hass.callService('climate', 'set_temperature', {
          entity_id: `climate.heating_assistant_${room.slug}`,
          temperature: st.setpoint,
        }).catch(() => {});
      }
    }, COMMIT_DEBOUNCE_MS);
  }

  function adjust(delta) {
    if (currentOff()) return; // setpoint is irrelevant while off
    const next = clampSetpoint(st.setpoint + delta);
    if (next === st.setpoint) return;
    st.setpoint = next;
    st.editing = true;
    paint();
    scheduleCommit();
  }

  /** Effective off-state: optimistic override wins, else backend/schedule. */
  function currentOff() {
    if (st.optimisticOff !== null) return st.optimisticOff;
    const activePeriod = findActivePeriod((getScheduleData(st, room)?.periods) || []);
    return resolveBackendOff(st, room, activePeriod);
  }

  function togglePower() {
    const turnOff = !currentOff();
    st.optimisticOff = turnOff;
    if (st.powerTimer) clearTimeout(st.powerTimer);
    st.powerTimer = setTimeout(() => {
      st.powerTimer = null;
      st.optimisticOff = null;
      paint();
    }, POWER_OPTIMISTIC_MS);
    paint();
    if (st.hass) {
      st.hass.callService('climate', turnOff ? 'turn_off' : 'turn_on', {
        entity_id: `climate.heating_assistant_${room.slug}`,
      }).catch(() => {});
    }
  }

  function paint() {
    const off = currentOff();
    container.classList.toggle('climate-card--off', off);
    els.power.classList.toggle('climate-card__power--off', off);

    if (off) {
      els.status.textContent = 'OFF';
      els.status.className = 'climate-card__status climate-card__status--off';
    } else {
      const info = statusInfo(st.power);
      els.status.textContent = info.label;
      els.status.className = 'climate-card__status ' + info.cls;
    }

    const num = parseFloat(st.temperature);
    if (isNaN(num)) {
      els.current.innerHTML = '<span class="climate-card__current-num">—</span>';
    } else {
      els.current.innerHTML =
        `<span class="climate-card__current-num">${num.toFixed(1)}</span>` +
        `<span class="climate-card__current-unit">°C</span>`;
    }

    // Schedule section is always meaningful (it explains the off-schedule and
    // when heating resumes), so paint it regardless of the off-state.
    const schedData = getScheduleData(st, room);
    const activePeriod = findActivePeriod(schedData?.periods || []);
    const html = buildScheduleHtml(schedData, activePeriod);
    if (html) {
      els.schedules.innerHTML = html;
      els.schedules.style.display = '';
    } else {
      els.schedules.style.display = 'none';
    }

    // When off, the setpoint / comfort corridor / marker are irrelevant — the
    // CSS hides them via .climate-card--off, so skip the rest of the paint.
    if (off) return;

    els.target.textContent = st.setpoint.toFixed(1) + '°';
    els.down.disabled = st.setpoint <= SP_MIN;
    els.up.disabled = st.setpoint >= SP_MAX;

    const hasComfort =
      st.comfortLower !== null && st.comfortUpper !== null &&
      st.comfortUpper > st.comfortLower;
    let half = TRACK_HALF_WIDTH;
    if (hasComfort) {
      const spread = Math.max(st.setpoint - st.comfortLower, st.comfortUpper - st.setpoint);
      half = Math.max(TRACK_HALF_WIDTH, spread + 0.75);
    }
    const lo = st.setpoint - half;
    const hi = st.setpoint + half;
    const toPct = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));

    els.trackMin.textContent = lo.toFixed(0) + '°';
    els.trackMax.textContent = hi.toFixed(0) + '°';

    if (hasComfort) {
      const l = toPct(st.comfortLower);
      const r = toPct(st.comfortUpper);
      els.comfort.style.display = '';
      els.comfort.style.left = l + '%';
      els.comfort.style.width = Math.max(0, r - l) + '%';
      els.comfortLabel.textContent =
        `COMFORT ${st.comfortLower.toFixed(1)}–${st.comfortUpper.toFixed(1)}°`;
      els.comfortLabel.style.display = '';
    } else {
      els.comfort.style.display = 'none';
      els.comfortLabel.style.display = 'none';
    }

    if (isNaN(num)) {
      els.marker.style.display = 'none';
    } else {
      els.marker.style.display = '';
      els.marker.style.left = Math.max(1, Math.min(99, toPct(num))) + '%';
      let tone = 'climate-card__track-marker--on';
      if (hasComfort) {
        if (num < st.comfortLower) tone = 'climate-card__track-marker--cool';
        else if (num > st.comfortUpper) tone = 'climate-card__track-marker--warm';
      } else {
        const dev = num - st.setpoint;
        if (dev <= -0.5) tone = 'climate-card__track-marker--cool';
        else if (dev >= 0.5) tone = 'climate-card__track-marker--warm';
      }
      els.marker.className = 'climate-card__track-marker ' + tone;
    }
  }

  paint();

  return {
    element: container,
    update(newState, newHass, newScheduleData) {
      if (newHass !== undefined) st.hass = newHass;
      if (newScheduleData !== undefined) st.scheduleData = newScheduleData;
      st.state = newState;

      st.temperature = entityValue(newState, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
      st.power = entityValue(newState, room.entities['heating_power_measured']);
      st.comfortLower = numOrNull(entityValue(newState, room.entities['constraint_lower']));
      st.comfortUpper = numOrNull(entityValue(newState, room.entities['constraint_upper']));

      const newSp = entityValue(newState, room.entities['setpoint']);
      if (newSp !== null && newSp !== undefined && !st.editing && !st.commitTimer) {
        st.setpoint = clampSetpoint(newSp);
      }

      paint();
    },
    destroy() {
      if (st.commitTimer) {
        clearTimeout(st.commitTimer);
        st.commitTimer = null;
      }
      if (st.powerTimer) {
        clearTimeout(st.powerTimer);
        st.powerTimer = null;
      }
    },
  };
}
