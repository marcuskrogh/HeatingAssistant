/* schedule-overview.js — read-only schedule summary card for the room-level
 * overview (room-detail) page.
 *
 * It reuses the same visual language as the schedules index cards
 * (.sched-index-card / .sched-row): an ENABLED/DISABLED badge followed by a
 * flat list of all configured periods.  The active period receives a NOW badge
 * and the next upcoming period receives a NEXT badge, applied inline in the
 * list.  The whole card is clickable and navigates to the editable schedule
 * detail page.
 *
 * Also shows any scheduled or running experiments for this room below the
 * period list.
 */

import { findActivePeriod, findNextPeriod } from '../schedule-utils.js';

/** Builds the inner HTML for a single period summary row. */
function periodRowHtml(p, isActive, isNext) {
  return `<div class="sched-row${isActive ? ' sched-row--active' : ''}${isNext ? ' sched-row--next' : ''}">
      ${isActive ? '<span class="sched-row__now-badge">NOW</span>' : ''}
      ${isNext ? '<span class="sched-index-card__next-label">NEXT</span>' : ''}
      <span class="sched-row__name">${p.name || 'Period'}</span>
      <span class="sched-row__time">${p.start}–${p.end}</span>
    </div>`;
}

function fmtOverviewExpWindow(exp) {
  if (!exp.start_ts) return '—';
  const d = new Date(exp.start_ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function render(card, schedData, experiments = []) {
  const periods = schedData?.periods || [];
  const enabled = schedData?.enabled ?? true;

  const activePeriod = findActivePeriod(periods);
  const nextPeriod = findNextPeriod(periods);

  let html = `<div class="sched-index-card__header">
      <span class="sched-index-card__name">SCHEDULE</span>
      <span class="sched-index-card__badge ${enabled ? 'sched-index-card__badge--on' : 'sched-index-card__badge--off'}">${enabled ? 'ENABLED' : 'DISABLED'}</span>
    </div>`;

  if (periods.length === 0) {
    html += `<div class="sched-index-card__empty">No periods configured — click to edit</div>`;
  } else {
    const rows = periods.map((p) => periodRowHtml(p, p === activePeriod, p === nextPeriod)).join('');
    html += `<div class="sched-index-card__list">${rows}</div>`;
  }

  // Experiment section
  const upcoming = experiments.filter((e) => e.status === 'scheduled' || e.status === 'running');
  if (upcoming.length > 0) {
    const expRows = upcoming.map((e) => {
      const isRunning = e.status === 'running';
      const cls = isRunning ? 'exp-status-badge--running' : 'exp-status-badge--scheduled';
      const label = isRunning ? 'RUNNING' : 'UPCOMING';
      const window = fmtOverviewExpWindow(e);
      return `<div class="sched-row">
        <span class="exp-status-badge ${cls}">${label}</span>
        <span class="sched-row__name">${e.name || 'Experiment'}</span>
        <span class="sched-row__time">${window}</span>
      </div>`;
    }).join('');
    html += `<div class="sched-index-card__sep"></div>
      <div class="sched-index-card__exp-label">EXPERIMENTS</div>
      <div class="sched-index-card__list">${expRows}</div>`;
  }

  card.innerHTML = html;
}

/**
 * Create a schedule-overview card.
 *   room         — the room descriptor (for context; not rendered directly)
 *   scheduleData — initial { enabled, periods } payload (may be null)
 *   onEdit       — optional click handler (navigates to the edit page)
 *   experiments  — optional array of experiments for this room
 */
export function createScheduleOverview(room, scheduleData, { onEdit, experiments } = {}) {
  const card = document.createElement('div');
  card.className = 'card sched-index-card' + (onEdit ? ' card--clickable' : '');
  if (onEdit) card.addEventListener('click', onEdit);

  let currentSchedData = scheduleData;
  let currentExperiments = experiments || [];

  render(card, currentSchedData, currentExperiments);

  return {
    element: card,
    update(newScheduleData, newExperiments) {
      if (newScheduleData !== undefined) currentSchedData = newScheduleData;
      if (newExperiments !== undefined) currentExperiments = newExperiments;
      render(card, currentSchedData, currentExperiments);
    },
  };
}
