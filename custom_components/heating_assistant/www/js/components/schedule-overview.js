/* schedule-overview.js — read-only schedule summary card for the room-level
 * overview (room-detail) page.
 *
 * It reuses the same visual language as the schedules index cards
 * (.sched-index-card / .sched-row): an ENABLED/DISABLED badge, the currently
 * active period highlighted with a NOW badge (or the next upcoming period when
 * nothing is active), followed by the remaining periods.  The whole card is
 * clickable and navigates to the editable schedule detail page.
 */

import { findActivePeriod, findNextPeriod, periodModeDisplay } from '../schedule-utils.js';

/** Builds the inner HTML for a single period summary row. */
function periodRowHtml(p, isActive) {
  const { text, cls } = periodModeDisplay(p);
  return `<div class="sched-row${isActive ? ' sched-row--active' : ''}">
      ${isActive ? '<span class="sched-row__now-badge">NOW</span>' : ''}
      <span class="sched-row__name">${p.name || 'Period'}</span>
      <span class="sched-row__time">${p.start}–${p.end}</span>
      <span class="sched-row__mode ${cls}">${text}</span>
    </div>`;
}

function render(card, schedData) {
  const periods = schedData?.periods || [];
  const enabled = schedData?.enabled ?? true;

  // The active period is resolved regardless of the enabled flag (mirroring the
  // schedules index card); the badge conveys whether the schedule is live.
  const activePeriod = findActivePeriod(periods);
  const others = periods.filter((p) => p !== activePeriod);

  let html = `<div class="sched-index-card__header">
      <span class="sched-index-card__name">SCHEDULE</span>
      <span class="sched-index-card__badge ${enabled ? 'sched-index-card__badge--on' : 'sched-index-card__badge--off'}">${enabled ? 'ENABLED' : 'DISABLED'}</span>
    </div>`;

  if (activePeriod) {
    html += periodRowHtml(activePeriod, true);
  } else if (periods.length > 0) {
    const next = findNextPeriod(periods);
    if (next) {
      const { text, cls } = periodModeDisplay(next);
      html += `<div class="sched-index-card__no-active">
          <span class="sched-index-card__next-label">NEXT →</span>
          <span class="sched-row__name">${next.name || 'Period'}</span>
          <span class="sched-row__time">${next.start}–${next.end}</span>
          <span class="sched-row__mode ${cls}">${text}</span>
        </div>`;
    } else {
      html += `<div class="sched-index-card__no-active">No period active today</div>`;
    }
  } else {
    html += `<div class="sched-index-card__empty">No periods configured — click to edit</div>`;
  }

  if (others.length > 0) {
    const rows = others.map((p) => periodRowHtml(p, false)).join('');
    html += `<div class="sched-index-card__sep"></div>
      <div class="sched-index-card__list">${rows}</div>`;
  }

  card.innerHTML = html;
}

/**
 * Create a schedule-overview card.
 *   room         — the room descriptor (for context; not rendered directly)
 *   scheduleData — initial { enabled, periods } payload (may be null)
 *   onEdit       — optional click handler (navigates to the edit page)
 */
export function createScheduleOverview(room, scheduleData, { onEdit } = {}) {
  const card = document.createElement('div');
  card.className = 'card sched-index-card' + (onEdit ? ' card--clickable' : '');
  if (onEdit) card.addEventListener('click', onEdit);

  render(card, scheduleData);

  return {
    element: card,
    update(newScheduleData) {
      render(card, newScheduleData);
    },
  };
}
