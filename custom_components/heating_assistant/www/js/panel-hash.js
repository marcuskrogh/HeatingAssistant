/**
 * Panel hash routing helpers.
 *
 * The Heating Assistant panel uses window.location.hash for in-panel SPA
 * routing.  Home Assistant routes via pathname (e.g. /config/integrations) and
 * its integrations dashboard also reads window.location.hash for deep links.
 * These helpers ensure panel hashes are only written while the panel pathname
 * is active, and are stripped when the user navigates away via the HA sidebar.
 */

export const PANEL_PATH = '/ha-industrial';

/** Hash prefixes owned by the panel router (not HA config deep-links). */
const PANEL_HASH_PREFIXES = [
  'overview',
  'room',
  'schedules',
  'tuning',
  'identification',
  'config',
];

export function isOnPanelPath() {
  return window.location.pathname.startsWith(PANEL_PATH);
}

export function isPanelHash(hash) {
  const route = (hash || '').replace(/^#/, '').split('/')[0];
  return PANEL_HASH_PREFIXES.includes(route);
}

export function readPanelRoute() {
  if (!isOnPanelPath()) return 'overview';
  return window.location.hash.slice(1) || 'overview';
}

/**
 * Set the panel hash route.  No-op when the user has navigated to another HA
 * page (e.g. Settings → Integrations).
 */
export function setPanelHash(hash) {
  if (!isOnPanelPath()) return;
  const normalized = hash.startsWith('#') ? hash : `#${hash}`;
  if (window.location.hash === normalized) return;
  window.location.hash = normalized;
}

/**
 * Remove a panel hash from the URL without adding a history entry.
 * Safe to call when leaving the panel via HA sidebar navigation.
 */
export function clearPanelHash() {
  if (!window.location.hash) return;
  const url = window.location.pathname + window.location.search;
  history.replaceState(history.state, '', url);
}
