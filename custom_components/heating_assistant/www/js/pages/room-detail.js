import { TimeSeriesChart, makeDataset, historyToDataPoints, forecastToDataPoints, loadChartJs } from '../components/time-series-chart.js';
import { createGauge, updateGauge } from '../components/gauge.js';
import { createClimateCard } from '../components/climate-card.js';
import { createCountdown } from '../components/countdown.js';
import { createScheduleOverview } from '../components/schedule-overview.js';
import { getRoomScheduleData } from '../schedule-utils.js';
import {
  formatPower,
  entityValue, entityAttr, systemEntity, modelFitLabel,
} from '../utils.js';

// Model-fit gauge severity mirrors the overview page's MODEL FIT gauge so the
// colour thresholds (GOOD / ACCEPTABLE / POOR) are identical across pages.
const FIT_SEVERITY = { good: 0.8, warning: 0.5, alarm: 0 };
// Fallback power-gauge span used until the room forecast supplies the actual
// heating/cooling capacity for this room.
const DEFAULT_MAX_POWER = 2000;

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

/** Whether the room is currently off — the effective state published by the
 *  coordinator (covers both the user power toggle and an active off-schedule). */
function computeRoomOff(state, slug) {
  const attrs = state[CONFIG_ENTITY]?.attributes || {};
  if (attrs.room_active && slug in attrs.room_active) return attrs.room_active[slug] === false;
  if (attrs.room_enabled && slug in attrs.room_enabled) return attrs.room_enabled[slug] === false;
  return false;
}

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

  const tempVal = entityValue(state, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
  const powerVal = entityValue(state, room.entities['heating_power_measured']);
  const fitVal = entityValue(state, room.entities['model_fit_quality']);
  const fitInfo = modelFitLabel(fitVal);
  const setpointVal = entityValue(state, room.entities['setpoint']);
  const comfortLowerVal = entityValue(state, room.entities['constraint_lower']);
  const comfortUpperVal = entityValue(state, room.entities['constraint_upper']);
  const climateEntityId = `climate.heating_assistant_${roomSlug}`;
  const offVal = computeRoomOff(state, roomSlug);

  // Climate card replaces the standalone Temperature + Setpoint KPIs: it shows
  // the current temperature, the active setpoint, the comfort corridor, and
  // lets the user retarget the setpoint inline (committed to HA after a short
  // debounce).
  const climateCard = createClimateCard({
    temperature: tempVal,
    setpoint: setpointVal,
    power: powerVal,
    comfortLower: comfortLowerVal,
    comfortUpper: comfortUpperVal,
    off: offVal,
    onSetpointChange: async (newSp) => {
      try {
        await hass.callService('climate', 'set_temperature', {
          entity_id: climateEntityId,
          temperature: newSp,
        });
      } catch (err) {
        // Service call failed; the display self-corrects on the next state update.
      }
    },
    onPowerToggle: async (turnOff) => {
      try {
        await hass.callService('climate', turnOff ? 'turn_off' : 'turn_on', {
          entity_id: climateEntityId,
        });
      } catch (err) {
        // Service call failed; the display self-corrects on the next state update.
      }
    },
  });
  const climateSection = document.createElement('div');
  climateSection.className = 'room-climate';
  climateSection.appendChild(climateCard.element);
  container.appendChild(climateSection);

  const kpiGrid = document.createElement('div');
  kpiGrid.className = 'grid-kpi';
  container.appendChild(kpiGrid);

  // KPIs use the same gauge design as the overview page (label + value +
  // severity bar) so the two overview surfaces share a common visual language.
  // The metrics differ — here the room-level Power and Model Fit.
  const powerBounds = { min: 0, max: DEFAULT_MAX_POWER };

  const powerGauge = createGauge({
    value: powerVal,
    min: powerBounds.min,
    max: powerBounds.max,
    label: 'POWER',
    format: formatPower,
  });

  const fitGauge = createGauge({
    value: fitVal,
    min: 0,
    max: 1,
    label: 'MODEL FIT',
    format: () => fitInfo.label,
    severity: FIT_SEVERITY,
  });

  kpiGrid.appendChild(powerGauge);
  kpiGrid.appendChild(fitGauge);

  function paintPowerGauge(value) {
    updateGauge(powerGauge, {
      value, min: powerBounds.min, max: powerBounds.max, format: formatPower,
    });
  }

  const countdown = createCountdown(state, true);
  kpiGrid.appendChild(countdown.element);

  // ── Schedule overview ──────────────────────────────────────────────────────
  // Mirrors the schedules index-card design; clicking opens the editable
  // schedule detail page for this room.
  const scheduleOverview = createScheduleOverview(room, null, {
    onEdit: () => { window.location.hash = `#schedules/${roomSlug}`; },
  });
  const scheduleSection = document.createElement('div');
  scheduleSection.className = 'room-schedule-overview';
  scheduleSection.appendChild(scheduleOverview.element);
  container.appendChild(scheduleSection);

  function refreshSchedule() {
    connection.getSchedules().then((roomSchedules) => {
      scheduleOverview.update(getRoomScheduleData(roomSchedules, room));
    }).catch(() => { /* leave the last-rendered schedule in place on failure */ });
  }
  refreshSchedule();

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

  // lastRunTs tracks the MPC solve timestamp; when it changes we know new
  // forecast data is available and re-fetch via the WS endpoint.
  const lastRunTs = { value: null };
  loadChartsData(room, state, connection, tempChart, powerChart, disturbChart, lastRunTs, (roomForecast) => {
    // The forecast carries this room's heating/cooling capacity — use it to
    // scale the power gauge so the bar reflects power as a fraction of capacity.
    if (roomForecast?.max_power != null) powerBounds.max = roomForecast.max_power;
    if (roomForecast?.max_cooling_power != null) powerBounds.min = -roomForecast.max_cooling_power;
    paintPowerGauge(entityValue(latestState, room.entities['heating_power_measured']));
  });

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
      const cl = entityValue(newState, room.entities['constraint_lower']);
      const cu = entityValue(newState, room.entities['constraint_upper']);
      const off = computeRoomOff(newState, roomSlug);

      climateCard.update({
        temperature: tv, setpoint: sp, power: pv,
        comfortLower: cl, comfortUpper: cu, off,
      });
      paintPowerGauge(pv);
      updateGauge(fitGauge, {
        value: fv, min: 0, max: 1, format: () => fi.label, severity: FIT_SEVERITY,
      });

      // Keep the schedule overview in sync with any toggle/save that triggered
      // this state update.
      refreshSchedule();

      updateChartsFromState(room, newState, connection, tempChart, powerChart, disturbChart, lastRunTs);
    },
    destroy() {
      clearInterval(countdownInterval);
      climateCard.destroy();
      tempChart.destroy();
      powerChart.destroy();
      disturbChart.destroy();
    },
  };
}

