import { TimeSeriesChart, makeDataset, historyToDataPoints, forecastToDataPoints, loadChartJs } from '../components/time-series-chart.js';
import { createKpiCard, updateKpiCard } from '../components/kpi-card.js';
import { createCountdown } from '../components/countdown.js';
import {
  formatTemperature, formatPower, formatNumber,
  entityValue, entityAttr, systemEntity, modelFitLabel,
} from '../utils.js';

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
  const fitInfo = modelFitLabel(fitVal);
  const setpointVal = entityValue(state, room.entities['setpoint']);

  const kpis = [
    createKpiCard({ value: formatTemperature(tempVal), label: 'Temperature', unit: '' }),
    createKpiCard({ value: setpointVal !== null ? formatTemperature(setpointVal) : '—', label: 'Setpoint', unit: '' }),
    createKpiCard({ value: formatPower(powerVal), label: 'Power', unit: '' }),
    createKpiCard({
      value: `<span class="fit-badge ${fitInfo.class}">${fitInfo.label}</span>`,
      label: 'Model Fit',
      unit: '',
      html: true,
    }),
  ];
  kpis.forEach((k) => kpiGrid.appendChild(k));

  const countdown = createCountdown(state, true);
  kpiGrid.appendChild(countdown.element);

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
    height: 240,
  });

  const powerChart = new TimeSeriesChart(powerChartEl, {
    title: 'HEATING POWER',
    yLabel: 'W',
    height: 200,
  });

  const disturbChart = new TimeSeriesChart(disturbChartEl, {
    title: 'DISTURBANCES',
    yLabel: '°C',
    y2: true,
    y2Label: 'W',
    height: 200,
  });

  loadChartsData(room, state, connection, tempChart, powerChart, disturbChart);

  const countdownInterval = setInterval(() => countdown.tick(state), 1000);

  return {
    update(newState) {
      const tv = entityValue(newState, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
      const pv = entityValue(newState, room.entities['heating_power_measured']);
      const fv = entityValue(newState, room.entities['model_fit_quality']);
      const fi = modelFitLabel(fv);
      const sp = entityValue(newState, room.entities['setpoint']);

      updateKpiCard(kpis[0], { value: formatTemperature(tv) });
      updateKpiCard(kpis[1], { value: sp !== null ? formatTemperature(sp) : '—' });
      updateKpiCard(kpis[2], { value: formatPower(pv) });
      updateKpiCard(kpis[3], { value: `<span class="fit-badge ${fi.class}">${fi.label}</span>`, html: true });

      updateChartsFromState(room, newState, tempChart, powerChart, disturbChart);
    },
    destroy() {
      clearInterval(countdownInterval);
      tempChart.destroy();
      powerChart.destroy();
      disturbChart.destroy();
    },
  };
}

async function loadChartsData(room, state, connection, tempChart, powerChart, disturbChart) {
  const tempFilteredEntity = room.entities['temperature_filtered'];
  const tempMeasuredEntity = room.entities['temperature_measured'];
  const powerMeasuredEntity = room.entities['heating_power_measured'];
  const solarMeasuredEntity = room.entities['solar_gain_measured'];
  const outdoorEntity = systemEntity('outdoor_temperature_measured');

  const historyEntities = [
    tempFilteredEntity,
    tempMeasuredEntity,
    powerMeasuredEntity,
    solarMeasuredEntity,
    outdoorEntity,
  ].filter(Boolean);

  const history = await connection.getHistory(historyEntities, 6);

  const filteredHistory = historyToDataPoints(history[tempFilteredEntity]);
  const measuredHistory = historyToDataPoints(history[tempMeasuredEntity]);
  const powerHistory = historyToDataPoints(history[powerMeasuredEntity]);
  const solarHistory = historyToDataPoints(history[solarMeasuredEntity]);
  const outdoorHistory = historyToDataPoints(history[outdoorEntity]);

  const forecastEntity = state[room.entities['temperature_forecast']];
  const forecastData = forecastEntity?.attributes?.forecast || [];

  const tempForecastNonlinear = forecastToDataPoints(forecastData, 'temperature');
  const tempForecastLinearised = forecastToDataPoints(forecastData, 'temperature_linearised');
  const setpointForecast = forecastToDataPoints(forecastData, 'setpoint');
  const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
  const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
  const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temperature');

  const upperBound = entityValue(state, room.entities['constraint_upper']);
  const lowerBound = entityValue(state, room.entities['constraint_lower']);

  buildTemperatureChart(tempChart, filteredHistory, measuredHistory, tempForecastNonlinear, tempForecastLinearised, setpointForecast, upperBound, lowerBound);
  buildPowerChart(powerChart, powerHistory, powerForecast, state, room);
  buildDisturbanceChart(disturbChart, outdoorHistory, outdoorForecast, solarHistory, solarForecast);
}

