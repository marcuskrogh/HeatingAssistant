import { TimeSeriesChart, makeDataset, historyToDataPoints, forecastToDataPoints, loadChartJs } from '../components/time-series-chart.js';
import { createKpiCard, updateKpiCard } from '../components/kpi-card.js';
import { formatTemperature, formatPower, formatNumber, entityValue, entityAttr, roomEntity, systemEntity } from '../utils.js';

export function renderRoomDetail(container, roomSlug, rooms, state, connection) {
  const room = rooms.find((r) => r.slug === roomSlug);
  if (!room) {
    container.innerHTML = `<div class="loading">Room not found: ${roomSlug}</div>`;
    return { update() {} };
  }

  container.innerHTML = '';

  const nav = document.createElement('button');
  nav.className = 'nav-back';
  nav.innerHTML = '<span class="nav-back__arrow">←</span> OVERVIEW';
  nav.addEventListener('click', () => { window.location.hash = '#overview'; });
  container.appendChild(nav);

  const header = document.createElement('div');
  header.className = 'room-header';
  header.innerHTML = `<h2 class="room-header__title">${room.name}</h2>`;
  container.appendChild(header);

  const kpiGrid = document.createElement('div');
  kpiGrid.className = 'grid-kpi';
  container.appendChild(kpiGrid);

  const tempVal = entityValue(state, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
  const powerVal = entityValue(state, room.entities['heating_power_measured']);
  const fitVal = entityValue(state, room.entities['model_fit_quality']);
  const errorVal = entityValue(state, room.entities['prediction_error']);

  const kpis = [
    createKpiCard({ value: formatTemperature(tempVal), label: 'Temperature', unit: '' }),
    createKpiCard({ value: formatPower(powerVal), label: 'Power', unit: '' }),
    createKpiCard({ value: fitVal !== null ? formatNumber(fitVal, 3) : '—', label: 'Fit (R²)', unit: '' }),
    createKpiCard({ value: errorVal !== null ? formatNumber(errorVal, 2) + '°C' : '—', label: 'Error', unit: '' }),
  ];
  kpis.forEach((k) => kpiGrid.appendChild(k));

  const chartsContainer = document.createElement('div');
  chartsContainer.className = 'grid-charts';
  container.appendChild(chartsContainer);

  const tempChartEl = document.createElement('div');
  const powerChartEl = document.createElement('div');
  const disturbChartEl = document.createElement('div');
  chartsContainer.appendChild(tempChartEl);
  chartsContainer.appendChild(powerChartEl);
  chartsContainer.appendChild(disturbChartEl);

  const tempChart = new TimeSeriesChart(tempChartEl, {
    title: 'TEMPERATURE',
    yLabel: '°C',
    height: 220,
  });

  const powerChart = new TimeSeriesChart(powerChartEl, {
    title: 'HEATING POWER',
    yLabel: 'W',
    yMin: 0,
    height: 180,
  });

  const disturbChart = new TimeSeriesChart(disturbChartEl, {
    title: 'DISTURBANCES',
    yLabel: '°C',
    y2: true,
    y2Label: 'W',
    height: 180,
  });

  loadChartsData(room, state, connection, tempChart, powerChart, disturbChart);

  return {
    update(newState) {
      const tv = entityValue(newState, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
      const pv = entityValue(newState, room.entities['heating_power_measured']);
      const fv = entityValue(newState, room.entities['model_fit_quality']);
      const ev = entityValue(newState, room.entities['prediction_error']);

      updateKpiCard(kpis[0], { value: formatTemperature(tv) });
      updateKpiCard(kpis[1], { value: formatPower(pv) });
      updateKpiCard(kpis[2], { value: fv !== null ? formatNumber(fv, 3) : '—' });
      updateKpiCard(kpis[3], { value: ev !== null ? formatNumber(ev, 2) + '°C' : '—' });

      updateChartsFromState(room, newState, tempChart, powerChart, disturbChart);
    },
  };
}

async function loadChartsData(room, state, connection, tempChart, powerChart, disturbChart) {
  const historyEntities = [
    room.entities['temperature_filtered'] || room.entities['temperature_measured'],
    room.entities['heating_power_measured'],
    room.entities['solar_gain_measured'],
  ].filter(Boolean);

  const outdoorEntity = systemEntity('outdoor_temperature_measured');
  historyEntities.push(outdoorEntity);

  const history = await connection.getHistory(historyEntities, 6);

  const tempHistoryEntity = room.entities['temperature_filtered'] || room.entities['temperature_measured'];
  const tempHistory = historyToDataPoints(history[tempHistoryEntity]);
  const powerHistory = historyToDataPoints(history[room.entities['heating_power_measured']]);
  const solarHistory = historyToDataPoints(history[room.entities['solar_gain_measured']]);
  const outdoorHistory = historyToDataPoints(history[outdoorEntity]);

  const forecastEntity = state[room.entities['temperature_forecast']];
  const forecastData = forecastEntity?.attributes?.forecast || [];

  const tempForecast = forecastToDataPoints(forecastData, 'temperature');
  const setpointData = forecastToDataPoints(forecastData, 'setpoint');
  const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
  const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
  const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temperature');

  const upperBound = entityValue(state, room.entities['constraint_upper']);
  const lowerBound = entityValue(state, room.entities['constraint_lower']);

  const tempDatasets = [
    makeDataset('Filtered', tempHistory, '#4fc3f7', { borderWidth: 2 }),
    makeDataset('Forecast', tempForecast, '#4fc3f7', { dashed: true, borderWidth: 1.5 }),
    makeDataset('Setpoint', setpointData, '#e57373', { dashed: true, borderWidth: 1, pointRadius: 0 }),
  ];

  if (upperBound !== null && lowerBound !== null) {
    const now = Date.now();
    const future = now + 4 * 3600 * 1000;
    const past = now - 7 * 3600 * 1000;
    const boundTimes = [past, now, future];
    tempDatasets.push(
      makeDataset('Upper', boundTimes.map((t) => ({ x: t, y: upperBound })), 'rgba(229,115,115,0.3)', {
        fill: '+1', borderWidth: 0, pointRadius: 0,
        backgroundColor: 'rgba(229,115,115,0.06)',
      }),
      makeDataset('Lower', boundTimes.map((t) => ({ x: t, y: lowerBound })), 'rgba(229,115,115,0.3)', {
        borderWidth: 0, pointRadius: 0,
      })
    );
  }

  await tempChart.render(tempDatasets);

  const powerDatasets = [
    makeDataset('Measured', powerHistory, '#ffb74d', { borderWidth: 2, fill: true, backgroundColor: 'rgba(255,183,77,0.1)' }),
    makeDataset('Planned', powerForecast, '#ffb74d', { dashed: true, borderWidth: 1.5 }),
  ];
  await powerChart.render(powerDatasets);

  const disturbDatasets = [
    makeDataset('Outdoor Temp', outdoorHistory, '#90a4ae', { borderWidth: 2 }),
    makeDataset('Outdoor Forecast', outdoorForecast, '#90a4ae', { dashed: true, borderWidth: 1.5 }),
    makeDataset('Solar Gain', solarHistory, '#ffd54f', { borderWidth: 2, yAxisID: 'y2', fill: true, backgroundColor: 'rgba(255,213,79,0.1)' }),
    makeDataset('Solar Forecast', solarForecast, '#ffd54f', { dashed: true, borderWidth: 1.5, yAxisID: 'y2' }),
  ];
  await disturbChart.render(disturbDatasets);
}

function updateChartsFromState(room, state, tempChart, powerChart, disturbChart) {
  const forecastEntity = state[room.entities['temperature_forecast']];
  if (!forecastEntity?.attributes?.forecast) return;

  const forecastData = forecastEntity.attributes.forecast;
  const tempForecast = forecastToDataPoints(forecastData, 'temperature');
  const setpointData = forecastToDataPoints(forecastData, 'setpoint');
  const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
  const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
  const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temperature');

  if (tempChart._chart && tempChart._chart.data.datasets.length >= 3) {
    tempChart._chart.data.datasets[1].data = tempForecast;
    tempChart._chart.data.datasets[2].data = setpointData;
    tempChart._chart.update('none');
  }

  if (powerChart._chart && powerChart._chart.data.datasets.length >= 2) {
    powerChart._chart.data.datasets[1].data = powerForecast;
    powerChart._chart.update('none');
  }

  if (disturbChart._chart && disturbChart._chart.data.datasets.length >= 4) {
    disturbChart._chart.data.datasets[1].data = outdoorForecast;
    disturbChart._chart.data.datasets[3].data = solarForecast;
    disturbChart._chart.update('none');
  }
}
