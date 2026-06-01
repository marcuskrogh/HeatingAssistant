import { TimeSeriesChart, makeDataset, historyToDataPoints, forecastToDataPoints, loadChartJs } from '../components/time-series-chart.js';
import { createKpiCard, updateKpiCard } from '../components/kpi-card.js';
import { createCountdown } from '../components/countdown.js';
import {
  formatTemperature, formatPower, formatNumber,
  entityValue, entityAttr, systemEntity, modelFitLabel,
} from '../utils.js';

export function renderRoomDetail(container, roomSlug, rooms, state, connection, hass) {
  const room = rooms.find((r) => r.slug === roomSlug);
  if (!room) {
    container.innerHTML = `<div class="loading">Room not found: ${roomSlug}</div>`;
    return { update() {} };
  }

  container.innerHTML = '';

  const nav = document.createElement('button');
  nav.className = 'nav-back';
  nav.innerHTML = '<span class="nav-back__arrow">\u2190</span> OVERVIEW';
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

  const setpointKpiEl = createKpiCard({ value: setpointVal !== null ? formatTemperature(setpointVal) : '\u2014', label: 'Setpoint', unit: '' });
  setpointKpiEl.classList.add('card--clickable', 'kpi--setpoint');
  setpointKpiEl.title = 'Click to change setpoint';
  // Append a persistent hint so users know the card is interactive.
  const spHint = document.createElement('span');
  spHint.className = 'kpi--setpoint__hint';
  spHint.textContent = 'TAP TO CHANGE';
  setpointKpiEl.appendChild(spHint);

  const kpis = [
    setpointKpiEl,
    createKpiCard({ value: formatTemperature(tempVal), label: 'Temperature', unit: '' }),
    createKpiCard({ value: formatPower(powerVal), label: 'Power', unit: '' }),
    createKpiCard({
      value: `<span class="fit-badge ${fitInfo.class}">${fitInfo.label}</span>`,
      label: 'Model Fit',
      unit: '',
      html: true,
    }),
  ];
  kpis.forEach((k) => kpiGrid.appendChild(k));

  // Make setpoint KPI clickable for inline editing.
  let setpointEditing = false;
  const setpointKpi = kpis[0];
  const climateEntityId = `climate.heating_assistant_${roomSlug}`;

  setpointKpi.addEventListener('click', () => {
    if (setpointEditing) return;
    const currentSp = entityValue(latestState, room.entities['setpoint']) ?? 22;
    setpointEditing = true;
    _showSetpointEditor(setpointKpi, currentSp, async (newSp) => {
      try {
        await hass.callService('climate', 'set_temperature', {
          entity_id: climateEntityId,
          temperature: newSp,
        });
      } catch (err) {
        // Service call failed; the display will self-correct on next state update.
      }
      setpointEditing = false;
    }, () => { setpointEditing = false; });
  });

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
    yLabel: '\u00b0C',
    height: 240,
  });

  const powerChart = new TimeSeriesChart(powerChartEl, {
    title: 'HEATING POWER & PRICE',
    yLabel: 'W',
    y2: true,
    y2Label: 'Price',
    height: 200,
  });

  const disturbChart = new TimeSeriesChart(disturbChartEl, {
    title: 'DISTURBANCES',
    yLabel: '\u00b0C',
    y2: true,
    y2Label: 'W',
    height: 200,
  });

  loadChartsData(room, state, connection, tempChart, powerChart, disturbChart);

  let latestState = state;
  const countdownInterval = setInterval(() => countdown.tick(latestState), 1000);

  return {
    update(newState) {
      latestState = newState;

      const tv = entityValue(newState, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
      const pv = entityValue(newState, room.entities['heating_power_measured']);
      const fv = entityValue(newState, room.entities['model_fit_quality']);
      const fi = modelFitLabel(fv);
      const sp = entityValue(newState, room.entities['setpoint']);

      if (!setpointEditing) {
        updateKpiCard(kpis[0], { value: sp !== null ? formatTemperature(sp) : '\u2014' });
      }
      updateKpiCard(kpis[1], { value: formatTemperature(tv) });
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

function _showSetpointEditor(kpiCard, currentValue, onConfirm, onCancel) {
  const valueEl = kpiCard.querySelector('.kpi__value');
  if (!valueEl) return;

  const savedHtml = valueEl.innerHTML;
  const STEP = 0.5;
  const MIN = 5;
  const MAX = 30;

  // Snap incoming value to the nearest 0.5 °C so the stepper is never
  // in an invalid state regardless of what the sensor reported.
  let selected = Math.round((currentValue ?? 22) / STEP) * STEP;
  selected = Math.max(MIN, Math.min(MAX, selected));

  function restore() {
    valueEl.innerHTML = savedHtml;
  }

  // ── Stepper row: [−]  22.0°  [+] ─────────────────────────────────
  const stepperRow = document.createElement('div');
  stepperRow.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:6px;';

  const downBtn = document.createElement('button');
  downBtn.className = 'btn btn--ghost';
  downBtn.textContent = '−';
  downBtn.style.cssText = 'padding:1px 10px;font-size:17px;min-width:0;line-height:1.4;';

  const valueDisplay = document.createElement('span');
  valueDisplay.style.cssText = 'font-family:var(--font-mono);font-size:18px;color:var(--text-primary);min-width:52px;text-align:center;display:inline-block;';

  const upBtn = document.createElement('button');
  upBtn.className = 'btn btn--ghost';
  upBtn.textContent = '+';
  upBtn.style.cssText = 'padding:1px 10px;font-size:17px;min-width:0;line-height:1.4;';

  stepperRow.appendChild(downBtn);
  stepperRow.appendChild(valueDisplay);
  stepperRow.appendChild(upBtn);

  // ── Confirm / Cancel row ──────────────────────────────────────────
  const btnRow = document.createElement('div');
  btnRow.style.cssText = 'display:flex;justify-content:center;gap:4px;margin-top:6px;';

  const confirmBtn = document.createElement('button');
  confirmBtn.className = 'btn btn--primary';
  confirmBtn.textContent = '✓';
  confirmBtn.style.cssText = 'padding:2px 10px;font-size:13px;min-width:0;';

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn btn--ghost';
  cancelBtn.textContent = '✗';
  cancelBtn.style.cssText = 'padding:2px 10px;font-size:13px;min-width:0;';

  btnRow.appendChild(confirmBtn);
  btnRow.appendChild(cancelBtn);

  valueEl.innerHTML = '';
  valueEl.appendChild(stepperRow);
  valueEl.appendChild(btnRow);

  function refresh() {
    valueDisplay.textContent = selected.toFixed(1) + '°';
    downBtn.disabled = selected <= MIN;
    upBtn.disabled = selected >= MAX;
  }
  refresh();

  downBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    selected = Math.max(MIN, selected - STEP);
    refresh();
  });

  upBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    selected = Math.min(MAX, selected + STEP);
    refresh();
  });

  confirmBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    // Optimistic update: show the new value immediately so the KPI reflects
    // the change before the HA state event arrives on the next cycle.
    valueEl.textContent = selected.toFixed(1) + '°C';
    onConfirm(selected);
  });

  cancelBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    restore();
    onCancel();
  });
}

