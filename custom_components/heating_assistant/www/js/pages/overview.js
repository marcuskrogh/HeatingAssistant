import { createGauge, updateGauge } from '../components/gauge.js';
import { createRoomTile, updateRoomTile } from '../components/room-tile.js';
import { formatDuration, formatPower, formatTemperature, formatNumber, entityValue, entityAttr, systemEntity } from '../utils.js';

export function renderOverview(container, rooms, state, connection) {
  container.innerHTML = '';

  const kpiSection = document.createElement('div');
  kpiSection.innerHTML = '<div class="section-header">PROCESS KPIs</div>';
  const kpiGrid = document.createElement('div');
  kpiGrid.className = 'grid-kpi';

  const gauges = buildGauges(state, rooms);
  gauges.forEach((g) => kpiGrid.appendChild(g.element));
  kpiSection.appendChild(kpiGrid);
  container.appendChild(kpiSection);

  const roomSection = document.createElement('div');
  roomSection.innerHTML = '<div class="section-header">ROOM UNITS</div>';
  const roomGrid = document.createElement('div');
  roomGrid.className = 'grid-rooms';

  const tiles = rooms.map((room) => {
    const tile = createRoomTile(room, state);
    roomGrid.appendChild(tile);
    return { room, element: tile };
  });

  roomSection.appendChild(roomGrid);
  container.appendChild(roomSection);

  return {
    update(newState) {
      gauges.forEach((g) => g.updater(newState));
      tiles.forEach((t) => updateRoomTile(t.element, t.room, newState));
    },
  };
}

function buildGauges(state, rooms) {
  const mpcValue = entityValue(state, systemEntity('mpc_performance'));
  const mpcGauge = createGauge({
    value: mpcValue !== null ? mpcValue * 1000 : 0,
    min: 0,
    max: 100,
    label: 'MPC COMPUTE',
    format: (v) => formatDuration(v / 1000),
    severity: { good: 0, warning: 20, alarm: 50, inverse: true },
  });

  const powerValue = entityValue(state, systemEntity('system_summary'));
  const powerGauge = createGauge({
    value: powerValue ?? 0,
    min: 0,
    max: 15000,
    label: 'TOTAL POWER',
    format: formatPower,
    severity: { good: 0, warning: 6000, alarm: 10000, inverse: true },
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

  let avgFit = 0;
  let fitCount = 0;
  rooms.forEach((room) => {
    const fitEntity = room.entities['model_fit_quality'];
    if (fitEntity) {
      const v = entityValue(state, fitEntity);
      if (v !== null) { avgFit += v; fitCount++; }
    }
  });
  if (fitCount > 0) avgFit /= fitCount;

  const fitGauge = createGauge({
    value: avgFit,
    min: 0,
    max: 1,
    label: 'MODEL FIT (R²)',
    format: (v) => formatNumber(v, 3),
    severity: { good: 0.85, warning: 0.6, alarm: 0 },
  });

  return [
    {
      element: mpcGauge,
      updater: (s) => {
        const v = entityValue(s, systemEntity('mpc_performance'));
        updateGauge(mpcGauge, {
          value: v !== null ? v * 1000 : 0,
          min: 0, max: 100,
          format: (v) => formatDuration(v / 1000),
          severity: { good: 0, warning: 20, alarm: 50, inverse: true },
        });
      },
    },
    {
      element: powerGauge,
      updater: (s) => {
        const v = entityValue(s, systemEntity('system_summary'));
        updateGauge(powerGauge, {
          value: v ?? 0, min: 0, max: 15000,
          format: formatPower,
          severity: { good: 0, warning: 6000, alarm: 10000, inverse: true },
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
      element: fitGauge,
      updater: (s) => {
        let avg = 0, cnt = 0;
        rooms.forEach((room) => {
          const fitEntity = room.entities['model_fit_quality'];
          if (fitEntity) {
            const v = entityValue(s, fitEntity);
            if (v !== null) { avg += v; cnt++; }
          }
        });
        if (cnt > 0) avg /= cnt;
        updateGauge(fitGauge, {
          value: avg, min: 0, max: 1,
          format: (v) => formatNumber(v, 3),
          severity: { good: 0.85, warning: 0.6, alarm: 0 },
        });
      },
    },
  ];
}
