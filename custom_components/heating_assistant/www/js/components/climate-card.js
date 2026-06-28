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

import { formatTemperature } from '../utils.js?v=82';
import {
  experimentPanelHtml, experimentPanelEls,
  paintExperimentPanel, paintExperimentProgress, experimentProgress,
} from '../experiment-utils.js?v=82';

const SP_STEP = 0.5;
const SP_MIN = 5;
const SP_MAX = 30;
// Comfort offset = symmetric ±band half-width around the setpoint the controller
// keeps the room inside.  Stepped on a 0.5 °C grid like the setpoint.
const OFFSET_STEP = 0.1;
const OFFSET_MIN = 0.1;
const OFFSET_MAX = 5.0;
const DEFAULT_OFFSET = 2.0;
const COMMIT_DEBOUNCE_MS = 700;
// After toggling power we optimistically hold the new state for a short window
// so the card reacts instantly; backend truth resumes once this elapses.
const POWER_OPTIMISTIC_MS = 6000;
const TRACK_HALF_WIDTH = 3; // minimum °C either side of the setpoint shown on the track
// Cadence at which the experiment progress bar self-advances between updates.
const PROGRESS_TICK_MS = 1000;

function clampSetpoint(v) {
  const snapped = Math.round((v ?? 22) / SP_STEP) * SP_STEP;
  return Math.max(SP_MIN, Math.min(SP_MAX, snapped));
}

function clampOffset(v) {
  const snapped = Math.round((v ?? DEFAULT_OFFSET) / OFFSET_STEP) * OFFSET_STEP;
  return Math.max(OFFSET_MIN, Math.min(OFFSET_MAX, snapped));
}

function numOrNull(v) {
  if (v === undefined || v === null) return null;
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}

/** Derive the comfort-band half-width [°C] from a constraint corridor, or null
 *  when the corridor is unavailable / degenerate. */
