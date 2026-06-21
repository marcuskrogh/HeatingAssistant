import { TimeSeriesChart, makeDataset, historyToDataPoints, historyToEnabledPoints, forecastToDataPoints, forecastToEnabledPoints, loadChartJs, sensorHistoriesToMinMaxSpan } from '../components/time-series-chart.js';
import { createGauge, updateGauge } from '../components/gauge.js';
import { createClimateCard } from '../components/climate-card.js';
import { createCountdown } from '../components/countdown.js';
import { createScheduleOverview } from '../components/schedule-overview.js';
import { getRoomScheduleData } from '../schedule-utils.js';
import { findActiveExperiment, experimentBands } from '../experiment-utils.js';
import {
  formatPower, formatPowerKw, formatTemperature, formatPrice,
  entityValue, entityAttr, systemEntity,
  wattsToKw, wattsToKwPoints,
} from '../utils.js';

// Fallback power-gauge span used until the room forecast supplies the actual
// heating/cooling capacity for this room.
const DEFAULT_MAX_POWER = 2000;
// Outdoor-temperature gauge thresholds, matched to the overview page so the
// colour bands are identical across surfaces.
const OUTDOOR_SEVERITY = { good: 5, warning: -5, alarm: -15 };
// Per-room solar-gain gauge span [W]; typical sunlit windows land well within.
const DEFAULT_MAX_SOLAR = 1000;
// Compact kW axis/tooltip formatting for power plots (values already in kW).
const POWER_KW_TICK = (v) => Number(v).toFixed(1);

const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

/** Format the current electricity price with the unit the price entity reports
 *  (currency/kWh), falling back to a bare number when no unit is available. */
