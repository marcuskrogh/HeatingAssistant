import { formatCountdown, entityAttr, entityLastUpdated, systemEntity } from '../utils.js?v=106';

export function createCountdown(state, small = false) {
  const container = document.createElement('div');
  container.className = `card countdown${small ? ' countdown--small' : ''}`;

  const dtS = getDtSeconds(state);
  const remaining = computeRemaining(state, dtS);

  renderCountdownContent(container, remaining, dtS, small, isSystemStopped(state));

  return {
    element: container,
    _dtS: dtS,
    tick(currentState) {
      const dt = getDtSeconds(currentState);
      const rem = computeRemaining(currentState, dt);
      updateCountdownDOM(container, rem, dt, isSystemStopped(currentState));
    },
  };
}

export function updateCountdown(countdown, state) {
  countdown._dtS = getDtSeconds(state);
}

// The control countdown is meaningless while the system is stopped: no control
// action is taken at the next tick, so the ring is frozen and the value is
// blanked rather than counting down. Driven by the same system_enabled flag the
// nav-bar START/STOP button reflects.
function isSystemStopped(state) {
  const enabled = entityAttr(state, systemEntity('system_summary'), 'system_enabled');
  return enabled === false;
}

function getDtSeconds(state) {
  const dt = entityAttr(state, systemEntity('mpc_performance'), 'dt_s');
  return dt ? parseFloat(dt) : 300;
}

function computeRemaining(state, dtS) {
  // Prefer the explicit MPC-run timestamp the coordinator publishes: it is
  // anchored to the actual internal solve schedule and is NOT bumped by the
  // fast UI refresh that writes entity state between solves.  Fall back to the
  // entity's HA last_updated for older integration versions.
  const lastRunTs = entityAttr(state, systemEntity('mpc_performance'), 'last_run_ts');
  let lastRunMs = null;
  if (lastRunTs != null) {
    const parsed = parseFloat(lastRunTs);
    if (!isNaN(parsed)) lastRunMs = parsed * 1000;
  }
  if (lastRunMs == null) {
    const lastUpdated = entityLastUpdated(state, systemEntity('mpc_performance'));
    if (!lastUpdated) return dtS;
    lastRunMs = lastUpdated.getTime();
  }

  const elapsed = (Date.now() - lastRunMs) / 1000;
  if (elapsed < 0) return dtS;
  const remaining = dtS - (elapsed % dtS);
  return remaining;
}

function renderCountdownContent(container, remaining, dtS, small, stopped) {
  const progress = stopped ? 0 : Math.min(1, Math.max(0, 1 - remaining / dtS));
  const circumference = 2 * Math.PI * 34;
  const dashOffset = circumference * (1 - progress);
  const ringClass = small ? 'countdown__ring countdown__ring--small' : 'countdown__ring';
  const valueText = stopped ? '—' : formatCountdown(remaining);
  const labelText = stopped ? 'STOPPED' : 'NEXT CONTROL';

  container.classList.toggle('countdown--paused', !!stopped);
  container.innerHTML = `
    <svg class="${ringClass}" viewBox="0 0 80 80">
      <circle class="countdown__ring-track" cx="40" cy="40" r="34" />
      <circle class="countdown__ring-fill" cx="40" cy="40" r="34"
        stroke-dasharray="${circumference}"
        stroke-dashoffset="${dashOffset}" />
    </svg>
    <span class="countdown__value">${valueText}</span>
    <span class="countdown__label">${labelText}</span>
  `;
}

function updateCountdownDOM(container, remaining, dtS, stopped) {
  const progress = stopped ? 0 : Math.min(1, Math.max(0, 1 - remaining / dtS));
  const circumference = 2 * Math.PI * 34;
  const dashOffset = circumference * (1 - progress);

  container.classList.toggle('countdown--paused', !!stopped);

  const ring = container.querySelector('.countdown__ring-fill');
  if (ring) {
    ring.setAttribute('stroke-dasharray', circumference);
    ring.setAttribute('stroke-dashoffset', dashOffset);
  }

  const valueEl = container.querySelector('.countdown__value');
  if (valueEl) valueEl.textContent = stopped ? '—' : formatCountdown(remaining);

  const labelEl = container.querySelector('.countdown__label');
  if (labelEl) labelEl.textContent = stopped ? 'STOPPED' : 'NEXT CONTROL';
}
