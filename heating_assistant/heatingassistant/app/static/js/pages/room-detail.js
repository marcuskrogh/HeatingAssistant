import { TimeSeriesChart, forecastToDataPoints, forecastToEnabledPoints } from '../components/time-series-chart.js?v=124';
import { createGauge, updateGauge } from '../components/gauge.js?v=127';
import { createClimateCard } from '../components/climate-card.js?v=124';
import { createCountdown, COUNTDOWN_NMPC, setCountdownComputing } from '../components/countdown.js?v=146';
import { bindKpiExpandSection } from '../components/kpi-expand.js?v=146';
import { createScheduleOverview } from '../components/schedule-overview.js?v=124';
import { getRoomScheduleData } from '../schedule-utils.js?v=124';
import { resolveRoomScheduleData, getRoomComfortOffset, patchStateComfortOffset } from '../schedules/schedules-shared.js?v=124';
import { findActiveExperiment, experimentBands } from '../experiment-utils.js?v=124';
import {
  KPI_SEVERITY,
  isRoomActive,
  roomTimeInRangePct,
  roomHeatLoss,
  heatLossGaugeMax,
  solarGainGaugeMax,
  roomModelFit,
} from '../kpi-engine.js?v=124';
import {
  energyPriceDetail,
  heatLossDetail,
  nextControlDetail,
  nextNmpcDetail,
  roomModelFitDetail,
  roomPowerDetail,
  solarGainDetail,
  timeInRangeDetail,
} from '../kpi-detail-catalog.js?v=146';
import { setPanelHash } from '../panel-hash.js?v=124';
import {
  setRoomComfortOffset,
  setRoomEnabled,
  setRoomSetpoint,
} from '../ha-services.js?v=124';
import {
  formatPower, formatPowerKw, formatPrice,
  entityValue, entityAttr, systemEntity,
  wattsToKw, wattsToKwPoints,
} from '../utils.js?v=127';
import {
  extendDatasetToNow,
  computeYLimits,
  updatePowerChartBounds,
} from '../charts/room-charts.js?v=140';
import { loadChartsData } from './room-detail-history.js?v=144';

// Fallback power-gauge span used until the room forecast supplies the actual
// heating/cooling capacity for this room.
const DEFAULT_MAX_POWER = 2000;
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