async function loadChartsData(room, state, connection, tempChart, powerChart, disturbChart, lastRunTs, onPowerBounds) {
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

  const [history, forecasts] = await Promise.all([
    connection.getHistory(historyEntities, 12),
    connection.getForecasts(),
  ]);

  // Seed lastRunTs so the first state-change event that matches the initial
  // MPC timestamp is treated as a no-op rather than an immediate re-fetch.
  lastRunTs.value = entityAttr(state, systemEntity('mpc_performance'), 'last_run_ts');

  const filteredHistory = historyToDataPoints(history[tempFilteredEntity]);
  const measuredHistory = historyToDataPoints(history[tempMeasuredEntity]);
  const setpointHistory = historyToDataPoints(history[setpointEntity]);
  const constraintUpperHistory = historyToDataPoints(history[constraintUpperEntity]);
  const constraintLowerHistory = historyToDataPoints(history[constraintLowerEntity]);
  const powerHistory = historyToDataPoints(history[powerMeasuredEntity]);
  const solarHistory = appendCurrentValue(historyToDataPoints(history[solarMeasuredEntity]), state, solarMeasuredEntity);
  const outdoorHistory = appendCurrentValue(historyToDataPoints(history[outdoorEntity]), state, outdoorEntity);
  const priceHistory = appendCurrentValue(historyToDataPoints(history[priceEntity]), state, priceEntity);

  const roomForecast = forecasts.rooms?.[room.slug];
  if (onPowerBounds) onPowerBounds(roomForecast);

  const forecastData = roomForecast?.forecast || [];
  const priceForecastData = forecasts.price_forecast || [];
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
  buildPowerChart(powerChart, powerHistory, powerForecast, priceHistory, priceForecast, roomForecast);
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

function buildPowerChart(chart, powerHistory, powerForecast, priceHistory, priceForecast, roomForecast) {
  const maxPower = roomForecast?.max_power ?? null;
  // Cooling capacity is asymmetric to heating: use the backend-provided
  // max_cooling_power (magnitude) for the lower bound, defaulting to 0 (no
  // cooling) rather than a mirror of the heating limit.
  const maxCoolingPower = roomForecast?.max_cooling_power ?? null;
  const minPower = maxCoolingPower !== null ? -maxCoolingPower : 0;

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
      borderWidth: 2, yAxisID: 'y2', stepped: 'before',
    }),
    makeDataset('Price Forecast', priceForecast, '#81c784', {
      dashed: true, borderWidth: 1.5, yAxisID: 'y2', stepped: 'before',
    }),
  ];

  if (maxPower !== null || maxCoolingPower !== null) {
    const now = Date.now();
    const past = now - 13 * 3600 * 1000;
    const lastTime = powerForecast.length > 0
      ? powerForecast[powerForecast.length - 1].x
      : now + 3 * 3600 * 1000;
    const boundTimes = [past, now, lastTime];

    if (maxPower !== null) {
      datasets.push(
        makeDataset('Above Max', boundTimes.map((t) => ({ x: t, y: yMax + 1000 })), 'transparent', {
          fill: { target: { value: maxPower }, above: 'rgba(229,115,115,0.12)', below: 'transparent' },
          borderWidth: 0, pointRadius: 0, showLine: true,
        })
      );
    }
    // Only shade the cooling bound when the room actually has cooling capacity;
    // a heating-only room has no negative-power limit to draw.
    if (maxCoolingPower !== null) {
      datasets.push(
        makeDataset('Below Min', boundTimes.map((t) => ({ x: t, y: yMin - 1000 })), 'transparent', {
          fill: { target: { value: minPower }, above: 'transparent', below: 'rgba(229,115,115,0.12)' },
          borderWidth: 0, pointRadius: 0, showLine: true,
        })
      );
    }
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

function updateChartsFromState(room, state, connection, tempChart, powerChart, disturbChart, lastRunTs) {
  // Forecast data only changes when the MPC runs; detect that via last_run_ts.
  const currentRunTs = entityAttr(state, systemEntity('mpc_performance'), 'last_run_ts');
  if (currentRunTs === lastRunTs.value) return;
  lastRunTs.value = currentRunTs;

  // Re-fetch forecast data from the backend and update the chart forecast datasets.
  connection.getForecasts().then((forecasts) => {
    if (!forecasts) return;
    const forecastData = forecasts.rooms?.[room.slug]?.forecast || [];
    const priceForecastData = forecasts.price_forecast || [];

    const tempForecast = forecastToDataPoints(forecastData, 'temperature');
    const tempLinearised = forecastToDataPoints(forecastData, 'linearised_temperature');
    const setpointData = forecastToDataPoints(forecastData, 'setpoint');
    const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
    const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
    const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temp');
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
  });
}