async function loadChartsData(room, state, connection, tempChart, powerChart, disturbChart) {
  const tempFilteredEntity = room.entities['temperature_filtered'];
  const tempMeasuredEntity = room.entities['temperature_measured'];
  const setpointEntity = room.entities['setpoint'];
  const constraintUpperEntity = room.entities['constraint_upper'];
  const constraintLowerEntity = room.entities['constraint_lower'];
  const powerMeasuredEntity = room.entities['heating_power_measured'];
  const solarMeasuredEntity = room.entities['solar_gain_measured'];
  const outdoorEntity = systemEntity('outdoor_temperature_measured');
  const priceEntity = systemEntity('electricity_price');

  const historyEntities = [
    tempFilteredEntity,
    tempMeasuredEntity,
    setpointEntity,
    constraintUpperEntity,
    constraintLowerEntity,
    powerMeasuredEntity,
    solarMeasuredEntity,
    outdoorEntity,
    priceEntity,
  ].filter(Boolean);

  const history = await connection.getHistory(historyEntities, 12);

  const filteredHistory = historyToDataPoints(history[tempFilteredEntity]);
  const measuredHistory = historyToDataPoints(history[tempMeasuredEntity]);
  const setpointHistory = historyToDataPoints(history[setpointEntity]);
  const constraintUpperHistory = historyToDataPoints(history[constraintUpperEntity]);
  const constraintLowerHistory = historyToDataPoints(history[constraintLowerEntity]);
  const powerHistory = historyToDataPoints(history[powerMeasuredEntity]);
  const solarHistory = appendCurrentValue(historyToDataPoints(history[solarMeasuredEntity]), state, solarMeasuredEntity);
  const outdoorHistory = appendCurrentValue(historyToDataPoints(history[outdoorEntity]), state, outdoorEntity);
  const priceHistory = appendCurrentValue(historyToDataPoints(history[priceEntity]), state, priceEntity);

  const forecastEntity = state[room.entities['temperature_forecast']];
  const forecastData = forecastEntity?.attributes?.forecast || [];

  const priceForecastEntity = state[systemEntity('electricity_price_forecast')];
  const priceForecastData = priceForecastEntity?.attributes?.forecast || [];
  const priceForecast = forecastToDataPoints(priceForecastData, 'price');

  const tempForecastNonlinear = forecastToDataPoints(forecastData, 'temperature');
  const tempForecastLinearised = forecastToDataPoints(forecastData, 'linearised_temperature');
  const setpointForecast = forecastToDataPoints(forecastData, 'setpoint');
  const constraintUpperForecast = forecastToDataPoints(forecastData, 'constraint_upper');
  const constraintLowerForecast = forecastToDataPoints(forecastData, 'constraint_lower');
  const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
  const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
  const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temp');

  buildTemperatureChart(
    tempChart,
    filteredHistory, measuredHistory,
    setpointHistory, setpointForecast,
    tempForecastNonlinear, tempForecastLinearised,
    constraintUpperHistory, constraintUpperForecast,
    constraintLowerHistory, constraintLowerForecast,
  );
  buildPowerChart(powerChart, powerHistory, powerForecast, priceHistory, priceForecast, state, room);
  buildDisturbanceChart(disturbChart, outdoorHistory, outdoorForecast, solarHistory, solarForecast);
}