function formatPriceWithUnit(state, priceEntity) {
  const value = entityValue(state, priceEntity);
  const text = formatPrice(value);
  if (text === '—') return text;
  const unit = entityAttr(state, priceEntity, 'unit_of_measurement');
  return unit ? `${text} ${unit}` : text;
}

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

  // Tracks the freshest state snapshot; declared up-front because some gauge
  // formatters (e.g. the price unit) read live attributes lazily on each paint.
  let latestState = state;
  // The identification experiment currently exciting this room, or null —
  // refreshed via WebSocket so the climate card can show the in-progress look.
  let activeExperiment = null;
  // Latest experiment list and this room's forecast block, kept so the plot's
  // experiment shading can be recomputed (and grid-aligned) whenever either the
  // experiment list (30 s poll) or the forecast (each MPC solve) changes.
  let latestExperiments = null;
  let latestForecastRoom = null;

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
    onComfortOffsetChange: async (newOffset) => {
      try {
        await hass.callService('heating_assistant', 'set_room_comfort_offset', {
          room_name: roomSlug,
          comfort_offset: newOffset,
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
  // severity bar). At room level these are the *current system values for this
  // room* — heater power, energy price, outdoor temperature and solar gain —
  // since indoor temperature / setpoint / comfort band already live on the
  // climate card above. Model fit is an overall-model metric and lives only on
  // the main overview.
  const powerBounds = { min: 0, max: DEFAULT_MAX_POWER };

  const priceEntity = systemEntity('electricity_price');
  const outdoorEntity = systemEntity('outdoor_temperature_measured');
  const solarEntity = room.entities['solar_gain_measured'];

  const powerGauge = createGauge({
    value: powerVal,
    min: powerBounds.min,
    max: powerBounds.max,
    label: 'POWER',
    format: formatPower,
  });

  // Price bar spans the upcoming forecast range (filled in from getForecasts);
  // until then it falls back to a 0-based unit scale. Cheaper = greener.
  const priceBounds = { min: 0, max: 1 };
  const priceGauge = createGauge({
    value: entityValue(state, priceEntity) ?? 0,
    min: priceBounds.min,
    max: priceBounds.max,
    label: 'ENERGY PRICE',
    format: () => formatPriceWithUnit(latestState, priceEntity),
  });
  if (!(priceEntity in state)) priceGauge.style.display = 'none';

  const outdoorGauge = createGauge({
    value: entityValue(state, outdoorEntity) ?? 0,
    min: -30,
    max: 40,
    label: 'OUTDOOR',
    format: formatTemperature,
    severity: OUTDOOR_SEVERITY,
  });

  const solarGauge = createGauge({
    value: entityValue(state, solarEntity) ?? 0,
    min: 0,
    max: DEFAULT_MAX_SOLAR,
    label: 'SOLAR GAIN',
    format: formatPower,
    severity: { good: 300, warning: 50, alarm: 0 },
  });

  kpiGrid.appendChild(powerGauge);
  kpiGrid.appendChild(priceGauge);
  kpiGrid.appendChild(outdoorGauge);
  kpiGrid.appendChild(solarGauge);

  function paintPowerGauge(value) {
    updateGauge(powerGauge, {
      value, min: powerBounds.min, max: powerBounds.max, format: formatPower,
    });
  }

  function paintPriceGauge(value) {
    // Hide the price KPI entirely when no price entity is configured (the
    // backend then publishes no electricity-price sensor at all).
    priceGauge.style.display = priceEntity in latestState ? '' : 'none';
    // Colour cheaper prices green: position within the forecast range, inverted.
    const span = priceBounds.max - priceBounds.min || 1;
    updateGauge(priceGauge, {
      value: value ?? 0, min: priceBounds.min, max: priceBounds.max,
      format: () => formatPriceWithUnit(latestState, priceEntity),
      severity: {
        good: priceBounds.min + span / 3,
        warning: priceBounds.min + (2 * span) / 3,
        alarm: priceBounds.max,
        inverse: true,
      },
    });
  }

  function paintOutdoorGauge(value) {
    updateGauge(outdoorGauge, {
      value: value ?? 0, min: -30, max: 40,
      format: formatTemperature, severity: OUTDOOR_SEVERITY,
    });
  }

  function paintSolarGauge(value) {
    updateGauge(solarGauge, {
      value: value ?? 0, min: 0, max: DEFAULT_MAX_SOLAR,
      format: formatPower, severity: { good: 300, warning: 50, alarm: 0 },
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

  // Resolve whether an identification experiment is exciting this room and push
  // it to the climate card so it can flip into the "experiment in progress"
  // look. Polled on a slow cadence because the scheduled → running transition
  // happens on a wall-clock boundary that need not coincide with a state event.
  // Recompute the shaded experiment window on every plot from the latest
  // experiment list and forecast, snapping the future edge to the MPC step grid
  // so the shading lines up with the (stepped) actuator signal.
  function applyExperimentBands() {
    if (latestExperiments == null) return;
    const bands = experimentBands(latestExperiments, roomSlug, latestForecastRoom);
    tempChart.setExperimentBands(bands);
    powerChart.setExperimentBands(bands);
    disturbChart.setExperimentBands(bands);
  }

  function onForecast(forecasts) {
    const roomForecast = forecasts?.rooms?.[roomSlug] || null;
    latestForecastRoom = roomForecast;
    applyExperimentBands();
    // Keep the power gauge and chart corridor aligned with the refreshed
    // rated capacity whenever the MPC publishes a new forecast.
    if (roomForecast) {
      const gaugeMax = roomForecast.current_rated_max_power ?? roomForecast.max_power;
      if (gaugeMax != null) powerBounds.max = gaugeMax;
      const gaugeMin = roomForecast.max_cooling_power
        ?? roomForecast.current_max_cooling_power;
      if (gaugeMin != null) powerBounds.min = -gaugeMin;
      paintPowerGauge(entityValue(latestState, room.entities['heating_power_measured']));
    }
  }

  function refreshExperiment() {
    connection.listExperiments().then((experiments) => {
      if (experiments == null) return; // fetch failed — keep the current state
      latestExperiments = experiments;
      activeExperiment = findActiveExperiment(experiments, roomSlug);
      climateCard.update({ experiment: activeExperiment });
      applyExperimentBands();
      // Update the schedule overview with room-specific experiments
      const roomExps = experiments.filter((e) => e.room_slug === roomSlug);
      scheduleOverview.update(undefined, roomExps);
    }).catch(() => { /* keep the last-known experiment state on failure */ });
  }

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
    yLabel: 'kW',
    yTickFormat: POWER_KW_TICK,
    yValueFormat: formatPowerKw,
    y2: true,
    y2Label: 'Price',
    height: 200,
  });

  const disturbChart = new TimeSeriesChart(disturbChartEl, {
    title: 'DISTURBANCES',
    yLabel: '\u00b0C',
    y2: true,
    y2Label: 'kW',
    y2TickFormat: POWER_KW_TICK,
    y2ValueFormat: formatPowerKw,
    height: 200,
  });

  // Resolve experiments now that the charts exist (the band overlay needs them);
  // polled on a slow cadence because the scheduled \u2192 running transition happens
  // on a wall-clock boundary that need not coincide with a state event.
  refreshExperiment();
  const experimentInterval = setInterval(refreshExperiment, 30000);

  // Plot display settings (Configuration → Display). historyHours sizes the
  // measured-history window; forecastHours requests a plot prediction horizon
  // that may extend past the controller horizon (0 = match controller horizon).
  // Defaults match the backend until the WS fetch below resolves.
  const plotSettings = { historyHours: 12, forecastHours: 0 };

  // lastRunTs tracks the MPC solve timestamp; when it changes we know new
  // forecast data is available and re-fetch via the WS endpoint.
  const lastRunTs = { value: null };
  const onChartsReady = (roomForecast, priceForecast) => {
    // Scale the power gauge to rated heating/cooling capacity (no sysid scale).
    const gaugeMax = roomForecast?.current_rated_max_power ?? roomForecast?.max_power;
    if (gaugeMax != null) powerBounds.max = gaugeMax;
    const gaugeMin = roomForecast?.max_cooling_power
      ?? roomForecast?.current_max_cooling_power;
    if (gaugeMin != null) powerBounds.min = -gaugeMin;
    // Same forecast block feeds the experiment-band grid alignment.
    latestForecastRoom = roomForecast || null;
    applyExperimentBands();
    paintPowerGauge(entityValue(latestState, room.entities['heating_power_measured']));

    // Span the price bar over the upcoming price range so the fill shows where
    // the current price sits within the forecast horizon.
    const prices = (priceForecast || []).map((p) => p.y).filter((y) => y != null);
    const current = entityValue(latestState, priceEntity);
    if (current != null) prices.push(current);
    if (prices.length > 0) {
      priceBounds.min = Math.min(...prices);
      priceBounds.max = Math.max(...prices);
    }
    paintPriceGauge(current);
  };

  // Resolve display settings before the first chart load so the history window
  // and forecast horizon honour the user's Configuration choices. Falls back to
  // the defaults above if the fetch fails.
  connection.getUiSettings().then((s) => {
    if (s) {
      const h = Number(s.plot_history_hours);
      if (Number.isFinite(h) && h > 0) plotSettings.historyHours = h;
      const f = Number(s.plot_forecast_hours);
      if (Number.isFinite(f)) plotSettings.forecastHours = f;
    }
  }).catch(() => { /* keep defaults */ }).then(() => {
    loadChartsData(room, state, connection, tempChart, powerChart, disturbChart, lastRunTs, plotSettings, onChartsReady);
  });

  const countdownInterval = setInterval(() => countdown.tick(latestState), 1000);

  return {
    update(newState) {
      latestState = newState;

      const tv = entityValue(newState, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
      const pv = entityValue(newState, room.entities['heating_power_measured']);
      const sp = entityValue(newState, room.entities['setpoint']);
      const cl = entityValue(newState, room.entities['constraint_lower']);
      const cu = entityValue(newState, room.entities['constraint_upper']);
      const off = computeRoomOff(newState, roomSlug);

      climateCard.update({
        temperature: tv, setpoint: sp, power: pv,
        comfortLower: cl, comfortUpper: cu, off,
        experiment: activeExperiment,
      });
      paintPowerGauge(pv);
      paintPriceGauge(entityValue(newState, priceEntity));
      paintOutdoorGauge(entityValue(newState, outdoorEntity));
      paintSolarGauge(entityValue(newState, solarEntity));

      // Keep the schedule overview in sync with any toggle/save that triggered
      // this state update.
      refreshSchedule();

      updateChartsFromState(room, newState, connection, tempChart, powerChart, disturbChart, lastRunTs, onForecast, plotSettings);
    },
    destroy() {
      clearInterval(countdownInterval);
      clearInterval(experimentInterval);
      climateCard.destroy();
      tempChart.destroy();
      powerChart.destroy();
      disturbChart.destroy();
    },
  };
}

async function loadChartsData(room, state, connection, tempChart, powerChart, disturbChart, lastRunTs, plotSettings, onPowerBounds) {
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

  const historyHours = (plotSettings && plotSettings.historyHours) || 12;
  const forecastHours = (plotSettings && plotSettings.forecastHours) || 0;
  const windowStart = Date.now() - historyHours * 3600 * 1000;

  const [history, forecasts] = await Promise.all([
    connection.getHistory(historyEntities, historyHours),
    connection.getForecasts(forecastHours),
  ]);

  const measuredHistory = historyToDataPoints(history[tempMeasuredEntity]);

  // Fetch raw sensor histories and build a min/max span when the room has
  // multiple configured temperature sensors. Align the band to the control
  // measurement timeline so it renders as one continuous shaded region.
  const slugify = (name) => (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  let sensorSpan = null;
  try {
    const modelCfg = await connection.getModelConfig();
    const roomCfg = modelCfg?.rooms?.find((r) => slugify(r.name) === room.slug);
    let rawSensorEntities = [];
    if (roomCfg) {
      rawSensorEntities = Array.isArray(roomCfg.temp_sensors) ? [...roomCfg.temp_sensors] : [];
      if (roomCfg.temp_sensor && !rawSensorEntities.includes(roomCfg.temp_sensor)) {
        rawSensorEntities.unshift(roomCfg.temp_sensor);
      }
    }
    if (rawSensorEntities.length > 1 && measuredHistory.length > 0) {
      const rawHistory = await connection.getHistory(rawSensorEntities, historyHours);
      const sensorSeries = [];
      for (const entityId of rawSensorEntities) {
        const pts = historyToDataPoints(rawHistory[entityId]);
        if (pts.length > 0) sensorSeries.push(pts);
      }
      if (sensorSeries.length > 1) {
        sensorSpan = sensorHistoriesToMinMaxSpan(sensorSeries, measuredHistory);
      }
    }
  } catch (e) {
    // Graceful degradation: skip the sensor span if config fetch fails.
  }

  // Seed lastRunTs so the first state-change event that matches the initial
  // MPC timestamp is treated as a no-op rather than an immediate re-fetch.
  lastRunTs.value = entityAttr(state, systemEntity('mpc_performance'), 'last_run_ts');

  // HA's history_during_period returns the initial boundary state with its
  // original lu (last_updated) timestamp, which may predate the chart window by
  // days for slowly-changing sensors like setpoint/constraints. Clamp it to the
  // window start so the x-axis is not distorted.
  function clampFirstToWindow(pts) {
    if (pts.length > 0 && pts[0].x < windowStart) {
      pts[0] = { ...pts[0], x: windowStart };
    }
    return pts;
  }

  // Chart.js stepped:'before' + spanGaps:false draws each step toward the NEXT
  // VALID point — not toward the next null. When the history has only one valid
  // entry before an off-period null (common for sensors that rarely change), the
  // segment has a single point and produces zero-width fill. Insert a synthetic
  // closing point at (null.x − 1 ms) with the same y so every valid run has at
  // least two points, matching the dense-point behaviour of the forecast data.
  function closeStepSegments(pts) {
    const out = [];
    for (let i = 0; i < pts.length; i++) {
      out.push(pts[i]);
      if (pts[i].y !== null && i + 1 < pts.length && pts[i + 1].y === null) {
        out.push({ x: pts[i + 1].x - 1, y: pts[i].y });
      }
    }
    return out;
  }

  const filteredHistory = historyToDataPoints(history[tempFilteredEntity]);
  const setpointHistory = closeStepSegments(clampFirstToWindow(historyToEnabledPoints(history[setpointEntity])));
  const constraintUpperHistory = closeStepSegments(clampFirstToWindow(historyToEnabledPoints(history[constraintUpperEntity])));
  const constraintLowerHistory = closeStepSegments(clampFirstToWindow(historyToEnabledPoints(history[constraintLowerEntity])));
  const powerHistory = appendCurrentValue(historyToDataPoints(history[powerMeasuredEntity]), state, powerMeasuredEntity);
  const solarHistory = appendCurrentValue(historyToDataPoints(history[solarMeasuredEntity]), state, solarMeasuredEntity);
  const outdoorHistory = appendCurrentValue(historyToDataPoints(history[outdoorEntity]), state, outdoorEntity);
  const priceHistory = appendCurrentValue(historyToDataPoints(history[priceEntity]), state, priceEntity);

  const roomForecast = forecasts.rooms?.[room.slug];

  const forecastData = roomForecast?.forecast || [];
  const priceForecastData = forecasts.price_forecast || [];
  const priceForecast = forecastToDataPoints(priceForecastData, 'price');

  // Hand the room capacity and the price-forecast range to the KPI gauges.
  if (onPowerBounds) onPowerBounds(roomForecast, priceForecast);

  const tempForecastNonlinear = forecastToDataPoints(forecastData, 'temperature');
  const tempForecastLinearised = forecastToDataPoints(forecastData, 'linearised_temperature');
  const setpointForecast = forecastToEnabledPoints(forecastData, 'setpoint');
  const constraintUpperForecast = forecastToEnabledPoints(forecastData, 'constraint_upper');
  const constraintLowerForecast = forecastToEnabledPoints(forecastData, 'constraint_lower');
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
    sensorSpan,
  );
  buildPowerChart(powerChart, powerHistory, powerForecast, priceHistory, priceForecast, roomForecast, windowStart);
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
  sensorSpan,
  options = {},
) {
  const forecastOnly = options.forecastOnly === true;
  const combinedSetpoint = forecastOnly
    ? setpointForecast
    : [...setpointHistory, ...setpointForecast];
  const combinedUpper = forecastOnly
    ? constraintUpperForecast
    : [...constraintUpperHistory, ...constraintUpperForecast];
  const combinedLower = forecastOnly
    ? constraintLowerForecast
    : [...constraintLowerHistory, ...constraintLowerForecast];

  const spanPts = sensorSpan ? [sensorSpan.min, sensorSpan.max] : [];
  const allData = forecastOnly
    ? [combinedSetpoint, forecastNonlinear, forecastLinearised, combinedUpper, combinedLower]
    : [
      filteredHistory, measuredHistory,
      combinedSetpoint, forecastNonlinear, forecastLinearised,
      combinedUpper, combinedLower,
      ...spanPts,
    ];
  const { yMin, yMax } = computeYLimits(allData, []);

  const datasets = [];
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Filtered', filteredHistory, '#4fc3f7', { borderWidth: 2 }),
      makeDataset('Measured', measuredHistory, '#e57373', {
        borderWidth: 0, pointRadius: 3, pointHoverRadius: 5,
        pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
        showLine: false,
      }),
    );
  }
  datasets.push(
    makeDataset('Forecast', forecastNonlinear, '#4fc3f7', {
      dashed: !forecastOnly, borderWidth: 2,
    }),
  );

  if (forecastLinearised.length > 0) {
    datasets.push(
      makeDataset('Linearised', forecastLinearised, '#ab47bc', {
        dashed: !forecastOnly, borderWidth: 1.5,
      }),
    );
  }

  if (combinedSetpoint.length > 0) {
    datasets.push(
      makeDataset('Setpoint', combinedSetpoint, '#e57373', {
        dashed: !forecastOnly, borderWidth: 1, pointRadius: 0, stepped: 'before', spanGaps: false,
      }),
    );
  }

  // Shade outside the comfort corridor without drawing visible boundary lines.
  // In Chart.js 4, fill.above/below refer to whether the DATASET is above/below
  // the TARGET — not the direction of the fill area. The upper constraint is
  // always below 'end' (chart top), so 'below' color applies; the lower
  // constraint is always above 'start' (chart bottom), so 'above' color applies.
  // spanGaps: false ensures that null points (off/disabled periods) create breaks
  // in the line and fill rather than bridging across them.
  if (combinedUpper.length > 0) {
    datasets.push(
      makeDataset('Constraint Upper', combinedUpper, 'transparent', {
        borderWidth: 0, pointRadius: 0, stepped: 'before', spanGaps: false,
        fill: { target: 'end', above: 'transparent', below: 'rgba(229,115,115,0.12)' },
      })
    );
  }
  if (combinedLower.length > 0) {
    datasets.push(
      makeDataset('Constraint Lower', combinedLower, 'transparent', {
        borderWidth: 0, pointRadius: 0, stepped: 'before', spanGaps: false,
        fill: { target: 'start', above: 'rgba(229,115,115,0.12)', below: 'transparent' },
      })
    );
  }

  if (sensorSpan && sensorSpan.min.length > 0 && sensorSpan.max.length > 0) {
    datasets.push(
      makeDataset('Sensor Min', sensorSpan.min, 'transparent', {
        borderWidth: 0, pointRadius: 0, tension: 0, order: 9,
      }),
      makeDataset('Sensor Range', sensorSpan.max, 'transparent', {
        borderWidth: 0,
        pointRadius: 0,
        tension: 0,
        fill: '-1',
        backgroundColor: 'rgba(79, 195, 247, 0.18)',
        order: 9,
      })
    );
  }

  chart.render(datasets, { yMin, yMax });
}

/**
 * Build time-series points for the dynamic capacity corridor.
 *
 * Returns two arrays of {x, y} points:
 *   heating — per-step COP-limited rated heating capacity (no power_scale)
 *   cooling — per-step rated cooling capacity, negated to a negative y value
 *
 * The historical window (from `windowStart` to the first forecast entry) is held
 * flat at the current-snapshot capacity; the forecast window uses per-step values
 * computed from the outdoor-temperature forecast so the corridor visibly moves
 * with outdoor temperature.
 */
function buildCapacityPoints(roomForecast, windowStart, forecastOnly = false) {
  const currentHeat = (
    roomForecast?.current_rated_max_power
    ?? roomForecast?.current_max_power
    ?? roomForecast?.max_power
    ?? null
  );
  const currentCool = (
    roomForecast?.max_cooling_power
    ?? roomForecast?.current_max_cooling_power
    ?? null
  );

  const heating = [];
  const cooling = [];

  if (!forecastOnly) {
    if (currentHeat != null) heating.push({ x: windowStart, y: wattsToKw(currentHeat) });
    if (currentCool != null) cooling.push({ x: windowStart, y: -wattsToKw(currentCool) });
  }

  for (const entry of (roomForecast?.forecast ?? [])) {
    const t = new Date(entry.time).getTime();
    if (entry.heating_capacity != null) heating.push({ x: t, y: wattsToKw(entry.heating_capacity) });
    if (entry.cooling_capacity != null) cooling.push({ x: t, y: -wattsToKw(entry.cooling_capacity) });
  }

  return { heating, cooling };
}

/**
 * Heating-side Y-axis anchor for the power chart [kW].
 *
 * Prefer the COP-limited rated capacity at the present outdoor temperature
 * (``current_rated_max_power``) so full command fills the scale.  Raw
 * ``max_power`` is the configured datasheet maximum at the rated COP point
 * and can be much higher than what a heat pump can deliver in cold weather,
 * which makes traces look capped around half the axis even at u = 1.
 */
function resolveHeatingPlotMaxKw(roomForecast) {
  return wattsToKw(
    roomForecast?.current_rated_max_power
    ?? roomForecast?.max_power
    ?? null,
  );
}

/** Refresh the power-chart Y limits and dynamic capacity corridor after a forecast update. */
function updatePowerChartBounds(chart, roomForecast, windowStart) {
  if (!chart._chart || !roomForecast) return;

  const maxPower = resolveHeatingPlotMaxKw(roomForecast);
  const maxCoolingPower = wattsToKw(roomForecast?.max_cooling_power ?? null);
  const minPower = maxCoolingPower !== null ? -maxCoolingPower : 0;

  const ds = chart._chart.data.datasets;
  const measuredIdx = ds.findIndex((d) => d.label === 'Measured Power');
  const plannedIdx = ds.findIndex((d) => d.label === 'Planned Power');
  const powerSeries = [
    measuredIdx >= 0 ? ds[measuredIdx].data : [],
    plannedIdx >= 0 ? ds[plannedIdx].data : [],
  ];
  const boundsArr = [maxPower, minPower, 0];
  const { yMin, yMax } = computeYLimits(powerSeries, boundsArr);

  if (chart._chart.options?.scales?.y) {
    chart._chart.options.scales.y.min = yMin;
    chart._chart.options.scales.y.max = yMax;
  }

  const historyStart = windowStart ?? chart._historyWindowStart ?? (Date.now() - 12 * 3600 * 1000);
  const { heating: heatingCapPts, cooling: coolingCapPts } = buildCapacityPoints(roomForecast, historyStart);

  const heatCapIdx = ds.findIndex((d) => d.label === 'Heating Capacity');
  if (heatCapIdx >= 0) ds[heatCapIdx].data = heatingCapPts;

  const coolCapIdx = ds.findIndex((d) => d.label === 'Cooling Capacity');
  if (coolCapIdx >= 0) ds[coolCapIdx].data = coolingCapPts;

  chart._chart.update('none');
}

function buildPowerChart(
  chart, powerHistory, powerForecast, priceHistory, priceForecast, roomForecast, windowStart,
  options = {},
) {
  const forecastOnly = options.forecastOnly === true;
  const maxPower = resolveHeatingPlotMaxKw(roomForecast);
  const maxCoolingPower = wattsToKw(roomForecast?.max_cooling_power ?? null);
  const minPower = maxCoolingPower !== null ? -maxCoolingPower : 0;

  const powerHistoryKw = wattsToKwPoints(powerHistory);
  const powerForecastKw = wattsToKwPoints(powerForecast);
  const allPower = forecastOnly ? [powerForecastKw] : [powerHistoryKw, powerForecastKw];
  const boundsArr = [maxPower, minPower, 0];
  const { yMin, yMax } = computeYLimits(allPower, boundsArr);

  const allPrice = forecastOnly ? priceForecast : [...priceHistory, ...priceForecast];
  const { yMin: priceMin, yMax: priceMax } = computeYLimits([allPrice], [0]);

  const datasets = [];
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Measured Power', powerHistoryKw, '#ffb74d', {
        borderWidth: 2, stepped: 'before',
        fill: true, backgroundColor: 'rgba(255,183,77,0.08)',
      }),
    );
  }
  datasets.push(
    makeDataset('Planned Power', powerForecastKw, '#ffb74d', {
      dashed: !forecastOnly, borderWidth: 2, stepped: 'before',
      ...(forecastOnly ? { fill: true, backgroundColor: 'rgba(255,183,77,0.08)' } : {}),
    }),
  );
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Price', priceHistory, '#81c784', {
        borderWidth: 2, yAxisID: 'y2', stepped: 'before',
      }),
    );
  }
  datasets.push(
    makeDataset(forecastOnly ? 'Price' : 'Price Forecast', priceForecast, '#81c784', {
      dashed: !forecastOnly, borderWidth: forecastOnly ? 2 : 1.5, yAxisID: 'y2', stepped: 'before',
    }),
  );

  // Dynamic capacity corridor: invisible boundary datasets whose fill extends to
  // the axis edge so infeasible regions are shaded without dashed reference lines.
  const historyStart = windowStart ?? (Date.now() - 12 * 3600 * 1000);
  chart._historyWindowStart = historyStart;
  const { heating: heatingCapPts, cooling: coolingCapPts } = buildCapacityPoints(
    roomForecast, historyStart, forecastOnly,
  );

  if (heatingCapPts.length > 0) {
    datasets.push(makeDataset('Heating Capacity', heatingCapPts, 'transparent', {
      fill: 'end',
      backgroundColor: 'rgba(229,115,115,0.12)',
      borderWidth: 0,
      pointRadius: 0,
      tension: 0,
      stepped: 'before',
      order: 10,
    }));
  }
  // Only shade the cooling bound when the room has cooling capacity.
  if (coolingCapPts.length > 0) {
    datasets.push(makeDataset('Cooling Capacity', coolingCapPts, 'transparent', {
      fill: 'start',
      backgroundColor: 'rgba(229,115,115,0.12)',
      borderWidth: 0,
      pointRadius: 0,
      tension: 0,
      stepped: 'before',
      order: 10,
    }));
  }

  chart.render(datasets, { yMin, yMax, y2Min: priceMin, y2Max: priceMax });
}

