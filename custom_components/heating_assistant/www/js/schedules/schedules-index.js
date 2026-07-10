import { findActivePeriod, findNextPeriod, periodModeDisplay, scheduleEnabledBadgeHtml, scheduleSectionHeaderHtml } from '../schedule-utils.js?v=96';
import { findNextScheduledExperiment } from '../experiment-utils.js?v=96';
import { setPanelHash } from '../panel-hash.js?v=96';
import { setScheduleEnabled } from '../ha-services.js?v=96';
import { getScheduleDataForRoom, makePeriodRow } from './schedules-shared.js?v=96';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

export function renderScheduleIndex(container, rooms, state, connection, hass) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'SCHEDULES';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Configure time-of-day schedules for each room. Click a card to view, edit, or add periods.';
  container.appendChild(desc);

  const grid = document.createElement('div');
  grid.className = 'grid-rooms';
  container.appendChild(grid);

  let cachedSchedules = {};

  function buildCards(roomSchedules, expsByRoom = {}) {
    grid.innerHTML = '';
    cachedSchedules = roomSchedules;

    for (const room of rooms) {
      const schedData = getScheduleDataForRoom(roomSchedules, room);
      const periods = schedData?.periods || [];
      const enabled = schedData?.enabled ?? true;

      const activePeriod = findActivePeriod(periods);
      const nextPeriod = findNextPeriod(periods);

      const roomExps = expsByRoom[room.slug] || [];
      const upcomingExps = roomExps.filter((e) => e.status === 'scheduled' || e.status === 'running');
      const activeExp = upcomingExps.find((e) => e.status === 'running') || null;
      const nextExp = findNextScheduledExperiment(upcomingExps);

      const card = document.createElement('div');
      card.className = 'card card--clickable sched-index-card';

      // ── Header: room name ────────────────────────────────────────────────
      const cardHeader = document.createElement('div');
      cardHeader.className = 'sched-index-card__header';
      cardHeader.innerHTML = `<span class="sched-index-card__name">${room.name}</span>`;
      card.appendChild(cardHeader);

      // ── Comfort periods section ─────────────────────────────────────────
      const comfortSection = document.createElement('div');
      comfortSection.className = 'sched-section';
      comfortSection.innerHTML = scheduleSectionHeaderHtml(
        'COMFORT PERIODS',
        scheduleEnabledBadgeHtml(enabled),
      );
      if (periods.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'sched-index-card__empty';
        empty.textContent = 'No periods configured — click to add';
        comfortSection.appendChild(empty);
      } else {
        const list = document.createElement('div');
        list.className = 'sched-index-card__list';

        const preview = periods.slice(0, 4);
        const overflow = periods.length - preview.length;

        for (const p of preview) {
          list.appendChild(makePeriodRow(p, p === activePeriod, p === nextPeriod));
        }

        if (overflow > 0) {
          const more = document.createElement('div');
          more.className = 'sched-index-card__overflow';
          more.textContent = `+${overflow} more…`;
          list.appendChild(more);
        }

        comfortSection.appendChild(list);
      }
      card.appendChild(comfortSection);

      // ── Experiments section ─────────────────────────────────────────────
      const expSection = document.createElement('div');
      expSection.className = 'sched-section';
      expSection.innerHTML = scheduleSectionHeaderHtml('EXPERIMENTS');
      if (upcomingExps.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'sched-index-card__empty';
        empty.textContent = 'No experiments scheduled';
        expSection.appendChild(empty);
      } else {
        const list = document.createElement('div');
        list.className = 'sched-index-card__list';
        const preview = upcomingExps.slice(0, 3);
        const overflow = upcomingExps.length - preview.length;
        for (const e of preview) {
          const row = document.createElement('div');
          row.innerHTML = experimentRowHtml(e, {
            isActive: e === activeExp,
            isNext: !activeExp && e === nextExp,
          });
          list.appendChild(row.firstElementChild);
        }
        if (overflow > 0) {
          const more = document.createElement('div');
          more.className = 'sched-index-card__overflow';
          more.textContent = `+${overflow} more…`;
          list.appendChild(more);
        }
        expSection.appendChild(list);
      }
      card.appendChild(expSection);

      card.addEventListener('click', () => {
        setPanelHash(`#schedules/${room.slug}`);
      });
      grid.appendChild(card);
    }
  }

  // Fetch schedules and experiments from the coordinator via WebSocket
  Promise.all([connection.getSchedules(), connection.listExperiments()]).then(([roomSchedules, experiments]) => {
    const expsByRoom = {};
    if (Array.isArray(experiments)) {
      for (const e of experiments) {
        if (e?.room_slug) {
          (expsByRoom[e.room_slug] || (expsByRoom[e.room_slug] = [])).push(e);
        }
      }
    }
    buildCards(roomSchedules, expsByRoom);
  });

  return {
    update(newState) {
      // On state update, re-fetch from WebSocket to stay in sync
      Promise.all([connection.getSchedules(), connection.listExperiments()]).then(([roomSchedules, experiments]) => {
        const expsByRoom = {};
        if (Array.isArray(experiments)) {
          for (const e of experiments) {
            if (e?.room_slug) {
              (expsByRoom[e.room_slug] || (expsByRoom[e.room_slug] = [])).push(e);
            }
          }
        }
        buildCards(roomSchedules, expsByRoom);
      });
    },
    destroy() {},
  };
}