function buildTemperatureChart(chart, filteredHistory, measuredHistory, forecastNonlinear, forecastLinearised, setpointData, upperBound, lowerBound) {
  const now = Date.now();
  const past = now - 7 * 3600 * 1000;
  const future = now + 4 * 3600 * 1000;

  const datasets = [
    makeDataset('Filtered', filteredHistory, '#4fc3f7', { borderWidth: 2 }),
    makeDataset('Measured', measuredHistory, '#e57373', {
      borderWidth: 0, pointRadius: 3, pointHoverRadius: 5,
      pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
      showLine: false,
    }),
    makeDataset('Forecast', forecastNonlinear, '#4fc3f7', { dashed: true, borderWidth: 2 }),
  ];

  if (forecastLinearised.length > 0) {
    datasets.push(
      makeDataset('Linearised', forecastLinearised, '#ab47bc', { dashed: true, borderWidth: 1.5 })
    );
  }

  if (setpointData.length > 0) {
    datasets.push(
      makeDataset('Setpoint', setpointData, '#e57373', { dashed: true, borderWidth: 1, pointRadius: 0 })
    );
  }

  if (upperBound !== null && lowerBound !== null) {
    const boundTimes = [past, now, future];
    const yMax = upperBound + 5;
    const yMin = lowerBound - 5;

    datasets.push(
      makeDataset('Above Comfort', boundTimes.map((t) => ({ x: t, y: yMax })), 'transparent', {
        fill: { target: { value: upperBound }, above: 'rgba(229,115,115,0.12)', below: 'transparent' },
        borderWidth: 0, pointRadius: 0, showLine: true,
      }),
      makeDataset('Upper Bound', boundTimes.map((t) => ({ x: t, y: upperBound })), 'rgba(229,115,115,0.4)', {
        borderWidth: 1, borderDash: [3, 3], pointRadius: 0, fill: false,
      }),
      makeDataset('Lower Bound', boundTimes.map((t) => ({ x: t, y: lowerBound })), 'rgba(229,115,115,0.4)', {
        borderWidth: 1, borderDash: [3, 3], pointRadius: 0, fill: false,
      }),
      makeDataset('Below Comfort', boundTimes.map((t) => ({ x: t, y: yMin })), 'transparent', {
        fill: { target: { value: lowerBound }, above: 'transparent', below: 'rgba(229,115,115,0.12)' },
        borderWidth: 0, pointRadius: 0, showLine: true,
      })
    );
  }

  chart.render(datasets);
}

