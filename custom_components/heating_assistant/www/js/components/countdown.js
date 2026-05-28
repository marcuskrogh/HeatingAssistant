import { formatCountdown, entityAttr, entityLastUpdated, systemEntity } from '../utils.js';

export function createCountdown(state, small = false) {
  const container = document.createElement('div');
  container.className = `card countdown${small ? ' countdown--small' : ''}`;

  const dtS = getDtSeconds(state);
  const remaining = computeRemaining(state, dtS);

  renderCountdownContent(container, remaining, dtS, small);

  return {
    element: container,
    _dtS: dtS,
    tick(currentState) {
      const dt = getDtSeconds(currentState);
      const rem = computeRemaining(currentState, dt);
      renderCountdownContent(container, rem, dt, small);
    },
  };
}

export function updateCountdown(countdown, state) {
  countdown._dtS = getDtSeconds(state);
}

function getDtSeconds(state) {
  const dt = entityAttr(state, systemEntity('mpc_performance'), 'dt_s');
  return dt ? parseFloat(dt) : 300;
}

function computeRemaining(state, dtS) {
  const lastUpdated = entityLastUpdated(state, systemEntity('mpc_performance'));
  if (!lastUpdated) return dtS;

  const elapsed = (Date.now() - lastUpdated.getTime()) / 1000;
  return Math.max(0, dtS - elapsed);
}

function renderCountdownContent(container, remaining, dtS, small) {
  const progress = 1 - remaining / dtS;
  const circumference = 2 * Math.PI * 34;
  const dashOffset = circumference * (1 - progress);

  if (small) {
    container.innerHTML = `
      <span class="countdown__value">${formatCountdown(remaining)}</span>
      <span class="countdown__interval">Δt = ${Math.round(dtS)}s</span>
      <span class="countdown__label">NEXT CONTROL</span>
    `;
  } else {
    container.innerHTML = `
      <svg class="countdown__ring" viewBox="0 0 80 80">
        <circle class="countdown__ring-track" cx="40" cy="40" r="34" />
        <circle class="countdown__ring-fill" cx="40" cy="40" r="34"
          style="stroke-dasharray: ${circumference}; stroke-dashoffset: ${dashOffset};" />
      </svg>
      <span class="countdown__value">${formatCountdown(remaining)}</span>
      <span class="countdown__interval">Δt = ${Math.round(dtS)}s</span>
      <span class="countdown__label">NEXT CONTROL</span>
    `;
  }
}
