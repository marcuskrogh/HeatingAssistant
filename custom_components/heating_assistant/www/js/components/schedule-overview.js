/* schedule-overview.js — read-only schedule summary card for the room-level
 * overview (room-detail) page.
 *
 * Shows comfort periods and identification experiments as two parallel sections
 * with the same sched-row visual language used on the schedules index and
 * overview room cards.  The whole card is clickable and navigates to the
 * editable schedule detail page.
 */

import {
  findActivePeriod, findNextPeriod,
  periodRowHtml, scheduleEnabledBadgeHtml, scheduleSectionHeaderHtml,
} from '../schedule-utils.js?v=83';
import {
  experimentRowHtml, findNextScheduledExperiment,
} from '../experiment-utils.js?v=83';

function render(card, schedData, experiments = []) {
  const periods = schedData?.periods || [];
  const enabled = schedData?.enabled ?? true;
  const activePeriod = findActivePeriod(periods);
  const nextPeriod = findNextPeriod(periods);

  const upcoming = experiments.filter((e) => e.status === 'scheduled' || e.status === 'running');
  const activeExp = upcoming.find((e) => e.status === 'running') || null;
  const nextExp = findNextScheduledExperiment(upcoming);

  let html = '';

  // Comfort periods section
  html += `<div class="sched-section">
    ${scheduleSectionHeaderHtml('COMFORT PERIODS', scheduleEnabledBadgeHtml(enabled))}`;

  if (periods.length === 0) {
    html += `<div class="sched-index-card__empty">No periods configured — click to edit</div>`;
  } else {
    const rows = periods.map((p) => periodRowHtml(p, p === activePeriod, p === nextPeriod)).join('');
    html += `<div class="sched-index-card__list">${rows}</div>`;
  }
  html += '</div>';

  // Experiments section — always shown so the two categories are recognisable
  html += `<div class="sched-section">
    ${scheduleSectionHeaderHtml('EXPERIMENTS')}`;

  if (upcoming.length === 0) {
    html += `<div class="sched-index-card__empty">No experiments scheduled</div>`;
  } else {
    const expRows = upcoming.map((e) => experimentRowHtml(e, {
      isActive: e === activeExp,
      isNext: !activeExp && e === nextExp,
    })).join('');
    html += `<div class="sched-index-card__list">${expRows}</div>`;
  }
  html += '</div>';

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
