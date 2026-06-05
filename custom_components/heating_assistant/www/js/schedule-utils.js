/* schedule-utils.js — shared schedule helpers.
 *
 * A single source of truth for resolving the *currently active* (and next)
 * schedule period, mirroring the Python ``SchedulePeriod.matches()`` logic.
 * Used by the schedules page and the climate cards so the "NOW" indicator is
 * always consistent across the dashboard.
 */

/** Returns the period active right now, or null. Mirrors SchedulePeriod.matches(). */
export function findActivePeriod(periods) {
  if (!periods || !periods.length) return null;
  const now = new Date();
  const day = (now.getDay() + 6) % 7;
  const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  for (const p of periods) {
    const days = p.days || [0, 1, 2, 3, 4, 5, 6];
    // Periods where end <= start cross midnight (e.g. 22:00 → 06:00).
    if (p.end <= p.start) {
      const prevDay = (day + 6) % 7;
      const inFirstHalf = hhmm >= p.start && days.includes(day);
      const inSecondHalf = hhmm < p.end && days.includes(prevDay);
      if (inFirstHalf || inSecondHalf) return p;
    } else {
      if (!days.includes(day)) continue;
      if (hhmm >= p.start && hhmm < p.end) return p;
    }
  }
  return null;
}

/** Returns the next upcoming period today, or null. */
export function findNextPeriod(periods) {
  if (!periods || !periods.length) return null;
  const now = new Date();
  const day = (now.getDay() + 6) % 7;
  const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  let best = null;
  for (const p of periods) {
    const days = p.days || [0, 1, 2, 3, 4, 5, 6];
    if (!days.includes(day)) continue;
    if (p.start > hhmm) {
      if (!best || p.start < best.start) best = p;
    }
  }
  return best;
}

/** Returns { text, cls } describing a period's mode for display. */
export function periodModeDisplay(p) {
  if (p.mode === 'off') return { text: 'OFF', cls: 'sched-row__mode--off' };
  if (p.setpoint != null) return { text: `${p.setpoint}°C`, cls: 'sched-row__mode--comfort' };
  return { text: 'COMFORT', cls: 'sched-row__mode--comfort' };
}
