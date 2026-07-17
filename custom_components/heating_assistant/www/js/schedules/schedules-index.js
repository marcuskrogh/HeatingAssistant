import { findActivePeriod, findNextPeriod, scheduleEnabledBadgeHtml, scheduleSectionHeaderHtml, serializeSchedulePeriod } from '../schedule-utils.js?v=106';
import { findNextScheduledExperiment } from '../experiment-utils.js?v=106';
import { setPanelHash } from '../panel-hash.js?v=106';
import { updateRoomSchedule } from '../ha-services.js?v=106';
import { getScheduleDataForRoom, makePeriodRow, mergeRoomSchedulesWithState, patchStateSchedule, resolveRoomScheduleData } from './schedules-shared.js?v=106';

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

        preview.forEach((p, idx) => {
          const row = makePeriodRow(p, p === activePeriod, p === nextPeriod, idx);
          const toggleBtn = row.querySelector('[data-period-enable]');
          if (toggleBtn) {
            toggleBtn.addEventListener('click', async (e) => {
              e.stopPropagation();
              toggleBtn.disabled = true;
              try {
                const updated = await togglePeriodEnabled(hass, state, room, periods, idx);
                periods.splice(0, periods.length, ...updated.map((period) => ({
                  ...period,
                  days: [...(period.days || [0, 1, 2, 3, 4, 5, 6])],
                })));
                cachedSchedules = {
                  ...cachedSchedules,
                  [room.slug]: { enabled, periods: [...periods] },
                };
                buildCards(cachedSchedules, cachedExpsByRoom);
              } catch (err) {
                toggleBtn.disabled = false;
              }
            });
          }
          list.appendChild(row);
        });

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
