/* experiment-utils.js — shared system-identification experiment helpers.
 *
 * A single source of truth for resolving the experiment *currently exciting* a
 * room and for turning its time window into progress, mirroring the Python
 * ``Experiment.is_active_at`` / ``ExperimentManager.active_for_room`` logic so
 * the room cards agree with the backend about what is running and how far along
 * it is.  Also owns the small experiment-panel markup/paint shared by both the
 * overview room-climate-tile and the room-detail climate-card, so the special
 * "experiment in progress" look stays identical across surfaces.
 */

const TERMINAL_STATUSES = new Set(['completed', 'cancelled']);

/** Index a flat experiment list (from connection.listExperiments) by room slug.
 *  Returns a plain object: { slug: [experiment, …] }.  ``null``/undefined input
 *  yields an empty index so callers can treat a failed fetch as "no data". */
export function indexExperimentsByRoom(experiments) {
  const byRoom = {};
  if (!Array.isArray(experiments)) return byRoom;
  for (const exp of experiments) {
    const slug = exp && exp.room_slug;
    if (!slug) continue;
    (byRoom[slug] || (byRoom[slug] = [])).push(exp);
  }
  return byRoom;
}

/** Resolve the experiment currently exciting ``roomSlug`` (or null).
 *
 * ``experiments`` may be the raw list from the backend or a per-room index
 * produced by :func:`indexExperimentsByRoom`.  An experiment counts as active
 * when it is not terminal and ``now`` falls inside its [start, end) window —
 * matching ``Experiment.is_active_at``.  When several overlap the earliest one
 * wins, mirroring ``ExperimentManager.active_for_room``. */
export function findActiveExperiment(experiments, roomSlug, nowMs = Date.now()) {
  let candidates;
  if (Array.isArray(experiments)) {
    candidates = experiments.filter((e) => e && e.room_slug === roomSlug);
  } else if (experiments && typeof experiments === 'object') {
    candidates = experiments[roomSlug] || [];
  } else {
    return null;
  }
  const nowS = nowMs / 1000;
  const active = candidates.filter(
    (e) => !TERMINAL_STATUSES.has(e.status) &&
      e.start_ts != null && e.end_ts != null &&
      e.start_ts <= nowS && nowS < e.end_ts,
  );
  if (active.length === 0) return null;
  return active.reduce((earliest, e) => (e.start_ts < earliest.start_ts ? e : earliest));
}

/** Instant (UNIX s) at which active excitation stops, ahead of ``end_ts``.
 *  Mirrors ``Experiment.excitation_end_ts``: a settle/response buffer of
 *  ``settle_s`` is reserved at the tail, clamped so it never swallows the
 *  whole window. */
export function excitationEndTs(exp) {
  const start = exp.start_ts ?? 0;
  const end = exp.end_ts ?? 0;
  const settle = Math.min(Math.max(0, exp.settle_s ?? 0), Math.max(0, end - start));
  return end - settle;
}

/** Which phase an active experiment is in at ``now``: 'exciting' during active
 *  excitation, 'settling' during the post-excitation buffer, or null when the
 *  experiment is not currently inside its window. */
export function experimentPhase(exp, nowMs = Date.now()) {
  const nowS = nowMs / 1000;
  if (nowS < (exp.start_ts ?? 0) || nowS >= (exp.end_ts ?? 0)) return null;
  return nowS < excitationEndTs(exp) ? 'exciting' : 'settling';
}

/** Build shaded-band descriptors for every scheduled or ongoing experiment of a
 *  room, for overlaying on the room-level plots.  Returns an array of
 *  ``{ start, end, excitationEnd, label, status }`` with timestamps in **ms**,
 *  one per non-terminal experiment (so an upcoming run shows before it starts).
 *  ``experiments`` may be the raw backend list or a per-room index. */
export function experimentBands(experiments, roomSlug) {
  let list;
  if (Array.isArray(experiments)) {
    list = experiments.filter((e) => e && e.room_slug === roomSlug);
  } else if (experiments && typeof experiments === 'object') {
    list = experiments[roomSlug] || [];
  } else {
    return [];
  }
  const bands = [];
  for (const e of list) {
    if (!e || TERMINAL_STATUSES.has(e.status)) continue;
    if (e.start_ts == null || e.end_ts == null) continue;
    bands.push({
      start: e.start_ts * 1000,
      end: e.end_ts * 1000,
      excitationEnd: excitationEndTs(e) * 1000,
      label: e.name && e.name.trim() ? e.name.trim().toUpperCase() : 'EXPERIMENT',
      status: e.status,
    });
  }
  return bands;
}

/** Turn an experiment's time window into a progress snapshot at ``now``.
 *  Returns { pct, elapsedS, remainingS, totalS } with pct clamped to 0–100. */
