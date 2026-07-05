/** Centralized hass.callService wrappers for heating_assistant and climate domains. */

export async function setSystemEnabled(hass, enabled) {
  return hass.callService('heating_assistant', 'set_system_enabled', { enabled });
}

export async function updateControllerTuning(hass, data) {
  return hass.callService('heating_assistant', 'update_controller_tuning', data);
}

export async function updateEstimationParams(hass, data) {
  return hass.callService('heating_assistant', 'update_estimation_params', data);
}

export async function setRoomComfortOffset(hass, roomName, comfortOffset) {
  return hass.callService('heating_assistant', 'set_room_comfort_offset', {
    room_name: roomName,
    comfort_offset: comfortOffset,
  });
}

export async function setClimateTemperature(hass, entityId, temperature) {
  return hass.callService('climate', 'set_temperature', {
    entity_id: entityId,
    temperature,
  });
}

export async function turnClimateOn(hass, entityId) {
  return hass.callService('climate', 'turn_on', { entity_id: entityId });
}

export async function turnClimateOff(hass, entityId) {
  return hass.callService('climate', 'turn_off', { entity_id: entityId });
}
