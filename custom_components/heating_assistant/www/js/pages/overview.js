import { createGauge, updateGauge } from '../components/gauge.js?v=82';
import { createRoomClimateTile } from '../components/room-climate-tile.js?v=82';
import { createCountdown, updateCountdown } from '../components/countdown.js?v=82';
import { indexExperimentsByRoom } from '../experiment-utils.js?v=82';
import {
  KPI_SEVERITY,
  DAILY_ENERGY_GAUGE_MAX_KWH,
  houseComfortIndex,
  houseHeatingPowerKw,
  houseHeatingPowerGaugeFill,
  houseEffectiveCop,
  houseMeanTrackingError,
  houseModelFit,
  mpcLoadPercent,
} from '../kpi-engine.js?v=82';
import {
  formatEnergy, formatPercent, formatPowerKw, formatNumber,
  entityValue,
} from '../utils.js?v=82';

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

  // ── Comfort index ────────────────────────────────────────────────────────────
  const comfortGauge = createGauge({
    value: houseComfortIndex(state, rooms) ?? 0,
    min: 0,
    max: 100,
    label: 'COMFORT',
    format: formatPercent,
    severity: KPI_SEVERITY.comfortIndex,
  });
  if (houseComfortIndex(state, rooms) === null) comfortGauge.style.display = 'none';
  gauges.push({
    element: comfortGauge,
    updater: (s) => {
      const idx = houseComfortIndex(s, rooms);
      comfortGauge.style.display = idx === null ? 'none' : '';
      updateGauge(comfortGauge, {
        value: idx ?? 0, min: 0, max: 100,
        format: formatPercent,
        severity: KPI_SEVERITY.comfortIndex,
      });
    },
  });

  // ── Total heating power ──────────────────────────────────────────────────────
  const powerFill = houseHeatingPowerGaugeFill(state) ?? 0;
  const powerGauge = createGauge({
    value: powerFill,
    min: 0,
    max: 1,
    label: 'HEATING POWER',
    format: () => formatPowerKw(houseHeatingPowerKw(state) ?? 0),
    severity: KPI_SEVERITY.houseHeatingPower,
  });
  gauges.push({
    element: powerGauge,
    updater: (s) => {
      const fill = houseHeatingPowerGaugeFill(s) ?? 0;
      updateGauge(powerGauge, {
        value: fill, min: 0, max: 1,
        format: () => formatPowerKw(houseHeatingPowerKw(s) ?? 0),
        severity: KPI_SEVERITY.houseHeatingPower,
      });
    },
  });

  // ── Effective system COP ─────────────────────────────────────────────────────
  const initialCop = houseEffectiveCop(state);
  const copGauge = createGauge({
    value: initialCop ?? 0,
    min: 0,
    max: 5,
    label: 'SYSTEM COP',
    format: (v) => formatNumber(v, 2),
    severity: KPI_SEVERITY.effectiveCop,
  });
  if (initialCop === null) copGauge.style.display = 'none';
  gauges.push({
    element: copGauge,
    updater: (s) => {
      const cop = houseEffectiveCop(s);
      copGauge.style.display = cop === null ? 'none' : '';
      updateGauge(copGauge, {
        value: cop ?? 0, min: 0, max: 5,
        format: (v) => formatNumber(v, 2),
        severity: KPI_SEVERITY.effectiveCop,
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
    max: DAILY_ENERGY_GAUGE_MAX_KWH,
    label: 'DAILY ENERGY',
    format: () => formatEnergy(dailyEnergy.value()),
    severity: KPI_SEVERITY.dailyEnergy,
  });
  dailyEnergy.init(state).then(() => paintEnergy(state));
  function paintEnergy(s) {
    dailyEnergy.observe(s);
    updateGauge(energyGauge, {
      value: dailyEnergy.value(), min: 0, max: DAILY_ENERGY_GAUGE_MAX_KWH,
      format: () => formatEnergy(dailyEnergy.value()),
      severity: KPI_SEVERITY.dailyEnergy,
    });
  }
  gauges.push({ element: energyGauge, updater: paintEnergy });

  // ── Mean tracking error ──────────────────────────────────────────────────────
  const trackingGauge = createGauge({
    value: houseMeanTrackingError(state, rooms) ?? 0,
    min: 0,
    max: 1,
    label: 'TRACKING ERROR',
    format: (v) => `${formatNumber(v, 2)}°C`,
    severity: KPI_SEVERITY.meanTrackingError,
  });
  if (houseMeanTrackingError(state, rooms) === null) trackingGauge.style.display = 'none';
  gauges.push({
    element: trackingGauge,
    updater: (s) => {
      const err = houseMeanTrackingError(s, rooms);
      trackingGauge.style.display = err === null ? 'none' : '';
      updateGauge(trackingGauge, {
        value: err ?? 0, min: 0, max: 1,
        format: (v) => `${formatNumber(v, 2)}°C`,
        severity: KPI_SEVERITY.meanTrackingError,
      });
    },
  });

  // ── Model fit ──────────────────────────────────────────────────────────────
  const initialFit = houseModelFit(state, rooms);
  const fitGauge = createGauge({
    value: initialFit.value,
    min: 0,
    max: 1,
    label: 'MODEL FIT',
    format: () => initialFit.label,
    severity: KPI_SEVERITY.modelFit,
  });
  gauges.push({
    element: fitGauge,
    updater: (s) => {
      const fit = houseModelFit(s, rooms);
      updateGauge(fitGauge, {
        value: fit.value, min: 0, max: 1,
        format: () => fit.label,
        severity: KPI_SEVERITY.modelFit,
      });
    },
  });

  // ── MPC load ─────────────────────────────────────────────────────────────────
  const mpcGauge = createGauge({
    value: mpcLoadPercent(state),
    min: 0,
    max: 100,
    label: 'MPC LOAD',
    format: formatPercent,
    severity: KPI_SEVERITY.mpcLoad,
  });
  gauges.push({
    element: mpcGauge,
    updater: (s) => updateGauge(mpcGauge, {
      value: mpcLoadPercent(s), min: 0, max: 100,
      format: formatPercent,
      severity: KPI_SEVERITY.mpcLoad,
    }),
  });

  return gauges;
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

