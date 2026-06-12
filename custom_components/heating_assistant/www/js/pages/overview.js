import { createGauge, updateGauge } from '../components/gauge.js';
import { createRoomClimateTile } from '../components/room-climate-tile.js';
import { createCountdown, updateCountdown } from '../components/countdown.js';
import { indexExperimentsByRoom } from '../experiment-utils.js';
import {
  formatEnergy, formatTemperature, formatPercent, formatIrradiance,
  entityValue, entityAttr, systemEntity, modelFitLabel,
  MAX_SOLVE_TIME_S,
} from '../utils.js';

// Sensor whose ``ghi_now_effective`` attribute carries the building-level solar
// irradiance [W/m²] (measured/forecast when available, else modeled clear-sky).
const SOLAR_STATUS_ENTITY = systemEntity('solar_radiation_status');

export function renderOverview(container, rooms, state, connection, hass) {
  container.innerHTML = '';

  const kpiSection = document.createElement('div');
  kpiSection.innerHTML = '<div class="section-header">SYSTEM STATUS</div>';
  const kpiGrid = document.createElement('div');
  kpiGrid.className = 'grid-kpi';

  const gauges = buildGauges(state, rooms, connection);
  gauges.forEach((g) => kpiGrid.appendChild(g.element));

  // MPC load and the next-control countdown are operational/diagnostic, so they
  // come last — after the metrics that describe the building's climate state.
  const countdown = createCountdown(state, false);
  kpiGrid.appendChild(countdown.element);

  kpiSection.appendChild(kpiGrid);
  container.appendChild(kpiSection);

  const roomSection = document.createElement('div');
  roomSection.innerHTML = '<div class="section-header">ROOMS</div>';
  const roomGrid = document.createElement('div');
  roomGrid.className = 'grid-rooms';

  const tiles = rooms.map((room) => {
    const tile = createRoomClimateTile(room, state, hass);
    roomGrid.appendChild(tile.element);
    return { room, tile };
  });

  roomSection.appendChild(roomGrid);
  container.appendChild(roomSection);

  let latestState = state;
  // Per-room index of scheduled identification experiments; kept fresh so the
  // tiles can flip into their "experiment in progress" look as a run starts.
  let latestExperiments = null;
  const countdownInterval = setInterval(() => countdown.tick(latestState), 1000);

  // Fetch fresh schedule data via WebSocket (bypasses entity-state cache) and
  // push it to all tiles. Debounced so that a burst of rapid state updates
  // (e.g. after the coordinator calls async_update_listeners() for many sensors
  // at once) only triggers a single round-trip instead of one per entity.
  let _scheduleRefreshTimer = null;
  function refreshSchedules(immediate = false) {
    if (immediate) {
      connection.getSchedules().then((scheduleData) => {
        tiles.forEach((t) => t.tile.update(latestState, hass, scheduleData));
      });
      return;
    }
    clearTimeout(_scheduleRefreshTimer);
    _scheduleRefreshTimer = setTimeout(() => {
      connection.getSchedules().then((scheduleData) => {
        tiles.forEach((t) => t.tile.update(latestState, hass, scheduleData));
      });
    }, 300);
  }

  // Fetch the experiment list and push the per-room index to all tiles. A null
  // response means the fetch failed, so keep whatever the tiles already hold.
  function refreshExperiments() {
    connection.listExperiments().then((experiments) => {
      if (experiments == null) return;
      latestExperiments = indexExperimentsByRoom(experiments);
      tiles.forEach((t) => t.tile.update(latestState, hass, undefined, latestExperiments));
    });
  }

  // Initial fetch — runs after the tiles are already in the DOM so the first
  // paint is instant and the schedule section fills in within one WS round-trip.
  refreshSchedules(true);
  refreshExperiments();
  // Experiments transition scheduled → running on a wall-clock boundary that may
  // not coincide with any state event, so poll on a slow cadence to catch the
  // start/end even when the dashboard is otherwise idle.
  const experimentInterval = setInterval(refreshExperiments, 30000);

  return {
    update(newState) {
      latestState = newState;
      gauges.forEach((g) => g.updater(newState));
      tiles.forEach((t) => t.tile.update(newState, hass, undefined, latestExperiments));
      updateCountdown(countdown, newState);
      // Re-fetch schedules so the badge and period list reflect any toggle or
      // save that triggered this state update. Debounced to coalesce bursts.
      refreshSchedules();
    },
    _countdownInterval: countdownInterval,
    destroy() {
      clearInterval(countdownInterval);
      clearInterval(experimentInterval);
      clearTimeout(_scheduleRefreshTimer);
      tiles.forEach((t) => t.tile.destroy());
    },
  };
}