function appendCurrentValue(dataPoints, state, entityId) {
  const val = entityValue(state, entityId);
  if (val !== null) {
    dataPoints.push({ x: Date.now(), y: val });
  }
  return dataPoints;
}

function computeYLimits(allDataPoints, bounds, marginFraction = 0.05) {
  let minVal = Infinity;
  let maxVal = -Infinity;

  for (const points of allDataPoints) {
    for (const p of points) {
      if (p && p.y != null) {
        if (p.y < minVal) minVal = p.y;
        if (p.y > maxVal) maxVal = p.y;
      }
    }
  }

  for (const b of bounds) {
    if (b != null) {
      if (b < minVal) minVal = b;
      if (b > maxVal) maxVal = b;
    }
  }

  if (!isFinite(minVal) || !isFinite(maxVal)) return { yMin: undefined, yMax: undefined };

  const range = maxVal - minVal || 1;
  const margin = range * marginFraction;
  return {
    yMin: minVal - margin,
    yMax: maxVal + margin,
  };
}

function buildTemperatureChart(
  chart,
  filteredHistory, measuredHistory,
  setpointHistory, setpointForecast,
  forecastNonlinear, forecastLinearised,
  constraintUpperHistory, constraintUpperForecast,
  constraintLowerHistory, constraintLowerForecast,
) {
  const combinedSetpoint = [...setpointHistory, ...setpointForecast];
  const combinedUpper = [...constraintUpperHistory, ...constraintUpperForecast];
  const combinedLower = [...constraintLowerHistory, ...constraintLowerForecast];

  const allData = [
    filteredHistory, measuredHistory,
    combinedSetpoint, forecastNonlinear, forecastLinearised,
    combinedUpper, combinedLower,
  ];
  const { yMin, yMax } = computeYLimits(allData, []);

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

  if (combinedSetpoint.length > 0) {
    datasets.push(
      makeDataset('Setpoint', combinedSetpoint, '#e57373', {
        dashed: true, borderWidth: 1, pointRadius: 0, stepped: 'before',
      })
    );
  }

  // Shade outside the comfort corridor without drawing visible boundary lines.
  // In Chart.js 4, fill.above/below refer to whether the DATASET is above/below
  // the TARGET — not the direction of the fill area. The upper constraint is
  // always below 'end' (chart top), so 'below' color applies; the lower
  // constraint is always above 'start' (chart bottom), so 'above' color applies.
  if (combinedUpper.length > 0) {
    datasets.push(
      makeDataset('Constraint Upper', combinedUpper, 'transparent', {
        borderWidth: 0, pointRadius: 0, stepped: 'before',
        fill: { target: 'end', above: 'transparent', below: 'rgba(229,115,115,0.12)' },
      })
    );
  }
  if (combinedLower.length > 0) {
    datasets.push(
      makeDataset('Constraint Lower', combinedLower, 'transparent', {
        borderWidth: 0, pointRadius: 0, stepped: 'before',
        fill: { target: 'start', above: 'rgba(229,115,115,0.12)', below: 'transparent' },
      })
    );
  }

  chart.render(datasets, { yMin, yMax });
}