/** Time-in-range display as integer percent (KPI_SPEC §5.2). */
function formatTimeInRangePct(pct) {
  if (pct === null || pct === undefined) return '—';
  const num = parseFloat(pct);
  if (isNaN(num)) return '—';
  return `${Math.round(num)}%`;
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
  // formatters read live attributes lazily on each paint.
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
  nav.addEventListener('click', () => { setPanelHash('#overview'); });
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
    comfortOffset: getRoomComfortOffset(state, room),
    off: offVal,
    onSetpointChange: async (newSp) => {
      try {
        await setRoomSetpoint(hass, roomSlug, newSp);
      } catch (err) {
        // Service call failed; the display self-corrects on the next state update.
      }
    },
    onComfortOffsetChange: async (newOffset) => {
      patchStateComfortOffset(state, roomSlug, newOffset);
      try {
        await setRoomComfortOffset(hass, roomSlug, newOffset);
      } catch (err) {
        // Service call failed; the display self-corrects on the next state update.
      }
    },
    onPowerToggle: async (turnOff) => {
      try {
        await setRoomEnabled(hass, roomSlug, !turnOff);
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
  const kpiExpand = bindKpiExpandSection(kpiGrid);

  // KPIs use the same gauge design as the overview page (label + value +
  // severity bar). At room level these are the *current system values for this
  // room* — time in range, heater power, energy price, solar gain, heat loss,
  // and model fit — since indoor temperature / setpoint / comfort band already
  // live on the climate card above.
  const powerBounds = { min: 0, max: DEFAULT_MAX_POWER };

  const priceEntity = systemEntity('electricity_price');
  const solarEntity = room.entities['solar_gain_measured'];

  const timeInRangeGauge = createGauge({
    value: 0,
    min: 0,
    max: 100,
    label: 'TIME IN RANGE',
    format: () => formatTimeInRangePct(null),
    severity: KPI_SEVERITY.timeInRange,
  });

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

  const solarGauge = createGauge({
    value: entityValue(state, solarEntity) ?? 0,
    min: 0,
    max: solarGainGaugeMax(0),
    label: 'SOLAR GAIN',
    format: formatPower,
    severity: KPI_SEVERITY.solarGain,
  });

  const heatLossGauge = createGauge({
    value: 0,
    min: 0,
    max: heatLossGaugeMax(0),
    label: 'HEAT LOSS',
    format: formatPower,
    severity: KPI_SEVERITY.heatLoss,
  });

  const initialFit = roomModelFit(state, room);
  const modelFitGauge = createGauge({
    value: initialFit.value ?? 0,
    min: 0,
    max: 1,
    label: 'MODEL FIT',
    format: () => initialFit.label,
    severity: KPI_SEVERITY.modelFit,
  });

  kpiExpand.register(timeInRangeGauge, {
    key: 'time-in-range',
    detail: (s) => timeInRangeDetail(s, room, isRoomActive(s, roomSlug)),
  });
  kpiExpand.register(powerGauge, {
    key: 'power',
    detail: (s) => roomPowerDetail(s, room, powerBounds),
  });
  kpiExpand.register(priceGauge, {
    key: 'energy-price',
    detail: (s) => energyPriceDetail(s, priceEntity, priceBounds),
  });
  kpiExpand.register(solarGauge, {
    key: 'solar-gain',
    detail: (s) => solarGainDetail(s, room),
  });
  kpiExpand.register(heatLossGauge, {
    key: 'heat-loss',
    detail: (s) => heatLossDetail(s, room),
  });
  kpiExpand.register(modelFitGauge, {
    key: 'model-fit',
    detail: (s) => roomModelFitDetail(s, room),
  });

  const countdown = createCountdown(state, true);
  kpiExpand.register(countdown.element, {
    key: 'next-control',
    detail: nextControlDetail,
  });
  const nmpcCountdown = createCountdown(state, { ...COUNTDOWN_NMPC, small: true });
  kpiExpand.register(nmpcCountdown.element, {
    key: 'next-nmpc',
    detail: nextNmpcDetail,
  });

  function paintCountdownLoading(s) {
    setCountdownComputing(
      nmpcCountdown.element,
      Boolean(entityAttr(s, systemEntity('mpc_performance'), 'nmpc_computing')),
    );
    setCountdownComputing(
      countdown.element,
      Boolean(entityAttr(s, systemEntity('mpc_performance'), 'control_computing')),
    );
  }
  paintCountdownLoading(state);

  function paintTimeInRangeGauge(s) {
    const lower = entityValue(s, room.entities['constraint_lower']);
    const upper = entityValue(s, room.entities['constraint_upper']);
    const temp = entityValue(s, room.entities['temperature_filtered'] || room.entities['temperature_measured']);
    const hasData = lower !== null && upper !== null && temp !== null;
    const active = isRoomActive(s, roomSlug);
    const pct = roomTimeInRangePct(s, room, active);
    timeInRangeGauge.style.display = active && hasData && pct !== null ? '' : 'none';
    if (!active || !hasData || pct === null) return;

    updateGauge(timeInRangeGauge, {
      value: pct,
      min: 0,
      max: 100,
      format: () => formatTimeInRangePct(pct),
      severity: KPI_SEVERITY.timeInRange,
    });
  }

  function paintPowerGauge(value, roomOff) {
    const displayVal = roomOff ? 0 : (value ?? 0);
    updateGauge(powerGauge, {
      value: displayVal, min: powerBounds.min, max: powerBounds.max, format: formatPower,
    });
  }

  function applyPriceBounds(priceForecast) {
    const prices = (priceForecast || []).map((p) => p.y).filter((y) => y != null);
    const current = entityValue(latestState, priceEntity);
    if (current != null) prices.push(current);
    if (prices.length > 0) {
      priceBounds.min = Math.min(...prices);
      priceBounds.max = Math.max(...prices);
    }
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

  function paintSolarGauge(value) {
    const gain = value ?? 0;
    updateGauge(solarGauge, {
      value: gain, min: 0, max: solarGainGaugeMax(gain),
      format: formatPower, severity: KPI_SEVERITY.solarGain,
    });
  }

  function paintHeatLossGauge(s) {
    const loss = roomHeatLoss(s, room) ?? 0;
    updateGauge(heatLossGauge, {
      value: loss, min: 0, max: heatLossGaugeMax(loss),
      format: formatPower, severity: KPI_SEVERITY.heatLoss,
    });
  }

  function paintModelFitGauge(s) {
    const fit = roomModelFit(s, room);
    updateGauge(modelFitGauge, {
      value: fit.value ?? 0, min: 0, max: 1,
      format: () => fit.label,
      severity: fit.value !== null ? KPI_SEVERITY.modelFit : undefined,
    });
  }

  paintTimeInRangeGauge(state);
  paintPowerGauge(powerVal, offVal);
  paintPriceGauge(entityValue(state, priceEntity));
  paintSolarGauge(entityValue(state, solarEntity));
  paintHeatLossGauge(state);
  paintModelFitGauge(state);
  kpiExpand.paint(state);

  // ── Schedule overview ──────────────────────────────────────────────────────
  // Mirrors the schedules index-card design; clicking opens the editable
  // schedule detail page for this room.
  const scheduleOverview = createScheduleOverview(room, null, {
    onEdit: () => { setPanelHash(`#schedules/${roomSlug}`); },
  });
  const scheduleSection = document.createElement('div');
  scheduleSection.className = 'room-schedule-overview';
  scheduleSection.appendChild(scheduleOverview.element);
  container.appendChild(scheduleSection);

  function refreshSchedule() {
    connection.getSchedules().then((roomSchedules) => {
      scheduleOverview.update(resolveRoomScheduleData(room, roomSchedules, latestState));
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
      paintPowerGauge(entityValue(latestState, room.entities['heating_power_measured']), computeRoomOff(latestState, roomSlug));
    }
    const priceForecast = forecastToDataPoints(forecasts?.price_forecast || [], 'price');
    applyPriceBounds(priceForecast);
    paintPriceGauge(entityValue(latestState, priceEntity));
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

  // lastRunTs tracks Start epoch + NLP result + P execution; when any changes
  // we re-fetch forecasts (a slow plan can land without a new 15 min tick).
  const lastRunTs = { value: null, sensorEntities: [] };
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
    paintPowerGauge(
      entityValue(latestState, room.entities['heating_power_measured']),
      computeRoomOff(latestState, roomSlug),
    );

    // Span the price bar over the upcoming price range so the fill shows where
    // the current price sits within the forecast horizon.
    applyPriceBounds(priceForecast);
    paintPriceGauge(entityValue(latestState, priceEntity));
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
    loadChartsData(room, state, connection, tempChart, powerChart, disturbChart, lastRunTs, plotSettings, onChartsReady, mpcForecastStamp);
  });

  const countdownInterval = setInterval(() => {
    countdown.tick(latestState);
    nmpcCountdown.tick(latestState);
  }, 1000);

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
        comfortLower: cl, comfortUpper: cu,
        comfortOffset: getRoomComfortOffset(newState, room),
        off,
        experiment: activeExperiment,
      });
      paintTimeInRangeGauge(newState);
      paintPowerGauge(pv, off);
      paintPriceGauge(entityValue(newState, priceEntity));
      paintSolarGauge(entityValue(newState, solarEntity));
      paintHeatLossGauge(newState);
      paintModelFitGauge(newState);
      paintCountdownLoading(newState);
      kpiExpand.paint(newState);

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

/** Extend measured history datasets to the current time on every state update. */
function extendLiveChartHistory(room, state, tempChart, powerChart, disturbChart, sensorEntityIds = []) {
  const now = Date.now();

  if (tempChart._chart) {
    const ds = tempChart._chart.data.datasets;
    const filteredIdx = ds.findIndex((d) => d.label === 'Filtered');
    const measuredIdx = ds.findIndex((d) => d.label === 'Measured');
    if (filteredIdx >= 0) {
      extendDatasetToNow(ds[filteredIdx].data, entityValue(state, room.entities['temperature_filtered']), now);
    }
    if (measuredIdx >= 0) {
      extendDatasetToNow(ds[measuredIdx].data, entityValue(state, room.entities['temperature_measured']), now);
    }
    if (sensorEntityIds.length > 1) {
      const sensorMinIdx = ds.findIndex((d) => d.label === 'Sensor Min');
      const sensorRangeIdx = ds.findIndex((d) => d.label === 'Sensor Range');
      if (sensorMinIdx >= 0 && sensorRangeIdx >= 0) {
        const values = sensorEntityIds.map((id) => entityValue(state, id)).filter((v) => v != null);
        if (values.length >= 2) {
          extendDatasetToNow(ds[sensorMinIdx].data, Math.min(...values), now);
          extendDatasetToNow(ds[sensorRangeIdx].data, Math.max(...values), now);
        }
      }
    }
    tempChart._chart.update('none');
  }

  if (powerChart._chart) {
    const ds = powerChart._chart.data.datasets;
    const measuredIdx = ds.findIndex((d) => d.label === 'Measured Power');
    if (measuredIdx >= 0) {
      const currentPower = entityValue(state, room.entities['heating_power_measured']);
      if (currentPower !== null) {
        extendDatasetToNow(ds[measuredIdx].data, wattsToKw(currentPower), now);
      }
    }
    const priceIdx = ds.findIndex((d) => d.label === 'Price');
    if (priceIdx >= 0) {
      extendDatasetToNow(ds[priceIdx].data, entityValue(state, systemEntity('electricity_price')), now);
    }
    powerChart._chart.update('none');
  }

  if (disturbChart._chart) {
    const ds = disturbChart._chart.data.datasets;
    const outdoorIdx = ds.findIndex((d) => d.label === 'Outdoor Temperature');
    const solarIdx = ds.findIndex((d) => d.label === 'Solar Gain');
    if (outdoorIdx >= 0) {
      extendDatasetToNow(ds[outdoorIdx].data, entityValue(state, systemEntity('outdoor_temperature_measured')), now);
    }
    if (solarIdx >= 0) {
      const solarVal = entityValue(state, room.entities['solar_gain_measured']);
      if (solarVal !== null) {
        extendDatasetToNow(ds[solarIdx].data, wattsToKw(solarVal), now);
      }
    }
    disturbChart._chart.update('none');
  }
}

function updateChartsFromState(room, state, connection, tempChart, powerChart, disturbChart, lastRunTs, onForecast, plotSettings) {
  // Keep measured history lines flush with the moving NOW marker on every update.
  extendLiveChartHistory(room, state, tempChart, powerChart, disturbChart, lastRunTs.sensorEntities || []);

  // Forecast data changes on a control tick or when the slow NMPC plan lands.
  const currentRunTs = mpcForecastStamp(state);
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

      replaceChartDataset(ds, 'Forecast', tempForecast);
      if (tempLinearised.length > 0) replaceChartDataset(ds, 'Linearised', tempLinearised);

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

      const { yMin, yMax } = computeYLimits(ds.map((d) => d.data || []), []);
      if (tempChart._chart.options?.scales?.y) {
        if (yMin !== undefined) tempChart._chart.options.scales.y.min = yMin;
        if (yMax !== undefined) tempChart._chart.options.scales.y.max = yMax;
      }
      tempChart._chart.update('none');
    }

    if (powerChart._chart) {
      const ds = powerChart._chart.data.datasets;
      replaceChartDataset(ds, 'Planned Power', wattsToKwPoints(powerForecast));

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
      replaceChartDataset(ds, 'Outdoor Forecast', outdoorForecast);
      replaceChartDataset(ds, 'Solar Gain Forecast', wattsToKwPoints(solarForecast));
      disturbChart._chart.update('none');
    }
  });
}

function mpcForecastStamp(state) {
  const entity = systemEntity('mpc_performance');
  return [
    entityAttr(state, entity, 'last_nmpc_ts'),
    entityAttr(state, entity, 'nmpc_result_ts'),
    entityAttr(state, entity, 'last_control_ran_ts'),
  ].join('|');
}

function replaceChartDataset(datasets, label, data) {
  const idx = datasets.findIndex((d) => d.label === label);
  if (idx >= 0) datasets[idx].data = data;
}