function buildPowerChart(chart, powerHistory, powerForecast, state, room) {
  const maxPowerAttr = entityAttr(state, room.entities['heating_power_forecast'], 'max_power');
  const maxPower = maxPowerAttr ? parseFloat(maxPowerAttr) : null;

  const now = Date.now();
  const past = now - 7 * 3600 * 1000;
  const future = now + 4 * 3600 * 1000;

  const datasets = [
    makeDataset('Measured', powerHistory, '#ffb74d', {
      borderWidth: 2, stepped: 'before',
      fill: true, backgroundColor: 'rgba(255,183,77,0.08)',
    }),
    makeDataset('Planned', powerForecast, '#ffb74d', {
      dashed: true, borderWidth: 2, stepped: 'before',
    }),
  ];

  if (maxPower !== null) {
    const boundTimes = [past, now, future];
    const yMax = maxPower * 1.3;
    const minPower = -maxPower;
    const yMin = minPower * 1.3;

    datasets.push(
      makeDataset('Max Power', boundTimes.map((t) => ({ x: t, y: maxPower })), 'rgba(229,115,115,0.4)', {
        borderWidth: 1, borderDash: [3, 3], pointRadius: 0, fill: false,
      }),
      makeDataset('Above Max', boundTimes.map((t) => ({ x: t, y: yMax })), 'transparent', {
        fill: { target: { value: maxPower }, above: 'rgba(229,115,115,0.12)', below: 'transparent' },
        borderWidth: 0, pointRadius: 0, showLine: true,
      }),
      makeDataset('Min Power', boundTimes.map((t) => ({ x: t, y: minPower })), 'rgba(229,115,115,0.4)', {
        borderWidth: 1, borderDash: [3, 3], pointRadius: 0, fill: false,
      }),
      makeDataset('Below Min', boundTimes.map((t) => ({ x: t, y: yMin })), 'transparent', {
        fill: { target: { value: minPower }, above: 'transparent', below: 'rgba(229,115,115,0.12)' },
        borderWidth: 0, pointRadius: 0, showLine: true,
      })
    );
  }

  chart.render(datasets);
}

function buildDisturbanceChart(chart, outdoorHistory, outdoorForecast, solarHistory, solarForecast) {
  const datasets = [
    makeDataset('Outdoor Temp', outdoorHistory, '#90a4ae', { borderWidth: 2 }),
    makeDataset('Outdoor Forecast', outdoorForecast, '#90a4ae', { dashed: true, borderWidth: 1.5 }),
    makeDataset('Solar Gain', solarHistory, '#ffd54f', {
      borderWidth: 2, yAxisID: 'y2',
      fill: true, backgroundColor: 'rgba(255,213,79,0.08)',
    }),
    makeDataset('Solar Forecast', solarForecast, '#ffd54f', {
      dashed: true, borderWidth: 1.5, yAxisID: 'y2',
    }),
  ];

  chart.render(datasets);
}

function updateChartsFromState(room, state, tempChart, powerChart, disturbChart) {
  const forecastEntity = state[room.entities['temperature_forecast']];
  if (!forecastEntity?.attributes?.forecast) return;

  const forecastData = forecastEntity.attributes.forecast;
  const tempForecast = forecastToDataPoints(forecastData, 'temperature');
  const tempLinearised = forecastToDataPoints(forecastData, 'temperature_linearised');
  const setpointData = forecastToDataPoints(forecastData, 'setpoint');
  const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
  const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
  const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temperature');

  if (tempChart._chart) {
    const ds = tempChart._chart.data.datasets;
    if (ds[2]) ds[2].data = tempForecast;
    if (ds[3] && tempLinearised.length > 0) ds[3].data = tempLinearised;
    const setpointIdx = ds.findIndex((d) => d.label === 'Setpoint');
    if (setpointIdx >= 0) ds[setpointIdx].data = setpointData;
    tempChart._chart.update('none');
  }

  if (powerChart._chart) {
    const ds = powerChart._chart.data.datasets;
    if (ds[1]) ds[1].data = powerForecast;
    powerChart._chart.update('none');
  }

  if (disturbChart._chart) {
    const ds = disturbChart._chart.data.datasets;
    if (ds[1]) ds[1].data = outdoorForecast;
    if (ds[3]) ds[3].data = solarForecast;
    disturbChart._chart.update('none');
  }
}