function buildPowerChart(chart, powerHistory, powerForecast, priceHistory, priceForecast, state, room) {
  const maxPowerAttr = entityAttr(state, room.entities['heating_power_forecast'], 'max_power');
  const maxPower = maxPowerAttr ? parseFloat(maxPowerAttr) : null;
  const minPower = maxPower !== null ? -maxPower : null;

  const allPower = [powerHistory, powerForecast];
  const boundsArr = [maxPower, minPower, 0];
  const { yMin, yMax } = computeYLimits(allPower, boundsArr);

  const allPrice = [...priceHistory, ...priceForecast];
  const { yMin: priceMin, yMax: priceMax } = computeYLimits([allPrice], [0]);

  const datasets = [
    makeDataset('Measured', powerHistory, '#ffb74d', {
      borderWidth: 2, stepped: 'before',
      fill: true, backgroundColor: 'rgba(255,183,77,0.08)',
    }),
    makeDataset('Planned', powerForecast, '#ffb74d', {
      dashed: true, borderWidth: 2, stepped: 'before',
    }),
    makeDataset('Price', priceHistory, '#81c784', {
      borderWidth: 2, yAxisID: 'y2',
    }),
    makeDataset('Price Forecast', priceForecast, '#81c784', {
      dashed: true, borderWidth: 1.5, yAxisID: 'y2',
    }),
  ];

  if (maxPower !== null) {
    const now = Date.now();
    const past = now - 13 * 3600 * 1000;
    const lastTime = powerForecast.length > 0
      ? powerForecast[powerForecast.length - 1].x
      : now + 3 * 3600 * 1000;
    const boundTimes = [past, now, lastTime];

    datasets.push(
      makeDataset('Above Max', boundTimes.map((t) => ({ x: t, y: yMax + 1000 })), 'transparent', {
        fill: { target: { value: maxPower }, above: 'rgba(229,115,115,0.12)', below: 'transparent' },
        borderWidth: 0, pointRadius: 0, showLine: true,
      }),
      makeDataset('Below Min', boundTimes.map((t) => ({ x: t, y: yMin - 1000 })), 'transparent', {
        fill: { target: { value: minPower }, above: 'transparent', below: 'rgba(229,115,115,0.12)' },
        borderWidth: 0, pointRadius: 0, showLine: true,
      })
    );
  }

  chart.render(datasets, { yMin, yMax, y2Min: priceMin, y2Max: priceMax });
}