function offsetFromCorridor(lower, upper) {
  if (lower === null || upper === null || upper <= lower) return null;
  return (upper - lower) / 2;
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
  temperature, setpoint, power, comfortLower, comfortUpper, off, experiment,
  onSetpointChange, onComfortOffsetChange, onPowerToggle,
} = {}) {
  const container = document.createElement('div');
  container.className = 'card climate-card';

  const cl = numOrNull(comfortLower);
  const cu = numOrNull(comfortUpper);
  const st = {
    temperature,
    setpoint: clampSetpoint(setpoint),
    power,
    comfortOffset: offsetFromCorridor(cl, cu) ?? DEFAULT_OFFSET,
    off: !!off,          // backend off-state (user toggle or off-schedule)
    experiment: experiment || null, // active identification experiment, or null
    optimisticOff: null, // optimistic power override (null = follow backend)
    powerTimer: null,    // clears optimisticOff after POWER_OPTIMISTIC_MS
    progressTimer: null, // ticks the experiment progress bar while a run is live
    editing: false,      // true while the user is mid setpoint adjustment
    commitTimer: null,   // pending setpoint debounce timer id
    offsetEditing: false, // true while the user is mid comfort-offset adjustment
    offsetCommitTimer: null, // pending comfort-offset debounce timer id
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
    ${experimentPanelHtml()}
    <div class="climate-card__comfort">
      <span class="climate-card__comfort-title">COMFORT BAND</span>
      <button class="climate-card__offset-step climate-card__offset-step--down" aria-label="Narrow comfort band">−</button>
      <span class="climate-card__offset-value"></span>
      <button class="climate-card__offset-step climate-card__offset-step--up" aria-label="Widen comfort band">+</button>
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
    offsetValue: container.querySelector('.climate-card__offset-value'),
    offsetDown: container.querySelector('.climate-card__offset-step--down'),
    offsetUp: container.querySelector('.climate-card__offset-step--up'),
    comfort: container.querySelector('.climate-card__track-comfort'),
    marker: container.querySelector('.climate-card__track-marker'),
    trackMin: container.querySelector('.climate-card__track-min'),
    trackMax: container.querySelector('.climate-card__track-max'),
    comfortLabel: container.querySelector('.climate-card__track-comfort-label'),
    experiment: experimentPanelEls(container),
  };

  function currentOff() {
    return st.optimisticOff !== null ? st.optimisticOff : st.off;
  }

  /** Keep a 1 s timer running only while an experiment is in progress so the
   *  progress bar creeps forward between the (infrequent) state pushes. */
  function syncProgressTimer() {
    const live = st.experiment && experimentProgress(st.experiment).remainingS > 0;
    if (live && !st.progressTimer) {
      st.progressTimer = setInterval(() => {
        if (st.experiment) paintExperimentProgress(els.experiment, st.experiment);
      }, PROGRESS_TICK_MS);
    } else if (!live && st.progressTimer) {
      clearInterval(st.progressTimer);
      st.progressTimer = null;
    }
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

  function scheduleOffsetCommit() {
    if (st.offsetCommitTimer) clearTimeout(st.offsetCommitTimer);
    st.offsetCommitTimer = setTimeout(() => {
      st.offsetCommitTimer = null;
      st.offsetEditing = false;
      if (typeof onComfortOffsetChange === 'function') onComfortOffsetChange(st.comfortOffset);
    }, COMMIT_DEBOUNCE_MS);
  }

  function adjustOffset(delta) {
    if (currentOff()) return; // comfort band is irrelevant while off
    const next = clampOffset(st.comfortOffset + delta);
    if (next === st.comfortOffset) return;
    st.comfortOffset = next;
    st.offsetEditing = true;
    paint();
    scheduleOffsetCommit();
  }

  els.offsetDown.addEventListener('click', () => adjustOffset(-OFFSET_STEP));
  els.offsetUp.addEventListener('click', () => adjustOffset(OFFSET_STEP));

  function paint() {
    // A live identification experiment overrides the off-schedule (the backend
    // excites this room regardless), so it wins the card's visual mode.
    const experiment = st.experiment || null;
    syncProgressTimer();
    const off = !experiment && currentOff();

    container.classList.toggle('climate-card--experiment', !!experiment);
    container.classList.toggle('climate-card--off', off);
    els.power.classList.toggle('climate-card__power--off', off);

    if (experiment) {
      els.status.textContent = 'EXPERIMENT';
      els.status.className = 'climate-card__status climate-card__status--experiment';
      paintExperimentPanel(els.experiment, experiment);
    } else if (off) {
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

    // When off or running an experiment, the setpoint / comfort corridor /
    // marker are irrelevant — the CSS hides them via the .climate-card--off /
    // .climate-card--experiment modifiers, so skip the rest of the paint.
    if (off || experiment) return;

    els.target.textContent = st.setpoint.toFixed(1) + '°';
    els.down.disabled = st.setpoint <= SP_MIN;
    els.up.disabled = st.setpoint >= SP_MAX;

    // Comfort-band stepper: the symmetric ±offset the controller keeps the room
    // inside.  Adjusting it widens / narrows the corridor live (drawn below).
    els.offsetValue.textContent = '±' + st.comfortOffset.toFixed(1) + '°';
    els.offsetDown.disabled = st.comfortOffset <= OFFSET_MIN;
    els.offsetUp.disabled = st.comfortOffset >= OFFSET_MAX;

    // Corridor track: setpoint pinned at centre, the comfort region (setpoint ±
    // offset) drawn as a highlighted band, and the current temp marker sliding
    // within it.  The half-width grows to keep the band comfortably inside the
    // track and updates live as the user edits the setpoint or comfort offset.
    const hasComfort = st.comfortOffset !== null && st.comfortOffset > 0;
    const comfortLower = hasComfort ? st.setpoint - st.comfortOffset : null;
    const comfortUpper = hasComfort ? st.setpoint + st.comfortOffset : null;
    let half = TRACK_HALF_WIDTH;
    if (hasComfort) {
      half = Math.max(TRACK_HALF_WIDTH, st.comfortOffset + 0.75);
    }
    const lo = st.setpoint - half;
    const hi = st.setpoint + half;
    const toPct = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));

    els.trackMin.textContent = lo.toFixed(0) + '°';
    els.trackMax.textContent = hi.toFixed(0) + '°';

    if (hasComfort) {
      const l = toPct(comfortLower);
      const r = toPct(comfortUpper);
      els.comfort.style.display = '';
      els.comfort.style.left = l + '%';
      els.comfort.style.width = Math.max(0, r - l) + '%';
      els.comfortLabel.textContent =
        `COMFORT ${comfortLower.toFixed(1)}–${comfortUpper.toFixed(1)}°`;
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
        if (num < comfortLower) tone = 'climate-card__track-marker--cool';
        else if (num > comfortUpper) tone = 'climate-card__track-marker--warm';
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
    update({ temperature, setpoint, power, comfortLower, comfortUpper, off, experiment } = {}) {
      if (temperature !== undefined) st.temperature = temperature;
      if (power !== undefined) st.power = power;
      if (off !== undefined) st.off = !!off;
      if (experiment !== undefined) st.experiment = experiment || null;
      // Never overwrite the setpoint while the user is mid-edit or a commit is
      // still pending — the optimistic value must win until HA confirms it.
      if (setpoint !== undefined && setpoint !== null && !st.editing && !st.commitTimer) {
        st.setpoint = clampSetpoint(setpoint);
      }
      // The comfort offset is derived from the live constraint corridor, but —
      // like the setpoint — must not clobber an in-flight user adjustment.
      if (comfortLower !== undefined || comfortUpper !== undefined) {
        const derived = offsetFromCorridor(numOrNull(comfortLower), numOrNull(comfortUpper));
        if (derived !== null && !st.offsetEditing && !st.offsetCommitTimer) {
          st.comfortOffset = derived;
        }
      }
      paint();
    },
    destroy() {
      if (st.commitTimer) {
        clearTimeout(st.commitTimer);
        st.commitTimer = null;
      }
      if (st.offsetCommitTimer) {
        clearTimeout(st.offsetCommitTimer);
        st.offsetCommitTimer = null;
      }
      if (st.powerTimer) {
        clearTimeout(st.powerTimer);
        st.powerTimer = null;
      }
      if (st.progressTimer) {
        clearInterval(st.progressTimer);
        st.progressTimer = null;
      }
    },
  };
}
