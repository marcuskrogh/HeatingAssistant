import { renderScheduleIndex } from '../schedules/schedules-index.js?v=107';
import { renderScheduleDetail } from '../schedules/schedules-detail.js?v=107';

export function renderSchedules(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderScheduleDetail(container, slug, rooms, state, connection, hass);
  }
  return renderScheduleIndex(container, rooms, state, connection, hass);
}
