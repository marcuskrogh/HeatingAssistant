/* climate-card.js — Home-Assistant-style climate control card in the
 * Heating Assistant industrial design language.
 *
 * Shows the current (filtered) room temperature, the active setpoint, the
 * comfort corridor, and an inline stepper that lets the user retarget the
 * setpoint just like a native HA climate entity.  A slim track visualises the
 * comfort region and where the current temperature sits relative to it.
 *
 * Setpoint changes are applied optimistically and committed to HA after a
 * short debounce so rapid +/- presses collapse into a single service call —
 * mirroring the behaviour of the HA thermostat card.
 *
 * The room can also be turned off from the card via the power toggle.  When
 * off — whether from the user toggle or an active off-schedule — the card
 * collapses into a clear OFF state that hides the now-irrelevant setpoint,
 * comfort corridor and temperature marker.
 *
 * Usage:
 *   const card = createClimateCard({ temperature, setpoint, power,
 *                                    comfortLower, comfortUpper, off,
 *                                    onSetpointChange, onPowerToggle });
 *   container.appendChild(card.element);
 *   card.update({ temperature, setpoint, power, comfortLower, comfortUpper, off });
 *   card.destroy();                                  // on teardown
 */

import { formatTemperature } from '../utils.js';

const SP_STEP = 0.5;
const SP_MIN = 5;
const SP_MAX = 30;
const COMMIT_DEBOUNCE_MS = 700;
// After toggling power we optimistically hold the new state for a short window
// so the card reacts instantly; backend truth resumes once this elapses.
const POWER_OPTIMISTIC_MS = 6000;
const TRACK_HALF_WIDTH = 3; // minimum °C either side of the setpoint shown on the track

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

export function createClimateCard({
  temperature, setpoint, power, comfortLower, comfortUpper, off,
  onSetpointChange, onPowerToggle,
} = {}) {
  const container = document.createElement('div');
  container.className = 'card climate-card';

  const st = {
    temperature,
    setpoint: clampSetpoint(setpoint),
    power,
    comfortLower: numOrNull(comfortLower),
    comfortUpper: numOrNull(comfortUpper),
    off: !!off,          // backend off-state (user toggle or off-schedule)
    optimisticOff: null, // optimistic power override (null = follow backend)
    powerTimer: null,    // clears optimisticOff after POWER_OPTIMISTIC_MS
    editing: false,      // true while the user is mid-adjustment
    commitTimer: null,   // pending debounce timer id
  };

  container.innerHTML = `
    <div class="climate-card__header">
      <span class="climate-card__title">CLIMATE</span>
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
  `;

  const els = {
    status: container.querySelector('.climate-card__status'),
    power: container.querySelector('.climate-card__power'),
    current: container.querySelector('.climate-card__current-value'),
    target: container.querySelector('.climate-card__target-value'),
    down: container.querySelector('.climate-card__step--down'),
    up: container.querySelector('.climate-card__step--up'),
    comfort: container.querySelector('.climate-card__track-comfort'),
    marker: container.querySelector('.climate-card__track-marker'),
    trackMin: container.querySelector('.climate-card__track-min'),
    trackMax: container.querySelector('.climate-card__track-max'),
    comfortLabel: container.querySelector('.climate-card__track-comfort-label'),
  };

  function currentOff() {
    return st.optimisticOff !== null ? st.optimisticOff : st.off;
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
    if (typeof onPowerToggle === 'function') onPowerToggle(turnOff);
  }

  els.power.addEventListener('click', togglePower);

  function scheduleCommit() {
    if (st.commitTimer) clearTimeout(st.commitTimer);
    st.commitTimer = setTimeout(() => {
      st.commitTimer = null;
      st.editing = false;
      if (typeof onSetpointChange === 'function') onSetpointChange(st.setpoint);
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

  els.down.addEventListener('click', () => adjust(-SP_STEP));
  els.up.addEventListener('click', () => adjust(SP_STEP));

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

    // When off, the setpoint / comfort corridor / marker are irrelevant — the
    // CSS hides them via .climate-card--off, so skip the rest of the paint.
    if (off) return;

    els.target.textContent = st.setpoint.toFixed(1) + '°';
    els.down.disabled = st.setpoint <= SP_MIN;
    els.up.disabled = st.setpoint >= SP_MAX;

    // Corridor track: setpoint pinned at centre, the comfort region drawn as a
    // highlighted band, and the current temp marker sliding within it.  The
    // half-width grows to keep the comfort band comfortably inside the track.
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
      // Tint the marker by position relative to the comfort region (falling
      // back to a ±0.5 °C band around the setpoint when no corridor is known).
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
    update({ temperature, setpoint, power, comfortLower, comfortUpper, off } = {}) {
      if (temperature !== undefined) st.temperature = temperature;
      if (power !== undefined) st.power = power;
      if (comfortLower !== undefined) st.comfortLower = numOrNull(comfortLower);
      if (comfortUpper !== undefined) st.comfortUpper = numOrNull(comfortUpper);
      if (off !== undefined) st.off = !!off;
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
      if (st.powerTimer) {
        clearTimeout(st.powerTimer);
        st.powerTimer = null;
      }
    },
  };
}
