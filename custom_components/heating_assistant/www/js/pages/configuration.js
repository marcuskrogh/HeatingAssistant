// Configuration page router — sub-pages reached via hash routes.
import { renderLanding } from '../config/config-landing.js?v=106';
import { renderDisplay } from '../config/config-display.js?v=106';
import { renderRoomList } from '../config/config-room-list.js?v=106';
import { renderRoomEditor } from '../config/config-room-editor.js?v=106';
import { renderSourceList } from '../config/config-source-list.js?v=106';
import { renderSourceEditor } from '../config/config-source-editor.js?v=106';
import { renderSystem, renderSystemParams } from '../config/config-system.js?v=106';

export function renderConfiguration(container, rooms, state, connection, hass, slug) {
  const parts = (slug || '').split('/').filter(Boolean);
  const page = parts[0] || '';

  if (page === 'display') return renderDisplay(container, connection, hass);
  if (page === 'system') return renderSystem(container, connection, hass);
  if (page === 'params') return renderSystemParams(container, connection, hass);
  if (page === 'rooms' && parts[1] != null) return renderRoomEditor(container, connection, hass, parts[1]);
  if (page === 'rooms') return renderRoomList(container, connection, hass);
  if (page === 'sources' && parts[1] != null) return renderSourceEditor(container, connection, hass, parts[1]);
  if (page === 'sources') return renderSourceList(container, connection, hass);
  return renderLanding(container);
}
