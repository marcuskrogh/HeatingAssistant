(() => {
  const DEFAULT_USER = {
    id: 'app-runtime',
    name: 'Heating Assistant App',
    is_admin: true,
  };

  const slugify = (value) => String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_');

  async function requestJson(path, options = {}) {
    const response = await fetch(path, {
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
      ...options,
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}: ${await response.text()}`);
    }
    return response.json();
  }

  function entityState(entityId, state, attributes = {}) {
    const now = new Date().toISOString();
    return {
      entity_id: entityId,
      state: String(state ?? 'unknown'),
      attributes: { ...attributes },
      last_changed: now,
      last_updated: now,
      context: { id: 'app-runtime', parent_id: null, user_id: null },
    };
  }

  function coerceNumber(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    return null;
  }

  function buildFallbackStates(stateSnapshot, config) {
    const states = {};
    const rooms = Array.isArray(config.rooms) ? config.rooms : [];
    const schedules = config.schedules || config.room_schedules || {};
    const controllerConfig = {
      comfort_offset: Number(config.comfort_offset ?? 2.0),
      tracking_weight: Number(config.tracking_weight ?? 1.0),
      energy_weight: Number(config.energy_weight ?? 1.0),
      energy_price_weight: Number(config.energy_price_weight ?? 1.0),
      smoothing_weight: Number(config.smoothing_weight ?? 0.05),
      soft_constraint_weight: Number(config.soft_constraint_weight ?? 10.0),
      soft_constraint_linear_weight: Number(config.soft_constraint_linear_weight ?? 0.0),
      terminal_weight: Number(config.terminal_weight ?? 1.0),
      horizon: Number(config.horizon ?? 100),
      update_interval: Number(config.update_interval ?? 900),
      room_schedules: schedules,
      room_comfort_offsets: {},
      room_enabled: {},
      room_active: {},
      system_enabled: Boolean(config.system_enabled),
    };

    states['sensor.heating_assistant_controller_config'] = entityState(
      'sensor.heating_assistant_controller_config',
      'ok',
      controllerConfig,
    );
    states['sensor.heating_assistant_system_summary'] = entityState(
      'sensor.heating_assistant_system_summary',
      0,
      {
        system_enabled: Boolean(config.system_enabled),
        control_mode: stateSnapshot.control?.mode || 'unknown',
        fallback_reason: stateSnapshot.control?.fallback_reason || null,
        comfort_index_pct: null,
        has_heat_pump: false,
      },
    );

    const roomTemps = stateSnapshot.room_temperatures || {};
    const tagValues = stateSnapshot.tag_values || {};
    for (const room of rooms) {
      const name = room.name;
      if (!name) continue;
      const slug = slugify(name);
      const setpoint = coerceNumber(tagValues[room.setpoint_tag])
        ?? coerceNumber(room.setpoint)
        ?? 21.0;
      const offset = coerceNumber(room.comfort_offset)
        ?? coerceNumber(config.comfort_offset)
        ?? 2.0;
      const temperature = coerceNumber(roomTemps[name]);
      const enabled = room.enabled !== false;
      controllerConfig.room_comfort_offsets[slug] = offset;
      controllerConfig.room_enabled[slug] = enabled;
      controllerConfig.room_active[slug] = enabled;

      states[`sensor.heating_assistant_${slug}_temperature_measured`] = entityState(
        `sensor.heating_assistant_${slug}_temperature_measured`,
        temperature ?? 'unknown',
        { room: name, unit_of_measurement: '°C' },
      );
      states[`sensor.heating_assistant_${slug}_temperature_filtered`] = entityState(
        `sensor.heating_assistant_${slug}_temperature_filtered`,
        temperature ?? 'unknown',
        { room: name, unit_of_measurement: '°C', comfort_deviation: null },
      );
      states[`sensor.heating_assistant_${slug}_setpoint`] = entityState(
        `sensor.heating_assistant_${slug}_setpoint`,
        setpoint,
        { room: name, unit_of_measurement: '°C' },
      );
      states[`sensor.heating_assistant_${slug}_constraint_lower`] = entityState(
        `sensor.heating_assistant_${slug}_constraint_lower`,
        setpoint - offset,
        { room: name, unit_of_measurement: '°C' },
      );
      states[`sensor.heating_assistant_${slug}_constraint_upper`] = entityState(
        `sensor.heating_assistant_${slug}_constraint_upper`,
        setpoint + offset,
        { room: name, unit_of_measurement: '°C' },
      );
      states[`sensor.heating_assistant_${slug}_heating_power_measured`] = entityState(
        `sensor.heating_assistant_${slug}_heating_power_measured`,
        0,
        { room: name, unit_of_measurement: 'W' },
      );
      states[`climate.heating_assistant_${slug}`] = entityState(
        `climate.heating_assistant_${slug}`,
        enabled ? 'heat' : 'off',
        {
          friendly_name: name,
          current_temperature: temperature,
          temperature: setpoint,
          hvac_modes: ['off', 'heat'],
          supported_features: 1,
        },
      );
    }
    return states;
  }

  function queryFromMessage(msg) {
    const params = new URLSearchParams();
    if (msg.room_slug != null) params.set('room_slug', msg.room_slug);
    if (msg.plot_forecast_hours != null) {
      params.set('plot_forecast_hours', String(msg.plot_forecast_hours));
    }
    const text = params.toString();
    return text ? `?${text}` : '';
  }

  class HeatingAssistantAppHassShim {
    constructor({ pollIntervalMs = 5000, statusElement = null } = {}) {
      this.states = {};
      this.user = DEFAULT_USER;
      this.locale = { language: navigator.language || 'en' };
      this.themes = { darkMode: true, theme: 'default', themes: {} };
      this.panels = {};
      this.services = {};
      this.config = {};
      this._listeners = new Set();
      this._pollIntervalMs = pollIntervalMs;
      this._pollTimer = null;
      this._statusElement = statusElement;
      this.connection = {
        sendMessagePromise: (msg) => this.callWS(msg),
        subscribeEvents: async (callback) => {
          this._listeners.add(callback);
          return () => this._listeners.delete(callback);
        },
      };
    }

    async start() {
      await this.refresh();
      this._pollTimer = window.setInterval(() => {
        this.refresh().catch((err) => this._setStatus(`API error: ${err.message}`, true));
      }, this._pollIntervalMs);
      return this;
    }

    stop() {
      if (this._pollTimer) window.clearInterval(this._pollTimer);
      this._pollTimer = null;
      this._listeners.clear();
    }

    async refresh() {
      const [stateSnapshot, config] = await Promise.all([
        requestJson('api/state'),
        requestJson('api/config'),
      ]);
      const previous = this.states;
      this.config = config;
      this.states = stateSnapshot.hass_states || buildFallbackStates(stateSnapshot, config);
      this._emitStateChanges(previous, this.states);
      this._setStatus(`API connected - ${Object.keys(this.states).length} entities`, false);
      return this.states;
    }

    async callWS(msg) {
      switch (msg?.type) {
        case 'heating_assistant/get_schedules':
          return requestJson('api/schedules');
        case 'heating_assistant/get_controller_config':
          return requestJson('api/controller_config');
        case 'heating_assistant/get_ui_settings':
          return requestJson('api/ui_settings');
        case 'heating_assistant/get_model_config':
          return requestJson('api/model_config');
        case 'heating_assistant/get_forecasts':
          return requestJson(`api/forecasts${queryFromMessage(msg)}`);
        case 'heating_assistant/preview_tuning_forecast':
          return requestJson('api/forecasts/preview', {
            method: 'POST',
            body: JSON.stringify(msg),
          });
        case 'heating_assistant/list_datasets':
          return requestJson(`api/datasets${queryFromMessage(msg)}`);
        case 'heating_assistant/get_dataset':
          return requestJson(`api/datasets/${encodeURIComponent(msg.dataset_id || '')}`);
        case 'heating_assistant/list_experiments':
          return requestJson('api/experiments');
        case 'history/history_during_period':
          return requestJson('api/history');
        default:
          console.warn('[HeatingAssistantAppHassShim] Unsupported callWS message:', msg);
          return {};
      }
    }

    async callService(domain, service, data = {}) {
      const result = await requestJson('api/services', {
        method: 'POST',
        body: JSON.stringify({ domain, service, data }),
      });
      await this.refresh();
      return result;
    }

    _emitStateChanges(previous, next) {
      for (const [entityId, newState] of Object.entries(next)) {
        if (JSON.stringify(previous[entityId]) === JSON.stringify(newState)) continue;
        const event = {
          event_type: 'state_changed',
          data: {
            entity_id: entityId,
            old_state: previous[entityId] || null,
            new_state: newState,
          },
        };
        for (const listener of this._listeners) {
          try {
            listener(event);
          } catch (err) {
            console.warn('[HeatingAssistantAppHassShim] state listener failed:', err);
          }
        }
      }
    }

    _setStatus(message, isError) {
      if (!this._statusElement) return;
      this._statusElement.textContent = message;
      this._statusElement.dataset.status = isError ? 'error' : 'ok';
    }
  }

  window.HeatingAssistantAppHassShim = HeatingAssistantAppHassShim;
})();