function buildDisturbanceChart(chart, outdoorHistory, outdoorForecast, solarHistory, solarForecast) {
  const allOutdoor = [...outdoorHistory, ...outdoorForecast];
  const allSolar = [...solarHistory, ...solarForecast];

  const { yMin: outdoorMin, yMax: outdoorMax } = computeYLimits([allOutdoor], []);
  const { yMin: solarMin, yMax: solarMax } = computeYLimits([allSolar], [0]);

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

  chart.render(datasets, { yMin: outdoorMin, yMax: outdoorMax, y2Min: solarMin, y2Max: solarMax });
}

function updateChartsFromState(room, state, tempChart, powerChart, disturbChart) {
  const forecastEntity = state[room.entities['temperature_forecast']];
  if (!forecastEntity?.attributes?.forecast) return;

  const forecastData = forecastEntity.attributes.forecast;
  const tempForecast = forecastToDataPoints(forecastData, 'temperature');
  const tempLinearised = forecastToDataPoints(forecastData, 'linearised_temperature');
  const setpointData = forecastToDataPoints(forecastData, 'setpoint');
  const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
  const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
  const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temp');

  const priceForecastEntity = state[systemEntity('electricity_price_forecast')];
  const priceForecastData = priceForecastEntity?.attributes?.forecast || [];
  const priceForecast = forecastToDataPoints(priceForecastData, 'price');

  if (tempChart._chart) {
    const ds = tempChart._chart.data.datasets;
    const now = Date.now();

    if (ds[2]) ds[2].data = tempForecast;
    if (ds[3] && tempLinearised.length > 0) ds[3].data = tempLinearised;

    const constraintUpperForecast = forecastToDataPoints(forecastData, 'constraint_upper');
    const constraintLowerForecast = forecastToDataPoints(forecastData, 'constraint_lower');

    for (const [label, newForecast] of [
      ['Setpoint', setpointData],
      ['Constraint Upper', constraintUpperForecast],
      ['Constraint Lower', constraintLowerForecast],
    ]) {
      const idx = ds.findIndex((d) => d.label === label);
      if (idx >= 0) {
        const existingHistory = ds[idx].data.filter((p) => p.x <= now);
        ds[idx].data = [...existingHistory, ...newForecast];
      }
    }

    tempChart._chart.update('none');
  }

  if (powerChart._chart) {
    const ds = powerChart._chart.data.datasets;
    if (ds[1]) ds[1].data = powerForecast;

    const priceIdx = ds.findIndex((d) => d.label === 'Price');
    const priceForecastIdx = ds.findIndex((d) => d.label === 'Price Forecast');
    if (priceForecastIdx >= 0) ds[priceForecastIdx].data = priceForecast;

    const combinedPrice = [
      ...(priceIdx >= 0 ? ds[priceIdx].data : []),
      ...(priceForecastIdx >= 0 ? priceForecast : []),
    ];
    const { yMin: priceMin, yMax: priceMax } = computeYLimits([combinedPrice], [0]);
    if (powerChart._chart.options?.scales?.y2) {
      powerChart._chart.options.scales.y2.min = priceMin;
      powerChart._chart.options.scales.y2.max = priceMax;
    }

    powerChart._chart.update('none');
  }

  if (disturbChart._chart) {
    const ds = disturbChart._chart.data.datasets;
    if (ds[1]) ds[1].data = outdoorForecast;
    if (ds[3]) ds[3].data = solarForecast;
    disturbChart._chart.update('none');
  }
}
