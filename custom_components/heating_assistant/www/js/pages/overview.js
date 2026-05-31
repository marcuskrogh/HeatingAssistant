import { createGauge, updateGauge } from '../components/gauge.js';
import { createRoomTile, updateRoomTile } from '../components/room-tile.js';
import { createCountdown, updateCountdown } from '../components/countdown.js';
import {
  formatPercent, formatEnergy, formatTemperature, formatPower,
  formatNumber, entityValue, entityAttr, systemEntity, modelFitLabel,
  MAX_SOLVE_TIME_S,
} from '../utils.js';

export function renderOverview(container, rooms, state, connection) {
  container.innerHTML = '';

  const kpiSection = document.createElement('div');
  kpiSection.innerHTML = '<div class="section-header">SYSTEM STATUS</div>';
  const kpiGrid = document.createElement('div');
  kpiGrid.className = 'grid-kpi';

  const gauges = buildGauges(state, rooms);
  gauges.forEach((g) => kpiGrid.appendChild(g.element));

  const countdown = createCountdown(state, false);
  kpiGrid.appendChild(countdown.element);

  kpiSection.appendChild(kpiGrid);
  container.appendChild(kpiSection);

  const roomSection = document.createElement('div');
  roomSection.innerHTML = '<div class="section-header">ROOMS</div>';
  const roomGrid = document.createElement('div');
  roomGrid.className = 'grid-rooms';

  const tiles = rooms.map((room) => {
    const tile = createRoomTile(room, state);
    roomGrid.appendChild(tile);
    return { room, element: tile };
  });

  roomSection.appendChild(roomGrid);
  container.appendChild(roomSection);

  let latestState = state;
  const countdownInterval = setInterval(() => countdown.tick(latestState), 1000);

  return {
    update(newState) {
      latestState = newState;
      gauges.forEach((g) => g.updater(newState));
      tiles.forEach((t) => updateRoomTile(t.element, t.room, newState));
      updateCountdown(countdown, newState);
    },
    _countdownInterval: countdownInterval,
    destroy() {
      clearInterval(countdownInterval);
    },
  };
}

function buildGauges(state, rooms) {
  const mpcSolveTime = entityValue(state, systemEntity('mpc_performance'));
  const mpcPercent = mpcSolveTime !== null ? (mpcSolveTime / MAX_SOLVE_TIME_S) * 100 : 0;

  const mpcGauge = createGauge({
    value: mpcPercent,
    min: 0,
    max: 100,
    label: 'MPC LOAD',
    format: (v) => formatPercent(v),
    severity: { good: 0, warning: 50, alarm: 80, inverse: true },
  });

  const outdoorValue = entityValue(state, systemEntity('outdoor_temperature_measured'));
  const outdoorGauge = createGauge({
    value: outdoorValue ?? 0,
    min: -30,
    max: 40,
    label: 'OUTDOOR',
    format: formatTemperature,
    severity: { good: 5, warning: -5, alarm: -15 },
  });

  const totalSolar = computeTotalSolar(state, rooms);
  const solarGauge = createGauge({
    value: totalSolar,
    min: 0,
    max: 5000,
    label: 'SOLAR GAIN',
    format: formatPower,
    severity: { good: 1000, warning: 200, alarm: 0 },
  });

  const fitEval = computeOverallFit(state, rooms);
  const fitGauge = createGauge({
    value: fitEval.value,
    min: 0,
    max: 1,
    label: 'MODEL FIT',
    format: () => fitEval.label,
    severity: { good: 0.8, warning: 0.5, alarm: 0 },
  });

  const dailyEnergy = computeDailyEnergy(state);
  const energyGauge = createGauge({
    value: dailyEnergy,
    min: 0,
    max: 100,
    label: 'DAILY ENERGY',
    format: () => formatEnergy(dailyEnergy),
    severity: { good: 0, warning: 50, alarm: 80, inverse: true },
  });

  return [
    {
      element: mpcGauge,
      updater: (s) => {
        const v = entityValue(s, systemEntity('mpc_performance'));
        const pct = v !== null ? (v / MAX_SOLVE_TIME_S) * 100 : 0;
        updateGauge(mpcGauge, {
          value: pct, min: 0, max: 100,
          format: (v) => formatPercent(v),
          severity: { good: 0, warning: 50, alarm: 80, inverse: true },
        });
      },
    },
    {
      element: outdoorGauge,
      updater: (s) => {
        const v = entityValue(s, systemEntity('outdoor_temperature_measured'));
        updateGauge(outdoorGauge, {
          value: v ?? 0, min: -30, max: 40,
          format: formatTemperature,
          severity: { good: 5, warning: -5, alarm: -15 },
        });
      },
    },
    {
      element: solarGauge,
      updater: (s) => {
        const total = computeTotalSolar(s, rooms);
        updateGauge(solarGauge, {
          value: total, min: 0, max: 5000,
          format: formatPower,
          severity: { good: 1000, warning: 200, alarm: 0 },
        });
      },
    },
    {
      element: energyGauge,
      updater: (s) => {
        const energy = computeDailyEnergy(s);
        updateGauge(energyGauge, {
          value: energy, min: 0, max: 100,
          format: () => formatEnergy(energy),
          severity: { good: 0, warning: 50, alarm: 80, inverse: true },
        });
      },
    },
    {
      element: fitGauge,
      updater: (s) => {
        const fit = computeOverallFit(s, rooms);
        updateGauge(fitGauge, {
          value: fit.value, min: 0, max: 1,
          format: () => fit.label,
          severity: { good: 0.8, warning: 0.5, alarm: 0 },
        });
      },
    },
  ];
}

function computeTotalSolar(state, rooms) {
  let total = 0;
  for (const room of rooms) {
    const entity = room.entities['solar_gain_measured'];
    if (entity) {
      const v = entityValue(state, entity);
      if (v !== null) total += v;
    }
  }
  return total;
}

function computeDailyEnergy(state) {
  // Sum the cumulative kWh from all energy_total sensors (already the correct integral).
  let cumulative = 0;
  let anyValid = false;
  for (const entityId of Object.keys(state)) {
    if (entityId.startsWith('sensor.heating_assistant_') && entityId.endsWith('_energy_total')) {
      const entity = state[entityId];
      if (entity) {
        const v = parseFloat(entity.state);
        if (!isNaN(v)) {
          cumulative += v;
          anyValid = true;
        }
      }
    }
  }
  if (!anyValid) return 0;

  // Subtract the cumulative total recorded at the start of today so the gauge
  // reflects energy used since midnight rather than since the sensor was created.
  const today = new Date().toDateString();
  let stored = null;
  try {
    stored = JSON.parse(localStorage.getItem('ha_daily_energy_baseline') || 'null');
  } catch (_) { /* ignore parse errors */ }

  if (!stored || stored.date !== today) {
    localStorage.setItem('ha_daily_energy_baseline', JSON.stringify({ date: today, value: cumulative }));
    return 0;
  }

  return Math.max(0, cumulative - stored.value);
}

function computeOverallFit(state, rooms) {
  let sum = 0;
  let count = 0;
  let worstR2 = 1;

  for (const room of rooms) {
    const entity = room.entities['model_fit_quality'];
    if (entity) {
      const v = entityValue(state, entity);
      if (v !== null) {
        sum += v;
        count++;
        if (v < worstR2) worstR2 = v;
      }
    }
  }

  const avg = count > 0 ? sum / count : 0;
  const fit = modelFitLabel(worstR2);
  return { value: avg, label: fit.label };
}
