/**
 * Panel hash routing helpers.
 *
 * The Heating Assistant panel uses window.location.hash for in-panel SPA
 * routing.  Home Assistant routes via pathname (e.g. /config/integrations) and
 * its integrations dashboard also reads window.location.hash for deep links.
 * These helpers ensure panel hashes are only written while the panel pathname
 * is active, and are stripped when the user navigates away via the HA sidebar.
 *
 * Ingress iframe remounts sometimes reload the base URL without a hash. Keep
 * the last in-panel route in sessionStorage so boot can restore it (SWD-296).
 */

export const PANEL_PATH = '/ha-industrial';

/** Hash prefixes owned by the panel router (not HA config deep-links). */
const PANEL_HASH_PREFIXES = [
  'overview',
  'room',
  'schedules',
  'tuning',
  'parameter-estimation',
  'system-status',
  'config',
];

const LEGACY_ROUTE_PREFIX = 'identification';

const GUARD_FLAG = '__haIndustrialPanelHashGuard';
const ROUTE_STORAGE_KEY = 'heating_assistant_panel_route_v1';

let _nativePushState = null;
let _nativeReplaceState = null;

export function isOnPanelPath() {
  if (typeof window !== 'undefined' && window.__HA_INGRESS_BASE) return true;
  return window.location.pathname.startsWith(PANEL_PATH);
}

export function isPanelHash(hash) {
  const route = (hash || '').replace(/^#/, '').split('/')[0];
  return PANEL_HASH_PREFIXES.includes(route) || route === LEGACY_ROUTE_PREFIX;
}

function migrateLegacyRoute(route) {
  const cleaned = String(route || '').replace(/^#/, '');
  if (!cleaned.startsWith(`${LEGACY_ROUTE_PREFIX}/`) && cleaned !== LEGACY_ROUTE_PREFIX) {
    return cleaned;
  }
  const migrated = cleaned.replace(new RegExp(`^${LEGACY_ROUTE_PREFIX}`), 'parameter-estimation');
  const hash = `#${migrated}`;
  if (isOnPanelPath() && window.location.hash !== hash) {
    const url = window.location.pathname + window.location.search + hash;
    const replace = _nativeReplaceState || history.replaceState.bind(history);
    replace(history.state, '', url);
  }
  return migrated;
}

export function rememberPanelRoute(route) {
  const cleaned = migrateLegacyRoute(String(route || '').replace(/^#/, ''));
  if (!cleaned || !isPanelHash(cleaned)) return;
  try {
    sessionStorage.setItem(ROUTE_STORAGE_KEY, cleaned);
  } catch (_) {
    /* private mode / blocked storage */
  }
}

function _restoreRememberedRoute() {
  try {
    const saved = sessionStorage.getItem(ROUTE_STORAGE_KEY);
    if (!saved) return null;
    const migrated = migrateLegacyRoute(saved);
    if (!isPanelHash(migrated)) return null;
    const normalized = `#${migrated}`;
    if (window.location.hash !== normalized) {
      const url = window.location.pathname + window.location.search + normalized;
      const replace = _nativeReplaceState || history.replaceState.bind(history);
      replace(history.state, '', url);
    }
    return migrated;
  } catch (_) {
    return null;
  }
}

export function readPanelRoute() {
  if (!isOnPanelPath()) return 'overview';
  const hash = window.location.hash.slice(1);
  if (hash) {
    const migrated = migrateLegacyRoute(hash);
    rememberPanelRoute(migrated);
    return migrated;
  }
  const restored = _restoreRememberedRoute();
  if (restored) return restored;
  return 'overview';
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
  rememberPanelRoute(normalized);
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