export function experimentProgress(exp, nowMs = Date.now()) {
  const nowS = nowMs / 1000;
  const totalS = Math.max(0, (exp.end_ts ?? 0) - (exp.start_ts ?? 0));
  const elapsedS = Math.max(0, Math.min(totalS, nowS - (exp.start_ts ?? 0)));
  const remainingS = Math.max(0, (exp.end_ts ?? 0) - nowS);
  const pct = totalS > 0 ? (elapsedS / totalS) * 100 : 0;
  return { pct: Math.max(0, Math.min(100, pct)), elapsedS, remainingS, totalS };
}

/** Short, uppercase label for an excitation signal type. */
export function signalLabel(type) {
  return ({ prbs: 'PRBS', step: 'STEP', pulse: 'PULSE' }[type] || (type || '—').toUpperCase());
}

/** Compact "time left" phrasing for the progress readout (uppercase, on-brand). */
export function formatRemaining(seconds) {
  if (seconds == null || !isFinite(seconds) || seconds <= 0) return 'FINISHING';
  const h = seconds / 3600;
  if (h >= 1) return `${h.toFixed(1)} H LEFT`;
  const m = Math.round(seconds / 60);
  if (m >= 1) return `${m} MIN LEFT`;
  return '<1 MIN LEFT';
}

/** Clock-time window label, e.g. "23:00 → 05:00". */
export function formatWindow(exp) {
  const fmt = (ts) => {
    if (ts == null) return '—';
    const d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return '—';
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };
  return `${fmt(exp.start_ts)} → ${fmt(exp.end_ts)}`;
}

/** Inner markup for the experiment panel injected into a climate card.  The
 *  card hides it unless the ``--experiment`` modifier class is present. */
export function experimentPanelHtml() {
  return `
    <div class="climate-card__experiment">
      <div class="climate-card__exp-head">
        <span class="climate-card__exp-name"></span>
        <span class="climate-card__exp-signal"></span>
      </div>
      <div class="climate-card__exp-bar">
        <span class="climate-card__exp-bar-fill"></span>
      </div>
      <div class="climate-card__exp-meta">
        <span class="climate-card__exp-pct"></span>
        <span class="climate-card__exp-remaining"></span>
      </div>
      <div class="climate-card__exp-facts">
        <div class="climate-card__exp-fact">
          <span class="climate-card__exp-fact-label">WINDOW</span>
          <span class="climate-card__exp-fact-value climate-card__exp-window"></span>
        </div>
        <div class="climate-card__exp-fact">
          <span class="climate-card__exp-fact-label">SAFE BAND</span>
          <span class="climate-card__exp-fact-value climate-card__exp-safe"></span>
        </div>
      </div>
    </div>`;
}

/** Look up the panel elements once so paints are cheap. */
export function experimentPanelEls(container) {
  return {
    name:      container.querySelector('.climate-card__exp-name'),
    signal:    container.querySelector('.climate-card__exp-signal'),
    barFill:   container.querySelector('.climate-card__exp-bar-fill'),
    pct:       container.querySelector('.climate-card__exp-pct'),
    remaining: container.querySelector('.climate-card__exp-remaining'),
    window:    container.querySelector('.climate-card__exp-window'),
    safe:      container.querySelector('.climate-card__exp-safe'),
  };
}

/** Paint the full experiment panel (static facts + live progress). */
export function paintExperimentPanel(els, exp, nowMs = Date.now()) {
  if (!els || !exp) return;
  els.name.textContent = exp.name && exp.name.trim() ? exp.name : 'IDENTIFICATION EXPERIMENT';
  els.signal.textContent = signalLabel(exp.signal_type);
  els.window.textContent = formatWindow(exp);
  const lo = exp.min_temp != null ? `${Math.round(exp.min_temp)}` : '—';
  const hi = exp.max_temp != null ? `${Math.round(exp.max_temp)}` : '—';
  els.safe.textContent = `${lo}–${hi}°`;
  paintExperimentProgress(els, exp, nowMs);
}

/** Paint only the live-advancing progress bits (cheap; called on each tick). */
export function paintExperimentProgress(els, exp, nowMs = Date.now()) {
  if (!els || !exp) return;
  const { pct, remainingS } = experimentProgress(exp, nowMs);
  els.barFill.style.width = pct.toFixed(1) + '%';
  els.pct.textContent = Math.round(pct) + '%';
  // During the settle tail the heaters are released and the room is relaxing,
  // so flag it rather than implying excitation is still running.
  const settling = experimentPhase(exp, nowMs) === 'settling';
  els.remaining.textContent = settling
    ? `SETTLING · ${formatRemaining(remainingS)}`
    : formatRemaining(remainingS);
}