function buildGauges(state, rooms, connection) {
  const gauges = [];

  // ── Outdoor temperature ────────────────────────────────────────────────────
  const outdoorEntity = systemEntity('outdoor_temperature_measured');
  const outdoorGauge = createGauge({
    value: entityValue(state, outdoorEntity) ?? 0,
    min: -30,
    max: 40,
    label: 'OUTDOOR',
    format: formatTemperature,
    severity: { good: 5, warning: -5, alarm: -15 },
  });
  gauges.push({
    element: outdoorGauge,
    updater: (s) => updateGauge(outdoorGauge, {
      value: entityValue(s, outdoorEntity) ?? 0, min: -30, max: 40,
      format: formatTemperature,
      severity: { good: 5, warning: -5, alarm: -15 },
    }),
  });

  // ── Solar irradiance ─────────────────────────────────────────────────────────
  // A building-level, location-only metric (W/m²) — "how strong is the sun" —
  // rather than a sum of per-room window gains, which only makes sense per room.
  // The backend always supplies an effective value (measured/forecast, else a
  // modeled clear-sky value), so the card is only hidden if that value is
  // genuinely unavailable (e.g. no site location).
  const solarGauge = createGauge({
    value: solarIrradiance(state) ?? 0,
    min: 0,
    max: 1000,
    label: 'SOLAR',
    format: formatIrradiance,
    severity: { good: 400, warning: 100, alarm: 0 },
  });
  if (solarIrradiance(state) === null) solarGauge.style.display = 'none';
  gauges.push({
    element: solarGauge,
    updater: (s) => {
      const ghi = solarIrradiance(s);
      solarGauge.style.display = ghi === null ? 'none' : '';
      updateGauge(solarGauge, {
        value: ghi ?? 0, min: 0, max: 1000,
        format: formatIrradiance,
        severity: { good: 400, warning: 100, alarm: 0 },
      });
    },
  });

  // ── Daily energy ─────────────────────────────────────────────────────────────
  // Energy delivered since local midnight. The cumulative ``*_energy_total``
  // sensors are TOTAL_INCREASING, so today's usage is current − value-at-midnight.
  // The midnight baseline is fetched from HA history (see DailyEnergyTracker) so
  // it is correct regardless of when the page is loaded and survives reloads.
  const dailyEnergy = new DailyEnergyTracker(connection);
  const energyGauge = createGauge({
    value: 0,
    min: 0,
    max: 100,
    label: 'DAILY ENERGY',
    format: () => formatEnergy(dailyEnergy.value()),
    severity: { good: 0, warning: 50, alarm: 80, inverse: true },
  });
  dailyEnergy.init(state).then(() => paintEnergy(state));
  function paintEnergy(s) {
    dailyEnergy.observe(s);
    updateGauge(energyGauge, {
      value: dailyEnergy.value(), min: 0, max: 100,
      format: () => formatEnergy(dailyEnergy.value()),
      severity: { good: 0, warning: 50, alarm: 80, inverse: true },
    });
  }
  gauges.push({ element: energyGauge, updater: paintEnergy });

  // ── Model fit ──────────────────────────────────────────────────────────────
  const initialFit = computeOverallFit(state, rooms);
  const fitGauge = createGauge({
    value: initialFit.value,
    min: 0,
    max: 1,
    label: 'MODEL FIT',
    format: () => initialFit.label,
    severity: { good: 0.8, warning: 0.5, alarm: 0 },
  });
  gauges.push({
    element: fitGauge,
    updater: (s) => {
      const fit = computeOverallFit(s, rooms);
      updateGauge(fitGauge, {
        value: fit.value, min: 0, max: 1,
        format: () => fit.label,
        severity: { good: 0.8, warning: 0.5, alarm: 0 },
      });
    },
  });

  // ── MPC load ─────────────────────────────────────────────────────────────────
  const mpcEntity = systemEntity('mpc_performance');
  const mpcPercent = (s) => {
    const v = entityValue(s, mpcEntity);
    return v !== null ? (v / MAX_SOLVE_TIME_S) * 100 : 0;
  };
  const mpcGauge = createGauge({
    value: mpcPercent(state),
    min: 0,
    max: 100,
    label: 'MPC LOAD',
    format: (v) => formatPercent(v),
    severity: { good: 0, warning: 50, alarm: 80, inverse: true },
  });
  gauges.push({
    element: mpcGauge,
    updater: (s) => updateGauge(mpcGauge, {
      value: mpcPercent(s), min: 0, max: 100,
      format: (v) => formatPercent(v),
      severity: { good: 0, warning: 50, alarm: 80, inverse: true },
    }),
  });

  return gauges;
}

