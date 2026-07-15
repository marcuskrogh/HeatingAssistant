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

import { entityValue } from '../utils.js?v=99';
import { setPanelHash } from '../panel-hash.js?v=99';
import { findActivePeriod, findNextPeriod, periodRowHtml, scheduleEnabledBadgeHtml, scheduleSectionHeaderHtml } from '../schedule-utils.js?v=99';
import {
  findActiveExperiment, experimentPanelHtml, experimentPanelEls,
  paintExperimentPanel, paintExperimentProgress,
  experimentRowHtml, findNextScheduledExperiment,
} from '../experiment-utils.js?v=99';
import {
  setClimateTemperature,
  setRoomComfortOffset,
  turnClimateOff,
  turnClimateOn,
} from '../ha-services.js?v=99';
import { resolveRoomScheduleData } from '../schedules/schedules-shared.js?v=99';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';
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
const TRACK_HALF_WIDTH = 3;
// Cadence at which the experiment progress bar self-advances between state
// pushes so the fill creeps forward smoothly while a run is in progress.
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

function getScheduleData(st, room) {
  return resolveRoomScheduleData(room, st.scheduleData || {}, st.state);
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

function buildScheduleHtml(schedData, activePeriod, nextPeriod, roomExperiments = []) {
  const periods = schedData?.periods || [];
  const upcoming = roomExperiments.filter((e) => e.status === 'scheduled' || e.status === 'running');
  const activeExp = upcoming.find((e) => e.status === 'running') || null;
  const nextExp = findNextScheduledExperiment(upcoming);

  if (periods.length === 0 && upcoming.length === 0) return '';

  let html = '';

  // Comfort periods section
  html += `<div class="sched-section">
    ${scheduleSectionHeaderHtml('COMFORT PERIODS', scheduleEnabledBadgeHtml(schedData?.enabled ?? true))}`;

  if (periods.length === 0) {
    html += `<div class="sched-index-card__empty">No periods configured</div>`;
  } else {
    const preview = periods.slice(0, 3);
    const overflow = periods.length - preview.length;
    const rows = preview.map((p) => periodRowHtml(p, p === activePeriod, p === nextPeriod)).join('');
    const more = overflow > 0 ? `<div class="sched-index-card__overflow">+${overflow} more</div>` : '';
    html += `${rows}${more}`;
  }
  html += '</div>';

  // Experiments section
  html += `<div class="sched-section">
    ${scheduleSectionHeaderHtml('EXPERIMENTS')}`;

  if (upcoming.length === 0) {
    html += `<div class="sched-index-card__empty">No experiments scheduled</div>`;
  } else {
    const preview = upcoming.slice(0, 2);
    const overflow = upcoming.length - preview.length;
    const expRows = preview.map((e) => experimentRowHtml(e, {
      isActive: e === activeExp,
      isNext: !activeExp && e === nextExp,
    })).join('');
    const expMore = overflow > 0 ? `<div class="sched-index-card__overflow">+${overflow} more</div>` : '';
    html += `${expRows}${expMore}`;
  }
  html += '</div>';

  return html;
}

export function createRoomClimateTile(room, state, hass, scheduleData, experimentData) {
  const container = document.createElement('div');
  container.className = 'card card--clickable climate-card room-climate-tile';
  container.dataset.room = room.slug;

  const st = {
    state,
    scheduleData: scheduleData || null,
    experimentData: experimentData || null,
    hass: hass || null,
    temperature: entityValue(state, room.entities['temperature_filtered'] || room.entities['temperature_measured']),
    setpoint: clampSetpoint(entityValue(state, room.entities['setpoint'])),
    power: entityValue(state, room.entities['heating_power_measured']),
    comfortOffset: offsetFromCorridor(
      numOrNull(entityValue(state, room.entities['constraint_lower'])),
      numOrNull(entityValue(state, room.entities['constraint_upper'])),
    ) ?? DEFAULT_OFFSET,
    editing: false,
    commitTimer: null,
    offsetEditing: false,    // true while the user is mid comfort-offset adjustment
    offsetCommitTimer: null, // pending comfort-offset debounce timer id
    optimisticOff: null,   // optimistic power override (null = follow backend)
    progressTimer: null,   // ticks the experiment progress bar while a run is live
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
    <div class="room-climate-tile__schedules room-tile__schedules" style="display:none"></div>
  `;

  const els = {
    status:       container.querySelector('.climate-card__status'),
    power:        container.querySelector('.climate-card__power'),
    current:      container.querySelector('.climate-card__current-value'),
    target:       container.querySelector('.climate-card__target-value'),
    down:         container.querySelector('.climate-card__step--down'),
    up:           container.querySelector('.climate-card__step--up'),
    offsetValue:  container.querySelector('.climate-card__offset-value'),
    offsetDown:   container.querySelector('.climate-card__offset-step--down'),
    offsetUp:     container.querySelector('.climate-card__offset-step--up'),
    comfort:      container.querySelector('.climate-card__track-comfort'),
    marker:       container.querySelector('.climate-card__track-marker'),
    trackMin:     container.querySelector('.climate-card__track-min'),
    trackMax:     container.querySelector('.climate-card__track-max'),
    comfortLabel: container.querySelector('.climate-card__track-comfort-label'),
    schedules:    container.querySelector('.room-climate-tile__schedules'),
    experiment:   experimentPanelEls(container),
  };

  els.down.addEventListener('click', (e) => { e.stopPropagation(); adjust(-SP_STEP); });
  els.up.addEventListener('click', (e) => { e.stopPropagation(); adjust(SP_STEP); });
  els.offsetDown.addEventListener('click', (e) => { e.stopPropagation(); adjustOffset(-OFFSET_STEP); });
  els.offsetUp.addEventListener('click', (e) => { e.stopPropagation(); adjustOffset(OFFSET_STEP); });
  els.power.addEventListener('click', (e) => { e.stopPropagation(); togglePower(); });

  container.addEventListener('click', () => {
    if (!st.editing && !st.commitTimer && !st.offsetEditing && !st.offsetCommitTimer) {
      setPanelHash(`#room/${room.slug}`);
    }
  });

  function scheduleCommit() {
    if (st.commitTimer) clearTimeout(st.commitTimer);
    st.commitTimer = setTimeout(() => {
      st.commitTimer = null;
      st.editing = false;
      if (st.hass) {
        setClimateTemperature(
          st.hass,
          `climate.heating_assistant_${room.slug}`,
          st.setpoint,
        ).catch(() => {});
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

  function scheduleOffsetCommit() {
    if (st.offsetCommitTimer) clearTimeout(st.offsetCommitTimer);
    st.offsetCommitTimer = setTimeout(() => {
      st.offsetCommitTimer = null;
      st.offsetEditing = false;
      if (st.hass) {
        setRoomComfortOffset(st.hass, room.slug, st.comfortOffset).catch(() => {});
      }
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

  /** The system-identification experiment currently exciting this room, or null. */
  function currentExperiment() {
    return findActiveExperiment(st.experimentData, room.slug);
  }

  /** Keep a 1 s timer running only while an experiment is in progress so the
   *  progress bar creeps forward between the (infrequent) state pushes. */
  function syncProgressTimer(exp) {
    if (exp && !st.progressTimer) {
      st.progressTimer = setInterval(() => {
        const live = currentExperiment();
        if (live) paintExperimentProgress(els.experiment, live);
        else { paint(); } // run just ended — repaint to drop the experiment look
      }, PROGRESS_TICK_MS);
    } else if (!exp && st.progressTimer) {
      clearInterval(st.progressTimer);
      st.progressTimer = null;
    }
  }

  /** Effective off-state: optimistic override wins, else backend/schedule. */
  function currentOff() {
    if (st.optimisticOff !== null) return st.optimisticOff;
    const activePeriod = findActivePeriod((getScheduleData(st, room)?.periods) || []);
    return resolveBackendOff(st, room, activePeriod);
  }

  /** Drop the optimistic power override once the backend reports the same state. */
  function reconcileOptimisticPower() {
    if (st.optimisticOff === null) return;
    const activePeriod = findActivePeriod((getScheduleData(st, room)?.periods) || []);
    const backendOff = resolveBackendOff(st, room, activePeriod);
    if (st.optimisticOff === backendOff) {
      st.optimisticOff = null;
    }
  }

  function togglePower() {
    const turnOff = !currentOff();
    st.optimisticOff = turnOff;
    paint();
    if (st.hass) {
      const entityId = `climate.heating_assistant_${room.slug}`;
      (turnOff ? turnClimateOff(st.hass, entityId) : turnClimateOn(st.hass, entityId))
        .catch(() => {
          st.optimisticOff = null;
          paint();
        });
    }
  }

  function paint() {
    // A live identification experiment overrides the off-schedule (the backend
    // excites this room regardless), so it wins the card's visual mode.
    const experiment = currentExperiment();
    syncProgressTimer(experiment);
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

    // Schedule section is always meaningful (it explains the off-schedule and
    // when heating resumes), so paint it regardless of the off-state.
    const schedData = getScheduleData(st, room);
    const periods = schedData?.periods || [];
    const activePeriod = findActivePeriod(periods);
    const nextPeriod = findNextPeriod(periods);
    const roomExps = (st.experimentData && st.experimentData[room.slug]) ? st.experimentData[room.slug] : [];
    const html = buildScheduleHtml(schedData, activePeriod, nextPeriod, roomExps);
    if (html) {
      els.schedules.innerHTML = html;
      els.schedules.style.display = '';
    } else {
      els.schedules.style.display = 'none';
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

    // Corridor (setpoint ± offset) — redrawn live as the user edits either the
    // setpoint or the comfort offset.
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
    update(newState, newHass, newScheduleData, newExperimentData) {
      if (newHass !== undefined) st.hass = newHass;
      if (newScheduleData !== undefined) st.scheduleData = newScheduleData;
      if (newExperimentData !== undefined) st.experimentData = newExperimentData;
      st.state = newState;

      st.temperature = entityValue(newState, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
      st.power = entityValue(newState, room.entities['heating_power_measured']);

      const newSp = entityValue(newState, room.entities['setpoint']);
      if (newSp !== null && newSp !== undefined && !st.editing && !st.commitTimer) {
        st.setpoint = clampSetpoint(newSp);
      }

      // Derive the comfort offset from the live corridor, but never clobber an
      // in-flight user adjustment (mirrors the setpoint guard above).
      const derived = offsetFromCorridor(
        numOrNull(entityValue(newState, room.entities['constraint_lower'])),
        numOrNull(entityValue(newState, room.entities['constraint_upper'])),
      );
      if (derived !== null && !st.offsetEditing && !st.offsetCommitTimer) {
        st.comfortOffset = derived;
      }

      reconcileOptimisticPower();
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
      if (st.progressTimer) {
        clearInterval(st.progressTimer);
        st.progressTimer = null;
      }
    },
  };
}
