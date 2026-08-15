export class HaConnection {
  constructor(hass) {
    this._hass = hass;
  }

  updateHass(hass) {
    this._hass = hass;
  }

  async getStates() {
    const states = this._hass.states;
    return { ...states };
  }

  async getSchedules() {
    try {
      const result = await this._hass.callWS({
        type: 'heating_assistant/get_schedules',
      });
      return result.room_schedules || {};
    } catch (e) {
      console.warn('Failed to fetch schedules via WebSocket:', e);
      return {};
    }
  }

  async getControllerConfig() {
    try {
      const msg = { type: 'heating_assistant/get_controller_config' };
      // hass.callWS is available in HA 2022.6+; fall back to the lower-level
      // sendMessagePromise for older installs.
      const result = await (typeof this._hass.callWS === 'function'
        ? this._hass.callWS(msg)
        : this._hass.connection.sendMessagePromise(msg));
      return (result && typeof result === 'object' && result.config
        && typeof result.config === 'object')
        ? result.config
        : {};
    } catch (e) {
      console.warn('[HaConnection] getControllerConfig WS failed:', e);
      return null;
    }
  }

  // Returns the dataset list, or ``null`` when the fetch fails. Callers must
  // distinguish ``null`` (transient error — keep the previous list) from ``[]``
  // (a successful response with no datasets) so a momentary WebSocket failure
  // never wipes a populated list.
  async listDatasets(roomSlug) {
    try {
      const msg = { type: 'heating_assistant/list_datasets' };
      if (roomSlug != null) msg.room_slug = roomSlug;
      const result = await this._hass.callWS(msg);
      return (result && Array.isArray(result.datasets)) ? result.datasets : null;
    } catch (e) {
      console.warn('Failed to fetch datasets via WebSocket:', e);
      return null;
    }
  }

  async getDataset(datasetId) {
    try {
      const result = await this._hass.callWS({
        type: 'heating_assistant/get_dataset',
        dataset_id: datasetId,
      });
      return (result && result.dataset) ? result.dataset : null;
    } catch (e) {
      console.warn('Failed to fetch dataset via WebSocket:', e);
      return null;
    }
  }

  async getPeCoverage(opts = {}) {
    try {
      const msg = { type: 'heating_assistant/get_pe_coverage' };
      if (opts.roomSlug != null) msg.room_slug = opts.roomSlug;
      if (opts.datasetIds && opts.datasetIds.length) msg.dataset_ids = opts.datasetIds;
      else if (opts.datasetId) msg.dataset_id = opts.datasetId;
      if (opts.windowStart != null) msg.window_start = opts.windowStart;
      if (opts.windowEnd != null) msg.window_end = opts.windowEnd;
      if (opts.horizonHours != null) msg.horizon_hours = opts.horizonHours;
      const result = await this._hass.callWS(msg);
      return result || null;
    } catch (e) {
      console.warn('Failed to fetch PE coverage via WebSocket:', e);
      return null;
    }
  }

  async getPeInputs(opts = {}) {
    try {
      const msg = { type: 'heating_assistant/get_pe_inputs' };
      if (opts.roomSlug != null) msg.room_slug = opts.roomSlug;
      if (opts.datasetId) msg.dataset_id = opts.datasetId;
      if (opts.windowStart != null) msg.window_start = opts.windowStart;
      if (opts.windowEnd != null) msg.window_end = opts.windowEnd;
      if (opts.horizonHours != null) msg.horizon_hours = opts.horizonHours;
      const result = await this._hass.callWS(msg);
      return result || null;
    } catch (e) {
      console.warn('Failed to fetch PE input series via WebSocket:', e);
      return null;
    }
  }

  // Returns the experiment list, or ``null`` when the fetch fails (so callers
  // can keep the previously-rendered list instead of clearing it).
  async listExperiments() {
    try {
      const result = await this._hass.callWS({
        type: 'heating_assistant/list_experiments',
      });
      return (result && Array.isArray(result.experiments)) ? result.experiments : null;
    } catch (e) {
      console.warn('Failed to fetch experiments via WebSocket:', e);
      return null;
    }
  }

  async getForecasts(plotForecastHours) {
    try {
      const msg = { type: 'heating_assistant/get_forecasts' };
      // A positive value requests a display horizon that may differ from the
      // controller horizon; 0 / undefined keeps the full controller horizon.
      if (plotForecastHours != null && Number(plotForecastHours) > 0) {
        msg.plot_forecast_hours = Number(plotForecastHours);
      }
      const result = await this._hass.callWS(msg);
      return result || {};
    } catch (e) {
      console.warn('Failed to fetch forecasts via WebSocket:', e);
      return {};
    }
  }

  // Run a one-off MPC solve with proposed tuning parameters (not applied).
  // Returns the same forecast payload shape as getForecasts, or null on failure.
  async previewTuningForecast(tuningParams, plotForecastHours) {
    try {
      const msg = {
        type: 'heating_assistant/preview_tuning_forecast',
        ...tuningParams,
      };
      if (plotForecastHours != null && Number(plotForecastHours) > 0) {
        msg.plot_forecast_hours = Number(plotForecastHours);
      }
      const result = await this._hass.callWS(msg);
      return result || null;
    } catch (e) {
      console.warn('Failed to preview tuning forecast via WebSocket:', e);
      return null;
    }
  }

  // Lightweight fetch of just the dashboard display settings (plot windows).
  // Returns null on failure so callers can fall back to their own defaults.
  async getUiSettings() {
    try {
      const result = await this._hass.callWS({
        type: 'heating_assistant/get_ui_settings',
      });
      return (result && result.ui_settings) ? result.ui_settings : null;
    } catch (e) {
      console.warn('Failed to fetch UI settings via WebSocket:', e);
      return null;
    }
  }

  // Full editable model configuration (rooms, heat sources, system entities,
  // display settings, enum choices) used by the Configuration page.
  async getModelConfig() {
    try {
      const result = await this._hass.callWS({
        type: 'heating_assistant/get_model_config',
      });
      return (result && typeof result === 'object') ? result : null;
    } catch (e) {
      console.warn('Failed to fetch model config via WebSocket:', e);
      return null;
    }
  }

  async getHistory(entityIds, hoursBack = 12) {
    const now = new Date();
    const start = new Date(now.getTime() - hoursBack * 3600 * 1000);
    try {
      const result = await this._hass.callWS({
        type: 'history/history_during_period',
        start_time: start.toISOString(),
        end_time: now.toISOString(),
        entity_ids: entityIds,
        minimal_response: true,
        significant_changes_only: false,
      });
      return result;
    } catch (e) {
      console.warn('History fetch failed:', e);
      return {};
    }
  }

  async getHistorySince(entityIds, start) {
    const now = new Date();
    try {
      const result = await this._hass.callWS({
        type: 'history/history_during_period',
        start_time: start.toISOString(),
        end_time: now.toISOString(),
        entity_ids: entityIds,
        minimal_response: true,
        significant_changes_only: false,
      });
      return result;
    } catch (e) {
      console.warn('History-since fetch failed:', e);
      return {};
    }
  }

  async getHistoryRange(entityIds, startDate, endDate) {
    try {
      const result = await this._hass.callWS({
        type: 'history/history_during_period',
        start_time: startDate.toISOString(),
        end_time: endDate.toISOString(),
        entity_ids: entityIds,
        minimal_response: true,
        significant_changes_only: false,
      });
      return result;
    } catch (e) {
      console.warn('History range fetch failed:', e);
      return {};
    }
  }

  async subscribe(callback) {
    const unsub = await this._hass.connection.subscribeEvents(
      callback,
      'state_changed'
    );
    return () => unsub();
  }

  getEntityState(entityId) {
    return this._hass.states[entityId] || null;
  }
}
