import { renderScheduleIndex } from '../schedules/schedules-index.js?v=103';
import { renderScheduleDetail } from '../schedules/schedules-detail.js?v=103';

export function renderSchedules(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderScheduleDetail(container, slug, rooms, state, connection, hass);
  }
  return renderScheduleIndex(container, rooms, state, connection, hass);
}