function buildDisturbanceChart(
  chart, outdoorHistory, outdoorForecast, solarHistory, solarForecast,
  options = {},
) {
  const forecastOnly = options.forecastOnly === true;
  const allOutdoor = forecastOnly ? outdoorForecast : [...outdoorHistory, ...outdoorForecast];
  const solarHistoryKw = wattsToKwPoints(solarHistory);
  const solarForecastKw = wattsToKwPoints(solarForecast);
  const allSolar = forecastOnly ? solarForecastKw : [...solarHistoryKw, ...solarForecastKw];

  const { yMin: outdoorMin, yMax: outdoorMax } = computeYLimits([allOutdoor], []);
  const { yMin: solarMin, yMax: solarMax } = computeYLimits([allSolar], [0]);

  const datasets = [];
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Outdoor Temperature', outdoorHistory, '#90a4ae', { borderWidth: 2 }),
    );
  }
  datasets.push(
    makeDataset(forecastOnly ? 'Outdoor Temperature' : 'Outdoor Forecast', outdoorForecast, '#90a4ae', {
      dashed: !forecastOnly, borderWidth: 2,
    }),
  );
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Solar Gain', solarHistoryKw, '#ffd54f', {
        borderWidth: 2, yAxisID: 'y2',
        fill: true, backgroundColor: 'rgba(255,213,79,0.08)',
      }),
    );
  }
  datasets.push(
    makeDataset(forecastOnly ? 'Solar Gain' : 'Solar Gain Forecast', solarForecastKw, '#ffd54f', {
      dashed: !forecastOnly, borderWidth: 2, yAxisID: 'y2',
      ...(forecastOnly ? { fill: true, backgroundColor: 'rgba(255,213,79,0.08)' } : {}),
    }),
  );

  chart.render(datasets, { yMin: outdoorMin, yMax: outdoorMax, y2Min: solarMin, y2Max: solarMax });
}