/** Building-level solar irradiance [W/m²] for display, or null when unavailable. */
function solarIrradiance(state) {
  const ghi = entityAttr(state, SOLAR_STATUS_ENTITY, 'ghi_now_effective');
  const num = parseFloat(ghi);
  return isNaN(num) ? null : num;
}

/** Entity ids of every cumulative heat-source energy-total sensor. */
function energyTotalEntities(state) {
  return Object.keys(state).filter(
    (id) => id.startsWith('sensor.heating_assistant_') && id.endsWith('_energy_total')
  );
}

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

/**
 * Tracks energy delivered since local midnight across all ``*_energy_total``
 * sensors. The per-entity baseline (cumulative kWh at midnight) is read from HA
 * history so it is correct no matter when the dashboard was opened, then today's
 * usage is computed from the live cumulative values without further fetches.
 */
class DailyEnergyTracker {
  constructor(connection) {
    this._connection = connection;
    this._baselines = {}; // entityId → cumulative kWh at start of today
    this._latest = {};     // entityId → most recent cumulative kWh
    this._day = startOfToday().toDateString();
    this._ready = false;
  }

  async init(state) {
    const entities = energyTotalEntities(state);
    if (entities.length === 0) { this._ready = true; return; }
    const history = await this._connection.getHistorySince(entities, startOfToday());
    for (const id of entities) {
      const points = history?.[id];
      // First recorded sample of the day is the cumulative total at (or just
      // after) midnight; fall back to the current value (→ 0 used today) when
      // history has no sample yet.
      const firstVal = Array.isArray(points) && points.length > 0
        ? parseFloat(points[0].s ?? points[0].state)
        : entityValue(state, id);
      if (firstVal !== null && !isNaN(firstVal)) this._baselines[id] = firstVal;
    }
    this._ready = true;
    this.observe(state);
  }

  /** Refresh the cached live cumulative totals from a state snapshot. */
  observe(state) {
    // Roll the baseline over at midnight without needing a page reload.
    const today = startOfToday().toDateString();
    if (today !== this._day) {
      this._day = today;
      this._baselines = { ...this._latest };
    }
    for (const id of energyTotalEntities(state)) {
      const v = entityValue(state, id);
      if (v !== null) {
        this._latest[id] = v;
        // A sensor that appears after init (or reset below its baseline) seeds
        // its own baseline so it never contributes a negative amount.
        if (!(id in this._baselines) || v < this._baselines[id]) {
          this._baselines[id] = v;
        }
      }
    }
  }

  /** Energy used since midnight [kWh]. */
  value() {
    let total = 0;
    for (const id of Object.keys(this._latest)) {
      const base = this._baselines[id];
      if (base !== undefined) total += Math.max(0, this._latest[id] - base);
    }
    return total;
  }
}

function computeOverallFit(state, rooms) {
  // Evaluate the overall model on the MEAN R² across rooms, and derive the
  // GOOD / ACCEPTABLE / POOR label from that same mean — so the bar fill and
  // the label always describe the same quantity (previously the bar showed the
  // average while the label reflected the single worst room).
  let sum = 0;
  let count = 0;

  for (const room of rooms) {
    const entity = room.entities['model_fit_quality'];
    if (entity) {
      const v = entityValue(state, entity);
      if (v !== null) {
        sum += v;
        count++;
      }
    }
  }

  const avg = count > 0 ? sum / count : 0;
  const fit = count > 0 ? modelFitLabel(avg) : { label: '—' };
  return { value: avg, label: fit.label };
}
