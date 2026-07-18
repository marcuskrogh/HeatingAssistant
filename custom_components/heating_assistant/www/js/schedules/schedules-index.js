import {
  escapeHtml,
  findActivePeriod,
  findNextPeriod,
  isPeriodInactive,
  partitionPeriods,
  scheduleEnabledBadgeHtml,
  scheduleSectionHeaderHtml,
  serializeSchedulePeriod,
} from '../schedule-utils.js?v=111';
import { experimentRowHtml, findNextScheduledExperiment } from '../experiment-utils.js?v=111';
import { setPanelHash } from '../panel-hash.js?v=111';
import { updateRoomSchedule } from '../ha-services.js?v=111';
import { makePeriodRow, mergeRoomSchedulesWithState, patchStateSchedule, resolveRoomScheduleData } from './schedules-shared.js?v=111';

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

function getDefaults(st, room) {
  const config = st[CONFIG_ENTITY]?.attributes || {};
  return {
    setpoint: config.room_setpoints?.[room.slug] ?? 21,
    comfort_offset: config.room_comfort_offsets?.[room.slug] ?? config.comfort_offset ?? 2.0,
    tracking_weight: 1.0,
    energy_weight: 1.0,
    frost_protection: 12,
  };
}

async function togglePeriodEnabled(hass, state, room, periods, periodIndex) {
  const next = periods.map((p, idx) => ({
    ...p,
    days: [...(p.days || [0, 1, 2, 3, 4, 5, 6])],
    enabled: idx === periodIndex ? !(p.enabled !== false) : p.enabled !== false,
  }));
  const defaults = getDefaults(state, room);
  const payload = next.map((p) => serializeSchedulePeriod(p, defaults));
  await updateRoomSchedule(hass, room.slug, payload);
  patchStateSchedule(state, room.slug, payload);
  return payload;
}

function bindPeriodToggle(row, hass, state, room, periods, periodIndex, enabled, rebuild) {
  const toggleBtn = row.querySelector('[data-period-enable]');
  if (!toggleBtn) return;
  toggleBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    toggleBtn.disabled = true;
    try {
      const updated = await togglePeriodEnabled(hass, state, room, periods, periodIndex);
      periods.splice(0, periods.length, ...updated.map((period) => ({
        ...period,
        days: [...(period.days || [0, 1, 2, 3, 4, 5, 6])],
      })));
      rebuild({ enabled, periods: [...periods] });
    } catch (err) {
      toggleBtn.disabled = false;
    }
  });
}

function appendPeriodRows(list, entries, activePeriod, nextPeriod, hass, state, room, periods, enabled, rebuild) {
  for (const { p, index } of entries) {
    const row = makePeriodRow(p, p === activePeriod, p === nextPeriod, index);
    bindPeriodToggle(row, hass, state, room, periods, index, enabled, rebuild);
    list.appendChild(row);
  }
}

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
  let cachedExpsByRoom = {};
  let scheduleLoadGen = 0;

  function buildCards(roomSchedules, expsByRoom = cachedExpsByRoom) {
    grid.innerHTML = '';
    const mergedSchedules = mergeRoomSchedulesWithState(roomSchedules, state);
    cachedSchedules = mergedSchedules;
    cachedExpsByRoom = expsByRoom;

    for (const room of rooms) {
      const schedData = resolveRoomScheduleData(room, mergedSchedules, state);
      const periods = schedData?.periods || [];
      const enabled = schedData?.enabled ?? true;

      const { active } = partitionPeriods(periods);
      const activePeriod = findActivePeriod(active);
      const nextPeriod = findNextPeriod(active);

      const activeEntries = [];
      const inactiveEntries = [];
      periods.forEach((p, index) => {
        if (isPeriodInactive(p)) inactiveEntries.push({ p, index });
        else activeEntries.push({ p, index });
      });

      const roomExps = expsByRoom[room.slug] || [];
      const upcomingExps = roomExps.filter((e) => e.status === 'scheduled' || e.status === 'running');
      const activeExp = upcomingExps.find((e) => e.status === 'running') || null;
      const nextExp = findNextScheduledExperiment(upcomingExps);

      const card = document.createElement('div');
      card.className = 'card card--clickable sched-index-card';

      const cardHeader = document.createElement('div');
      cardHeader.className = 'sched-index-card__header';
      cardHeader.innerHTML = `<span class="sched-index-card__name">${escapeHtml(room.name)}</span>`;
      card.appendChild(cardHeader);

      const comfortSection = document.createElement('div');
      comfortSection.className = 'sched-section';
      comfortSection.innerHTML = scheduleSectionHeaderHtml(
        'COMFORT PERIODS',
        scheduleEnabledBadgeHtml(enabled),
      );

      const rebuildRoom = (updatedSched) => {
        cachedSchedules = {
          ...cachedSchedules,
          [room.slug]: updatedSched,
        };
        buildCards(cachedSchedules, cachedExpsByRoom);
      };

      if (periods.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'sched-index-card__empty';
        empty.textContent = 'No periods configured \u2014 click to add';
        comfortSection.appendChild(empty);
      } else {
        const list = document.createElement('div');
        list.className = 'sched-index-card__list';

        if (activeEntries.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'sched-index-card__empty';
          empty.textContent = 'No active periods';
          list.appendChild(empty);
        } else {
          appendPeriodRows(
            list, activeEntries, activePeriod, nextPeriod,
            hass, state, room, periods, enabled, rebuildRoom,
          );
        }

        comfortSection.appendChild(list);

        if (inactiveEntries.length > 0) {
          const details = document.createElement('details');
          details.className = 'sched-inactive';

          const summary = document.createElement('summary');
          summary.className = 'sched-inactive__summary';
          summary.textContent = `Inactive (${inactiveEntries.length})`;
          // Keep card navigation for rows; only block summary toggle from opening the room.
          summary.addEventListener('click', (e) => e.stopPropagation());
          details.appendChild(summary);

          const inactiveList = document.createElement('div');
          inactiveList.className = 'sched-inactive__list';
          appendPeriodRows(
            inactiveList, inactiveEntries, null, null,
            hass, state, room, periods, enabled, rebuildRoom,
          );
          details.appendChild(inactiveList);
          comfortSection.appendChild(details);
        }
      }
      card.appendChild(comfortSection);

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
          more.textContent = `+${overflow} more\u2026`;
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

  const initialGen = ++scheduleLoadGen;
  Promise.all([connection.getSchedules(), connection.listExperiments()]).then(([roomSchedules, experiments]) => {
    if (initialGen !== scheduleLoadGen) return;
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
      state = newState;
      const gen = ++scheduleLoadGen;
      Promise.all([connection.getSchedules(), connection.listExperiments()]).then(([roomSchedules, experiments]) => {
        if (gen !== scheduleLoadGen) return;
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
