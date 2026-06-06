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

  async getForecasts() {
    try {
      const result = await this._hass.callWS({
        type: 'heating_assistant/get_forecasts',
      });
      return result || {};
    } catch (e) {
      console.warn('Failed to fetch forecasts via WebSocket:', e);
      return {};
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
