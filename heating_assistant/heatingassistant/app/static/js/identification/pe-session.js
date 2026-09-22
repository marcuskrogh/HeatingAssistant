import { renderPeProgress } from './pe-progress.js?v=171';
import { cancelParameterEstimation } from '../ha-services.js?v=171';

const POLL_MS = 1000;
const CLOCK_MS = 250;
const FALLBACK_CAP_MS = 30 * 60 * 1000;

export function peSessionOf(connection) {
  return connection && connection.peSession ? connection.peSession : null;
}

export function attachPeSession({ overlayHost, chips = [], connection, getHass }) {
  const overlay = document.createElement('div');
  overlay.className = 'pe-progress-overlay';
  overlay.hidden = true;
  overlayHost.appendChild(overlay);

  let job = null;
  let dismissed = true;
  let clockTimer = null;
  let pollTimer = null;
  const listeners = new Set();

  function running() {
    return Boolean(job && job.status === 'running');
  }

  function overlayOpen() {
    return !overlay.hidden;
  }

  function snapshot() {
    return { job, running: running(), dismissed, overlayOpen: overlayOpen() };
  }

  function notify() {
    const snap = snapshot();
    for (const fn of listeners) {
      try { fn(snap); } catch (_) { /* page unmounted */ }
    }
    paintChips();
  }

  function subscribe(fn) {
    listeners.add(fn);
    fn(snapshot());
    return () => listeners.delete(fn);
  }

  function lockBackground(on) {
    const shell = overlayHost.querySelector('.shell');
    if (!shell) return;
    shell.style.overflowY = on ? 'hidden' : '';
  }

  function stopClock() {
    if (clockTimer != null) {
      window.clearInterval(clockTimer);
      clockTimer = null;
    }
  }

  function startClock() {
    if (clockTimer != null) return;
    clockTimer = window.setInterval(() => {
      if (!overlay.hidden && job) renderPeProgress(overlay, job);
    }, CLOCK_MS);
  }

  function stopPoll() {
    if (pollTimer != null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPoll() {
    if (pollTimer != null) return;
    pollTimer = window.setInterval(() => { refresh(); }, POLL_MS);
  }

  function paintOverlay() {
    if (overlay.hidden || !job) return;
    renderPeProgress(overlay, job);
  }

  function paintChips() {
    const busy = running();
    for (const chip of chips) {
      if (!chip) continue;
      chip.hidden = !busy;
      chip.setAttribute('aria-hidden', busy ? 'false' : 'true');
    }
  }

  function show() {
    dismissed = false;
    overlay.hidden = false;
    lockBackground(true);
    overlay.scrollTop = 0;
    if (!job) job = { status: 'running' };
    paintOverlay();
    startClock();
    if (running()) startPoll();
    notify();
  }

  function hide() {
    dismissed = true;
    overlay.hidden = true;
    overlay.innerHTML = '';
    lockBackground(false);
    stopClock();
    notify();
  }

  async function refresh() {
    if (!connection || typeof connection.getPeJob !== 'function') return job;
    const next = await connection.getPeJob();
    if (next && typeof next === 'object') job = next;
    if (running()) startPoll();
    else stopPoll();
    if (!overlay.hidden) paintOverlay();
    if (!running() && overlay.hidden) stopClock();
    notify();
    return job;
  }

  async function stopJob() {
    const hass = typeof getHass === 'function' ? getHass() : null;
    if (hass) {
      try { await cancelParameterEstimation(hass); } catch (_) { /* already done */ }
    }
    if (job && job.status === 'running') {
      job = { ...job, message: 'Stopping…' };
    }
    dismissed = false;
    show();
    await refresh();
  }

  async function waitUntilSettled() {
    let originMs = Date.now();
    let capMs = FALLBACK_CAP_MS;
    startPoll();
    while (Date.now() - originMs < capMs) {
      const current = await refresh();
      if (current != null) {
        const capS = Number(current.cap_s);
        if (Number.isFinite(capS) && capS > 0) capMs = capS * 1000;
        const startedAt = Number(current.started_at);
        if (Number.isFinite(startedAt) && startedAt > 1e9) {
          originMs = startedAt * 1000;
        }
        const status = current.status || 'idle';
        if (status === 'success') return current;
        if (status === 'cancelled') {
          const err = new Error(current.message || 'Estimation stopped');
          err.peCancelled = true;
          throw err;
        }
        if (status === 'error') {
          throw new Error(current.message || 'Estimation failed');
        }
      }
      await new Promise((res) => setTimeout(res, POLL_MS));
    }
    try { await stopJob(); } catch (_) { /* job may already have finished */ }
    throw new Error('Parameter estimation timed out');
  }

  function noteStarted(seed) {
    job = seed && typeof seed === 'object' ? { status: 'running', ...seed } : { status: 'running' };
    dismissed = false;
    startPoll();
    show();
  }

  overlay.addEventListener('click', (ev) => {
    const closeBtn = ev.target.closest('[data-pe-close]');
    if (closeBtn && overlay.contains(closeBtn)) {
      hide();
      return;
    }
    const stopBtn = ev.target.closest('[data-pe-stop]');
    if (stopBtn && overlay.contains(stopBtn)) {
      stopJob();
    }
  });

  for (const chip of chips) {
    if (!chip) continue;
    chip.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      show();
    });
  }

  function destroy() {
    stopPoll();
    stopClock();
    lockBackground(false);
    overlay.remove();
    listeners.clear();
    if (connection && connection.peSession === api) connection.peSession = null;
  }

  const api = {
    show,
    hide,
    stop: stopJob,
    refresh,
    waitUntilSettled,
    noteStarted,
    isRunning: running,
    subscribe,
    destroy,
    getJob: () => job,
  };

  if (connection) connection.peSession = api;
  refresh();
  return api;
}

export function mountPeRunningBanner(container, session) {
  if (!container || !session) return () => {};
  const banner = document.createElement('div');
  banner.className = 'pe-running-banner';
  banner.hidden = true;
  banner.innerHTML = `
    <div class="pe-running-banner__copy">
      <span class="pe-running-banner__dot" aria-hidden="true"></span>
      <span class="pe-running-banner__text">Parameter estimation is running</span>
    </div>
    <div class="pe-running-banner__actions">
      <button type="button" class="btn btn--ghost btn--sm" data-pe-open>Show progress</button>
      <button type="button" class="btn btn--ghost btn--sm" data-pe-stop>Stop</button>
    </div>
  `;
  container.insertBefore(banner, container.firstChild);
  banner.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-pe-open]')) {
      session.show();
      return;
    }
    if (ev.target.closest('[data-pe-stop]')) {
      session.stop();
    }
  });
  const unsub = session.subscribe((snap) => {
    banner.hidden = !snap.running;
  });
  return () => {
    unsub();
    banner.remove();
  };
}
