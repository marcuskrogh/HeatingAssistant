/* climate-card.js — Home-Assistant-style climate control card in the
 * Heating Assistant industrial design language.
 *
 * Shows the current (filtered) room temperature, the active setpoint, and an
 * inline stepper that lets the user retarget the setpoint just like a native
 * HA climate entity.  A slim corridor track visualises how far the current
 * temperature sits from the target.
 *
 * Setpoint changes are applied optimistically and committed to HA after a
 * short debounce so rapid +/- presses collapse into a single service call —
 * mirroring the behaviour of the HA thermostat card.
 *
 * Usage:
 *   const card = createClimateCard({ temperature, setpoint, power, onSetpointChange });
 *   container.appendChild(card.element);
 *   card.update({ temperature, setpoint, power });   // on every state tick
 *   card.destroy();                                  // on teardown
 */

import { formatTemperature } from '../utils.js';

const SP_STEP = 0.5;
const SP_MIN = 5;
const SP_MAX = 30;
const COMMIT_DEBOUNCE_MS = 700;
const TRACK_HALF_WIDTH = 3; // °C either side of the setpoint shown on the track

function clampSetpoint(v) {
  const snapped = Math.round((v ?? 22) / SP_STEP) * SP_STEP;
  return Math.max(SP_MIN, Math.min(SP_MAX, snapped));
}

function statusInfo(power) {
  const num = parseFloat(power);
  if (isNaN(num) || Math.abs(num) < 1) {
    return { label: 'IDLE', cls: 'climate-card__status--idle' };
  }
  if (num > 0) return { label: 'HEATING', cls: 'climate-card__status--heating' };
  return { label: 'COOLING', cls: 'climate-card__status--cooling' };
}

export function createClimateCard({ temperature, setpoint, power, onSetpointChange } = {}) {
  const container = document.createElement('div');
  container.className = 'card climate-card';

  const st = {
    temperature,
    setpoint: clampSetpoint(setpoint),
    power,
    editing: false,      // true while the user is mid-adjustment
    commitTimer: null,   // pending debounce timer id
  };

  container.innerHTML = `
    <div class="climate-card__header">
      <span class="climate-card__title">CLIMATE</span>
      <span class="climate-card__status"></span>
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
    </div>
    <div class="climate-card__track">
      <span class="climate-card__track-setpoint"></span>
      <span class="climate-card__track-marker"></span>
    </div>
    <div class="climate-card__track-scale">
      <span class="climate-card__track-min"></span>
      <span class="climate-card__track-mid">TARGET</span>
      <span class="climate-card__track-max"></span>
    </div>
  `;

  const els = {
    status: container.querySelector('.climate-card__status'),
    current: container.querySelector('.climate-card__current-value'),
    target: container.querySelector('.climate-card__target-value'),
    down: container.querySelector('.climate-card__step--down'),
    up: container.querySelector('.climate-card__step--up'),
    marker: container.querySelector('.climate-card__track-marker'),
    trackMin: container.querySelector('.climate-card__track-min'),
    trackMax: container.querySelector('.climate-card__track-max'),
  };

  function scheduleCommit() {
    if (st.commitTimer) clearTimeout(st.commitTimer);
    st.commitTimer = setTimeout(() => {
      st.commitTimer = null;
      st.editing = false;
      if (typeof onSetpointChange === 'function') onSetpointChange(st.setpoint);
    }, COMMIT_DEBOUNCE_MS);
  }

  function adjust(delta) {
    const next = clampSetpoint(st.setpoint + delta);
    if (next === st.setpoint) return;
    st.setpoint = next;
    st.editing = true;
    paint();
    scheduleCommit();
  }

  els.down.addEventListener('click', () => adjust(-SP_STEP));
  els.up.addEventListener('click', () => adjust(SP_STEP));

  function paint() {
    const info = statusInfo(st.power);
    els.status.textContent = info.label;
    els.status.className = 'climate-card__status ' + info.cls;

    const num = parseFloat(st.temperature);
    if (isNaN(num)) {
      els.current.innerHTML = '<span class="climate-card__current-num">—</span>';
    } else {
      els.current.innerHTML =
        `<span class="climate-card__current-num">${num.toFixed(1)}</span>` +
        `<span class="climate-card__current-unit">°C</span>`;
    }

    els.target.textContent = st.setpoint.toFixed(1) + '°';
    els.down.disabled = st.setpoint <= SP_MIN;
    els.up.disabled = st.setpoint >= SP_MAX;

    // Corridor track: setpoint pinned at centre, current temp marker slides
    // within ±TRACK_HALF_WIDTH °C of it.
    const lo = st.setpoint - TRACK_HALF_WIDTH;
    const hi = st.setpoint + TRACK_HALF_WIDTH;
    els.trackMin.textContent = lo.toFixed(0) + '°';
    els.trackMax.textContent = hi.toFixed(0) + '°';

    if (isNaN(num)) {
      els.marker.style.display = 'none';
    } else {
      els.marker.style.display = '';
      const pct = Math.max(1, Math.min(99, ((num - lo) / (hi - lo)) * 100));
      els.marker.style.left = pct + '%';
      // Tint the marker by deviation from target: cool below, warm above.
      const dev = num - st.setpoint;
      let tone = 'climate-card__track-marker--on';
      if (dev <= -0.5) tone = 'climate-card__track-marker--cool';
      else if (dev >= 0.5) tone = 'climate-card__track-marker--warm';
      els.marker.className = 'climate-card__track-marker ' + tone;
    }
  }

  paint();

  return {
    element: container,
    update({ temperature, setpoint, power } = {}) {
      if (temperature !== undefined) st.temperature = temperature;
      if (power !== undefined) st.power = power;
      // Never overwrite the setpoint while the user is mid-edit or a commit is
      // still pending — the optimistic value must win until HA confirms it.
      if (setpoint !== undefined && setpoint !== null && !st.editing && !st.commitTimer) {
        st.setpoint = clampSetpoint(setpoint);
      }
      paint();
    },
    destroy() {
      if (st.commitTimer) {
        clearTimeout(st.commitTimer);
        st.commitTimer = null;
      }
    },
  };
}
