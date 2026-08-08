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

const GUARD_FLAG = '__haIndustrialPanelHashGuard';

let _nativePushState = null;
let _nativeReplaceState = null;

export function isOnPanelPath() {
  if (typeof window !== 'undefined' && window.__HA_INGRESS_BASE) return true;
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
 * Remove a panel-owned hash that leaked onto a non-panel HA route.
 * Preserves HA deep links such as #domain=heating_assistant.
 */
export function stripLeakedPanelHash() {
  if (isOnPanelPath()) return;
  if (!window.location.hash) return;
  if (!isPanelHash(window.location.hash)) return;
  const url = window.location.pathname + window.location.search;
  const replace = _nativeReplaceState || history.replaceState.bind(history);
  replace(history.state, '', url);
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
 * Remove a leaked panel hash from the URL without adding a history entry.
 * Safe to call when leaving the panel via HA sidebar navigation.
 */
export function clearPanelHash() {
  stripLeakedPanelHash();
}

/**
 * Install a document-level guard that strips panel hashes whenever HA
 * navigates away from the panel path.  HA sidebar navigation updates the
 * pathname via history.pushState/replaceState and dispatches location-changed
 * while leaving window.location.hash untouched — disconnect-time cleanup alone
 * is too early because the pathname may not have updated yet.
 */
export function installPanelHashGuard() {
  if (typeof window === 'undefined' || window[GUARD_FLAG]) return;
  window[GUARD_FLAG] = true;

  _nativePushState = history.pushState.bind(history);
  _nativeReplaceState = history.replaceState.bind(history);

  window.addEventListener('hashchange', stripLeakedPanelHash);
  window.addEventListener('popstate', stripLeakedPanelHash);
  window.addEventListener('location-changed', stripLeakedPanelHash);

  history.pushState = (...args) => {
    const result = _nativePushState(...args);
    stripLeakedPanelHash();
    return result;
  };
  history.replaceState = (...args) => {
    const result = _nativeReplaceState(...args);
    stripLeakedPanelHash();
    return result;
  };

  stripLeakedPanelHash();
}

installPanelHashGuard();
