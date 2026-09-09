import { formatCountdown, entityAttr, entityLastUpdated, systemEntity } from '../utils.js?v=127';

export const COUNTDOWN_CONTROL = {
  dtAttr: 'dt_s',
  lastRunAttr: 'last_nmpc_ts',
  label: 'NEXT CONTROL',
  defaultDt: 900,
  useEntityLastUpdated: false,
  missingRemaining: 'period',
};

export const COUNTDOWN_NMPC = {
  dtAttr: 'nmpc_period_s',
  lastRunAttr: 'last_nmpc_ts',
  label: 'NEXT NMPC',
  defaultDt: 900,
  useEntityLastUpdated: false,
  missingRemaining: 'due',
};

export function createCountdown(state, options = false) {
  const spec = resolveSpec(options);
  const container = document.createElement('div');
  container.className = `card countdown${spec.small ? ' countdown--small' : ''}`;

  const dtS = getDtSeconds(state, spec);
  const remaining = computeRemaining(state, dtS, spec);

  renderCountdownContent(container, remaining, dtS, spec.small, isSystemStopped(state), spec.label);
  setCountdownComputing(container, countdownIsComputing(state, spec));

  return {
    element: container,
    _dtS: dtS,
    _spec: spec,
    tick(currentState) {
      const dt = getDtSeconds(currentState, spec);
      const rem = computeRemaining(currentState, dt, spec);
      updateCountdownDOM(container, rem, dt, isSystemStopped(currentState), spec.label);
      setCountdownComputing(container, countdownIsComputing(currentState, spec));
    },
  };
}

export function updateCountdown(countdown, state) {
  const spec = countdown._spec || COUNTDOWN_CONTROL;
  countdown._dtS = getDtSeconds(state, spec);
  setCountdownComputing(countdown.element, countdownIsComputing(state, spec));
}

export function setCountdownComputing(container, computing) {
  if (!container) return;
  const on = !!computing;
  container.classList?.toggle('countdown--computing', on);
  const host = typeof container.closest === 'function'
    ? container.closest('.kpi-expand')
    : null;
  if (host?.classList) host.classList.toggle('countdown--computing', on);
}

/**
 * Overlay when the runtime flag is set, or for a short catch-up right after
 * a period wrap so a 5 s Ingress poll is not required to start the chrome.
 * After the catch-up, trust the flag: a stale result stamp must not keep
 * the overlay on while computing is no.
 */
export function countdownIsComputing(state, spec = false, nowMs = Date.now()) {
  const resolved = resolveSpec(spec);
  if (isSystemStopped(state)) return false;
  const entity = systemEntity('mpc_performance');
  const flagName = resolved.dtAttr === 'nmpc_period_s' ? 'nmpc_computing' : 'control_computing';
  if (entityAttr(state, entity, flagName)) return true;

  const dtS = getDtSeconds(state, resolved);
  const lastRunTs = parseFloat(entityAttr(state, entity, resolved.lastRunAttr));
  if (!Number.isFinite(dtS) || dtS <= 0 || !Number.isFinite(lastRunTs)) return false;

  const nowS = nowMs / 1000;
  const elapsed = nowS - lastRunTs;
  if (elapsed < 0) return false;
  const intoSlot = elapsed % dtS;
  const slotStart = nowS - intoSlot;
  const resultAttr = resolved.dtAttr === 'nmpc_period_s' ? 'nmpc_result_ts' : 'last_control_ran_ts';
  const resultTs = parseFloat(entityAttr(state, entity, resultAttr));
  if (Number.isFinite(resultTs) && resultTs >= slotStart - 0.05) return false;

  return intoSlot < wrapOverlayCapS(resolved);
}

export function countdownRemaining(state, options = false) {
  const spec = resolveSpec(options);
  const dtS = getDtSeconds(state, spec);
  return computeRemaining(state, dtS, spec);
}

// The control countdown is meaningless while the system is stopped: no control
// action is taken at the next tick, so the ring is frozen and the value is
// blanked rather than counting down. Driven by the same system_enabled flag the
// nav-bar START/STOP button reflects.
function isSystemStopped(state) {
  const enabled = entityAttr(state, systemEntity('system_summary'), 'system_enabled');
  return enabled === false;
}

/** Seconds to keep wrap overlay before a skipped worker is treated as idle. */
function wrapOverlayCapS(resolved) {
  return resolved.dtAttr === 'nmpc_period_s' ? 8 : 2.5;
}

function resolveSpec(options) {
  if (options === true || options === false) {
    return { ...COUNTDOWN_CONTROL, small: !!options };
  }
  return { ...COUNTDOWN_CONTROL, ...options };
}

function getDtSeconds(state, spec) {
  const dt = entityAttr(state, systemEntity('mpc_performance'), spec.dtAttr);
  return dt ? parseFloat(dt) : spec.defaultDt;
}

function computeRemaining(state, dtS, spec) {
  // Prefer the explicit timestamp the coordinator publishes: it is anchored to
  // the shared Start epoch so both rings stay on the same substepping grid.
  const lastRunTs = entityAttr(state, systemEntity('mpc_performance'), spec.lastRunAttr);
  let lastRunMs = null;
  if (lastRunTs != null) {
    const parsed = parseFloat(lastRunTs);
    if (!isNaN(parsed)) lastRunMs = parsed * 1000;
  }
  if (lastRunMs == null && spec.useEntityLastUpdated) {
    const lastUpdated = entityLastUpdated(state, systemEntity('mpc_performance'));
    if (lastUpdated) lastRunMs = lastUpdated.getTime();
  }
  if (lastRunMs == null) {
    return spec.missingRemaining === 'due' ? 0 : dtS;
  }

  const elapsed = (Date.now() - lastRunMs) / 1000;
  if (elapsed < 0) return dtS;
  const remaining = dtS - (elapsed % dtS);
  return remaining;
}

function renderCountdownContent(container, remaining, dtS, small, stopped, label) {
  const progress = stopped ? 0 : Math.min(1, Math.max(0, 1 - remaining / dtS));
  const circumference = 2 * Math.PI * 34;
  const dashOffset = circumference * (1 - progress);
  const ringClass = small ? 'countdown__ring countdown__ring--small' : 'countdown__ring';
  const valueText = stopped ? '—' : formatCountdown(remaining);
  const labelText = stopped ? 'STOPPED' : label;

  container.classList.toggle('countdown--paused', !!stopped);
  container.innerHTML = `
    <svg class="${ringClass}" viewBox="0 0 80 80">
      <g class="countdown__spin">
        <circle class="countdown__ring-track" cx="40" cy="40" r="34" />
      </g>
      <circle class="countdown__ring-fill" cx="40" cy="40" r="34"
        stroke-dasharray="${circumference}"
        stroke-dashoffset="${dashOffset}" />
    </svg>
    <span class="countdown__value">${valueText}</span>
    <span class="countdown__label">${labelText}</span>
  `;
}

function updateCountdownDOM(container, remaining, dtS, stopped, label) {
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
  if (labelEl) labelEl.textContent = stopped ? 'STOPPED' : label;
}