function updateChartsFromState(room, state, connection, tempChart, powerChart, disturbChart, lastRunTs, onForecast, plotSettings) {
  // Forecast data only changes when the MPC runs; detect that via last_run_ts.
  const currentRunTs = entityAttr(state, systemEntity('mpc_performance'), 'last_run_ts');
  if (currentRunTs === lastRunTs.value) return;
  lastRunTs.value = currentRunTs;

  const forecastHours = (plotSettings && plotSettings.forecastHours) || 0;
  // Re-fetch forecast data from the backend and update the chart forecast datasets.
  connection.getForecasts(forecastHours).then((forecasts) => {
    if (!forecasts) return;
    // Re-align the experiment shading to the fresh forecast grid.
    if (onForecast) onForecast(forecasts);
    const forecastData = forecasts.rooms?.[room.slug]?.forecast || [];
    const priceForecastData = forecasts.price_forecast || [];

    const tempForecast = forecastToDataPoints(forecastData, 'temperature');
    const tempLinearised = forecastToDataPoints(forecastData, 'linearised_temperature');
    const setpointData = forecastToEnabledPoints(forecastData, 'setpoint');
    const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
    const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
    const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temp');
    const priceForecast = forecastToDataPoints(priceForecastData, 'price');

    if (tempChart._chart) {
      const ds = tempChart._chart.data.datasets;
      const now = Date.now();

      if (ds[2]) ds[2].data = tempForecast;
      if (ds[3] && tempLinearised.length > 0) ds[3].data = tempLinearised;

      const constraintUpperForecast = forecastToEnabledPoints(forecastData, 'constraint_upper');
      const constraintLowerForecast = forecastToEnabledPoints(forecastData, 'constraint_lower');

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
      if (ds[1]) ds[1].data = wattsToKwPoints(powerForecast);

      // Extend the measured-power history to "now" so there is no visual gap
      // between the last recorded point and the forecast start.
      const measuredIdx = ds.findIndex((d) => d.label === 'Measured Power');
      if (measuredIdx >= 0) {
        const powerEntity = room.entities['heating_power_measured'];
        const currentPower = entityValue(state, powerEntity);
        if (currentPower !== null) {
          const pts = ds[measuredIdx].data;
          const now = Date.now();
          if (pts.length === 0 || pts[pts.length - 1].x < now) {
            pts.push({ x: now, y: wattsToKw(currentPower) });
          }
        }
      }

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

      const historyHours = (plotSettings && plotSettings.historyHours) || 12;
      const historyWindowStart = Date.now() - historyHours * 3600 * 1000;
      updatePowerChartBounds(powerChart, forecasts.rooms?.[room.slug], historyWindowStart);
    }

    if (disturbChart._chart) {
      const ds = disturbChart._chart.data.datasets;
      if (ds[1]) ds[1].data = outdoorForecast;
      if (ds[3]) ds[3].data = wattsToKwPoints(solarForecast);
      disturbChart._chart.update('none');
    }
  });
}

export {
  buildTemperatureChart,
  buildPowerChart,
  buildDisturbanceChart,
};
