// HA loads custom panels as classic scripts in a shared global scope.  Visiting
// another custom panel re-runs this file; top-level const would throw
// "Identifier 'BASE_PATH' has already been declared".  Scope everything in an
// IIFE and skip when the element is already registered.
(() => {
  if (customElements.get('ha-industrial-panel')) return;

  const BASE_PATH = 'ha-industrial-panel';

// Cache-bust token for all dynamically-imported submodules (js/pages/*, css).
//
// Derived from THIS file's own URL query string — the `?v=` that HA's panel
// registration appends via `js_url` in __init__.py.  Reusing the same token
// for every submodule import guarantees the entry point and its submodules
// are always loaded at the same version, so the two can never drift.
//
// Uses document.currentScript.src (available in classic scripts throughout
// synchronous top-level execution).  import.meta.url is NOT used here because
// HA loads panel JS as a classic <script>, and import.meta is module-only
// syntax that causes a parse-time SyntaxError in classic scripts regardless of
// any try/catch.
//
// Single source of truth: the `?v=` on `js_url` in __init__.py.  Bump that
// one number on every frontend change — no need to touch this file.
const PANEL_VERSION = (() => {
  try {
    const src = document.currentScript?.src ?? '';
    const v = new URLSearchParams(src.split('?')[1] ?? '').get('v');
    if (v) return v;
  } catch (e) {
    /* unexpected — fall through to hardcoded fallback */
  }
  return '114';
})();

// If a boot stalls (a hung dynamic import or WebSocket call leaves the panel on
// "INITIALIZING…" with no router), the watchdog abandons the stalled attempt and
// retries so the panel can never get permanently stuck requiring a manual page
// reload.  Generous enough that a slow first-ever asset fetch is not interrupted.
const BOOT_WATCHDOG_MS = 6000;

// Page stylesheets are linked explicitly in the shadow root.  Do NOT load them
// via @import inside industrial.css — that pattern is unreliable inside shadow
// DOM (especially mobile Safari) and leaves climate/schedule cards unstyled.
const PANEL_STYLESHEETS = [
  'css/industrial.css',
  'css/pages/tuning.css',
  'css/pages/identification.css',
  'css/pages/schedules.css',
  'css/pages/climate-card.css',
  'css/pages/configuration.css',
];

function panelStylesheetLinks(version) {
  return PANEL_STYLESHEETS
    .map((path) => `<link rel="stylesheet" href="${BASE_PATH}/${path}?v=${version}">`)
    .join('\n      ');
}

const PANEL_PATH = '/ha-industrial';
const PANEL_HASH_PREFIXES = ['overview', 'room', 'schedules', 'tuning', 'identification', 'config'];
const PANEL_HASH_GUARD_FLAG = '__haIndustrialPanelHashGuard';

let _nativeReplaceState = null;

function _isOnPanelPath() {
  if (typeof window !== 'undefined' && window.__HA_INGRESS_BASE) return true;
  return window.location.pathname.startsWith(PANEL_PATH);
}

function _isPanelHash(hash) {
  const route = (hash || '').replace(/^#/, '').split('/')[0];
  return PANEL_HASH_PREFIXES.includes(route);
}

function _readPanelRoute() {
  if (!_isOnPanelPath()) return 'overview';
  return window.location.hash.slice(1) || 'overview';
}

function _setPanelHash(hash) {
  if (!_isOnPanelPath()) return;
  const normalized = hash.startsWith('#') ? hash : `#${hash}`;
  if (window.location.hash !== normalized) {
    window.location.hash = normalized;
  }
}

function _stripLeakedPanelHash() {
  if (_isOnPanelPath()) return;
  if (!window.location.hash) return;
  if (!_isPanelHash(window.location.hash)) return;
  const url = window.location.pathname + window.location.search;
  const replace = _nativeReplaceState || history.replaceState.bind(history);
  replace(history.state, '', url);
}

// Install synchronously when the entry script loads.  ES module imports of
// panel-hash.js also install the guard, but that happens later during boot.
function _installPanelHashGuard() {
  if (window[PANEL_HASH_GUARD_FLAG]) return;
  window[PANEL_HASH_GUARD_FLAG] = true;

  _nativeReplaceState = history.replaceState.bind(history);
  const nativePushState = history.pushState.bind(history);

  window.addEventListener('hashchange', _stripLeakedPanelHash);
  window.addEventListener('popstate', _stripLeakedPanelHash);
  window.addEventListener('location-changed', _stripLeakedPanelHash);

  history.pushState = (...args) => {
    const result = nativePushState(...args);
    _stripLeakedPanelHash();
    return result;
  };
  history.replaceState = (...args) => {
    const result = _nativeReplaceState(...args);
    _stripLeakedPanelHash();
    return result;
  };

  _stripLeakedPanelHash();
}

_installPanelHashGuard();

const CONTROLLER_CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';

/** Return true when slug-keyed room schedule payloads differ. */
function roomSchedulesChanged(prevSchedules, nextSchedules) {
  const p = prevSchedules || {};
  const n = nextSchedules || {};
  const keys = new Set([...Object.keys(p), ...Object.keys(n)]);
  if (keys.size === 0) return false;
  for (const key of keys) {
    const prevPeriods = p[key]?.periods ?? [];
    const nextPeriods = n[key]?.periods ?? [];
    if (prevPeriods.length !== nextPeriods.length) return true;
    if (JSON.stringify(prevPeriods) !== JSON.stringify(nextPeriods)) return true;
    if ((p[key]?.enabled ?? true) !== (n[key]?.enabled ?? true)) return true;
  }
  return false;
}

/** Return true when slug-keyed comfort offset maps differ. */
function roomComfortOffsetsChanged(prevOffsets, nextOffsets) {
  const p = prevOffsets || {};
  const n = nextOffsets || {};
  const keys = new Set([...Object.keys(p), ...Object.keys(n)]);
  for (const key of keys) {
    if (Number(p[key]) !== Number(n[key])) return true;
  }
  return false;
}

/** Return true when controller-config attrs that drive schedules or climate UI differ. */
function controllerConfigAttrsChanged(prevAttrs, nextAttrs) {
  const pa = prevAttrs || {};
  const na = nextAttrs || {};
  if (roomSchedulesChanged(pa.room_schedules, na.room_schedules)) return true;
  if (roomComfortOffsetsChanged(pa.room_comfort_offsets, na.room_comfort_offsets)) return true;
  for (const key of ['room_active', 'room_enabled']) {
    const pv = pa[key];
    const nv = na[key];
    if (pv === nv) continue;
    if (!pv || !nv) return true;
    const slugs = new Set([...Object.keys(pv), ...Object.keys(nv)]);
    for (const slug of slugs) {
      if (pv[slug] !== nv[slug]) return true;
    }
  }
  return false;
}

/** Shallow snapshot of controller-config attrs that drive schedule and climate UI. */
function snapshotConfigAttrs(attrs) {
  const a = attrs || {};
  const snap = {};
  for (const key of ['room_active', 'room_enabled']) {
    if (a[key]) snap[key] = { ...a[key] };
  }
  if (a.room_schedules) {
    snap.room_schedules = JSON.parse(JSON.stringify(a.room_schedules));
  }
  if (a.room_comfort_offsets) {
    snap.room_comfort_offsets = { ...a.room_comfort_offsets };
  }
  return snap;
}

/** Merge room_schedules, keeping patched data only when HA payload is empty or shorter. */
function mergeRoomSchedulesPreferringPrev(prevSchedules, nextSchedules) {
  const p = prevSchedules || {};
  const n = nextSchedules || {};
  const keys = new Set([...Object.keys(p), ...Object.keys(n)]);
  const merged = { ...n };
  for (const key of keys) {
    const prevPeriods = p[key]?.periods ?? [];
    const nextPeriods = n[key]?.periods ?? [];
    if (nextPeriods.length === 0 && prevPeriods.length > 0) {
      merged[key] = p[key];
      continue;
    }
    if (
      prevPeriods.length > 0
      && nextPeriods.length > 0
      && JSON.stringify(prevPeriods) !== JSON.stringify(nextPeriods)
      && prevPeriods.length > nextPeriods.length
    ) {
      merged[key] = p[key];
    }
  }
  return merged;
}

/** Merge room_comfort_offsets, keeping patched values only when HA omits a key. */
function mergeComfortOffsetsPreferringPrev(prevOffsets, nextOffsets) {
  const p = prevOffsets || {};
  const n = nextOffsets || {};
  const keys = new Set([...Object.keys(p), ...Object.keys(n)]);
  const merged = { ...n };
  for (const key of keys) {
    if (p[key] !== undefined && n[key] === undefined) {
      merged[key] = p[key];
    }
  }
  return merged;
}

/** Keep optimistic controller-config attrs when HA pushes stale data. */
function mergeControllerConfigEntity(prevEntity, nextEntity) {
  if (!prevEntity) return nextEntity;
  const prevAttrs = prevEntity.attributes || {};
  const nextAttrs = nextEntity.attributes || {};
  const mergedSchedules = mergeRoomSchedulesPreferringPrev(
    prevAttrs.room_schedules,
    nextAttrs.room_schedules,
  );
  const mergedOffsets = mergeComfortOffsetsPreferringPrev(
    prevAttrs.room_comfort_offsets,
    nextAttrs.room_comfort_offsets,
  );
  const schedulesMatch = JSON.stringify(mergedSchedules)
    === JSON.stringify(nextAttrs.room_schedules || {});
  const offsetsMatch = JSON.stringify(mergedOffsets)
    === JSON.stringify(nextAttrs.room_comfort_offsets || {});
  if (schedulesMatch && offsetsMatch) {
    return nextEntity;
  }
  return {
    ...nextEntity,
    attributes: {
      ...nextAttrs,
      room_schedules: mergedSchedules,
      room_comfort_offsets: mergedOffsets,
    },
  };
}

/** Apply one HA sensor state into panel ``panelState``; returns new config snapshot. */
function applySensorStateToPanel(panelState, id, state, configAttrSnapshot) {
  const snapshot = id === CONTROLLER_CONFIG_ENTITY ? configAttrSnapshot : null;
  if (!sensorStateChanged(panelState[id], state, snapshot)) {
    return { changed: false, configAttrSnapshot };
  }
  if (id === CONTROLLER_CONFIG_ENTITY) {
    const merged = mergeControllerConfigEntity(panelState[id], state);
    panelState[id] = merged;
    return { changed: true, configAttrSnapshot: snapshotConfigAttrs(merged.attributes) };
  }
  panelState[id] = state;
  return { changed: true, configAttrSnapshot };
}

/** Detect HA sensor state changes, including in-place attribute mutations. */
function sensorStateChanged(prev, next, configSnapshot) {
  if (!next) return false;
  if (!prev) return true;
  if (next.entity_id === CONTROLLER_CONFIG_ENTITY) {
    if (controllerConfigAttrsChanged(configSnapshot, next.attributes)) return true;
    if (controllerConfigAttrsChanged(prev.attributes, next.attributes)) return true;
    // Synthetic patches (patchStateSchedule) detach from hass.states references.
    return false;
  }
  if (prev !== next) return true;
  // HA may mutate the same state object when only attributes change.
  if (prev.last_updated !== next.last_updated) return true;
  if (prev.state !== next.state) return true;
  if (next.entity_id === CONTROLLER_CONFIG_ENTITY) {
    return controllerConfigAttrsChanged(prev.attributes, next.attributes);
  }
  return false;
}

class HaIndustrialPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._connection = null;
    this._router = null;
    this._rooms = [];
    this._state = {};
    this._booting = false;
    // Bumped on disconnect (and by the watchdog) so in-flight async boot work is
    // ignored after teardown / supersession.
    this._bootGeneration = 0;
    // Recovery timer that re-boots if an attempt stalls before a router exists.
    this._watchdogTimer = null;
    // Deferred hash cleanup after sidebar navigation away from the panel.
    this._hashCleanupTimer = null;
    // The backend starts STOPPED after every (re)start; the user must press
    // START to engage the controller.  Reflect that default until the real
    // system_enabled attribute syncs in from the coordinator.
    this._systemRunning = false;
    // Snapshot of controller-config room on/off attrs so in-place HA mutations
    // are still detected when the cached state object is reused.
    this._configAttrSnapshot = null;
    // Stable reference so the same listener can be added/removed across
    // disconnect/reconnect cycles without accumulating duplicates.
    this._onHashChange = () => this._updateActiveNav();
  }

  // HA may batch property updates via setProperties instead of individual setters.
  setProperties(props) {
    if ('hass' in props) this.hass = props.hass;
    if ('narrow' in props) this.narrow = props.narrow;
    if ('panel' in props) this.panel = props.panel;
    if ('route' in props) this.route = props.route;
    this._ensureBooted();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._connection) {
      this._connection.updateHass(hass);
    }
    if (this._menuButton) {
      this._menuButton.hass = hass;
    }
    if (this._router) {
      this._applyHassState(hass);
    } else if (hass && this.isConnected) {
      // Fallback when hass updates after connect but boot was interrupted.
      this._ensureBooted();
    }
  }

  _applyHassState(hass) {
    let changed = false;
    for (const [id, state] of Object.entries(hass.states)) {
      if (!id.startsWith('sensor.heating_assistant_')) continue;
      const result = applySensorStateToPanel(this._state, id, state, this._configAttrSnapshot);
      if (result.changed) {
        if (result.configAttrSnapshot !== undefined) {
          this._configAttrSnapshot = result.configAttrSnapshot;
        }
        changed = true;
      }
    }
    if (changed) {
      this._router.update(this._state);
      this._syncSystemRunning();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuButton) {
      this._menuButton.narrow = narrow;
    }
  }

  set panel(panel) {
    this._panel = panel;
  }

  async _boot() {
    if (this._booting || !this._hass || !this.isConnected) return;

    this._booting = true;
    const generation = this._bootGeneration;
    this._startBootWatchdog(generation);

    if (this._router) {
      this._router.destroy();
      this._router = null;
    }

    window.removeEventListener('hashchange', this._onHashChange);
    this._renderShell();

    try {
      const [
        { HaConnection },
        { Router },
        { discoverRooms },
        { renderOverview },
        { renderRoomDetail },
        { renderSystemIdentification },
        { renderControllerTuning },
        { renderSchedules },
        { renderConfiguration },
      ] = await Promise.all([
        import(`${BASE_PATH}/js/ha-connection.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/router.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/discovery.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/overview.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/room-detail.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/system-identification.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/tuning-controller.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/schedules.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/configuration.js?v=${PANEL_VERSION}`),
      ]);

      if (generation !== this._bootGeneration || !this.isConnected) {
        this._clearBootWatchdog();
        return;
      }

      this._connection = new HaConnection(this._hass);

      // Read state from the LATEST hass (may have been updated via set hass()
      // while modules were loading) rather than the stored connection snapshot.
      const latestStates = this._hass.states;
      this._state = { ...latestStates };
      const cfg = this._state[CONTROLLER_CONFIG_ENTITY];
      this._configAttrSnapshot = cfg ? snapshotConfigAttrs(cfg.attributes) : null;
      this._rooms = discoverRooms(this._state);

      const contentEl = this.shadowRoot.getElementById('content');
      this._router = new Router(contentEl, {
        overview: () => renderOverview(contentEl, this._rooms, this._state, this._connection, this._hass),
        room: (slug) => renderRoomDetail(contentEl, slug, this._rooms, this._state, this._connection, this._hass),
        identification: (slug) => renderSystemIdentification(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
        tuning: (slug) => renderControllerTuning(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
        schedules: (slug) => renderSchedules(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
        config: (slug) => renderConfiguration(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
      });

      this._router.start();
      // A working router means boot succeeded — stand the watchdog down.
      this._clearBootWatchdog();
      this._updateActiveNav();
      this._syncSystemRunning();

      // After the router renders the initial page, sync with the very latest
      // hass.states to pick up any changes that arrived during boot.  This
      // closes the window between the state snapshot above and subscription
      // activation where state_changed events could be missed.
      this._syncLatestState();
    } catch (err) {
      if (generation !== this._bootGeneration) {
        this._clearBootWatchdog();
        return;
      }
      // Surface the error in the panel instead of leaving the user on the
      // frozen "INITIALIZING..." placeholder forever.
      const contentEl = this.shadowRoot.getElementById('content');
      if (contentEl) {
        contentEl.innerHTML = `
          <div class="loading" style="color:#e57373;">
            LOAD ERROR — ${err?.message || err}<br>
            <small style="opacity:0.6;">Check the browser console and HA logs for details.</small>
          </div>`;
      }
      console.error('[heating-assistant] Boot failed:', err);
    } finally {
      if (generation === this._bootGeneration) {
        this._booting = false;
      }
    }
  }

  _syncLatestState() {
    if (!this._hass || !this._router) return;
    let changed = false;
    for (const [id, state] of Object.entries(this._hass.states)) {
      if (!id.startsWith('sensor.heating_assistant_')) continue;
      const result = applySensorStateToPanel(this._state, id, state, this._configAttrSnapshot);
      if (result.changed) {
        if (result.configAttrSnapshot !== undefined) {
          this._configAttrSnapshot = result.configAttrSnapshot;
        }
        changed = true;
      }
    }
    if (changed) this._router.update(this._state);
  }

  _syncSystemRunning() {
    const summary = this._state['sensor.heating_assistant_system_summary'];
    if (!summary) return;
    const enabled = summary.attributes?.system_enabled;
    if (typeof enabled === 'boolean') {
      this._systemRunning = enabled;
      this._updateRunButton();
    }
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      ${panelStylesheetLinks(PANEL_VERSION)}
      <div class="shell">
        <div id="top-bar"></div>
        <nav class="panel-nav" id="panel-nav">
          <div class="panel-nav__links" id="nav-links">
            <a class="panel-nav__link" href="#overview">OVERVIEW</a>
            <a class="panel-nav__link" href="#schedules">SCHEDULES</a>
            <a class="panel-nav__link" href="#tuning">TUNING</a>
            <a class="panel-nav__link" href="#identification">SYSTEM IDENTIFICATION</a>
            <a class="panel-nav__link" href="#config">CONFIGURATION</a>
          </div>
          <div class="panel-nav__brand">
            <svg class="panel-nav__logo" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <line x1="31" y1="52" x2="71" y2="52" stroke="#00d4aa" stroke-width="2.5" stroke-linecap="round" opacity="0.32"/>
              <path d="M 31 72 C 42 72, 45 53.5, 57 52.5 C 63 52, 67 52, 71 52" fill="none" stroke="#00d4aa" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="71" cy="52" r="3.6" fill="#00d4aa"/>
              <path d="M 20 84 L 20 47 L 50 19 L 80 47 L 80 84 Z" fill="none" stroke="#00d4aa" stroke-width="5.5" stroke-linejoin="round" stroke-linecap="round"/>
            </svg>
            <span class="panel-nav__name">HEATING ASSISTANT</span>
          </div>
          <span class="panel-nav__fill"></span>
          <div class="panel-nav__controls">
            <div class="panel-nav__live-indicator" id="live-indicator">
              <span class="live-dot" id="live-dot"></span>
              <span class="live-label" id="live-label">STOPPED</span>
            </div>
            <button class="panel-nav__run-btn" id="run-btn" aria-label="Start or stop the heating assistant">
              ⏻
            </button>
          </div>
          <button class="panel-nav__toggle" id="nav-toggle" aria-label="Toggle navigation" aria-expanded="false">
            <span class="panel-nav__toggle-icon">&#9776;</span>
            <span class="panel-nav__toggle-label">PAGES</span>
          </button>
        </nav>
        <main id="content" class="content">
          <div class="loading">INITIALIZING...</div>
        </main>
      </div>
    `;

    this._buildTopBar();

    const navToggle = this.shadowRoot.getElementById('nav-toggle');
    const navLinks = this.shadowRoot.getElementById('nav-links');

    if (navToggle && navLinks) {
      navToggle.addEventListener('click', () => {
        const isOpen = navLinks.classList.toggle('panel-nav__links--open');
        navToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      });
    }

    const runBtn = this.shadowRoot.getElementById('run-btn');
    if (runBtn) {
      runBtn.addEventListener('click', () => this._toggleSystem());
    }

    this._updateRunButton();

    this.shadowRoot.querySelectorAll('.panel-nav__link').forEach((link) => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        this._navigatePanel(link.getAttribute('href'));
        // Close mobile dropdown after navigation
        if (navLinks) {
          navLinks.classList.remove('panel-nav__links--open');
          if (navToggle) navToggle.setAttribute('aria-expanded', 'false');
        }
      });
    });

    window.addEventListener('hashchange', this._onHashChange);
  }

  _updateRunButton() {
    const nav = this.shadowRoot.getElementById('panel-nav');
    const btn = this.shadowRoot.getElementById('run-btn');
    if (!btn) return;

    // The whole second bar signals system state: --live tints and animates the
    // bar when the controller is running, --stopped marks it dormant.
    if (nav) {
      nav.classList.toggle('panel-nav--live', this._systemRunning);
      nav.classList.toggle('panel-nav--stopped', !this._systemRunning);
    }

    btn.classList.toggle('panel-nav__run-btn--running', this._systemRunning);

    const dot = this.shadowRoot.getElementById('live-dot');
    const label = this.shadowRoot.getElementById('live-label');
    if (dot) {
      dot.classList.toggle('live-dot--live', this._systemRunning);
      dot.classList.toggle('live-dot--stopped', !this._systemRunning);
    }
    if (label) {
      label.textContent = this._systemRunning ? 'LIVE' : 'STOPPED';
      label.classList.toggle('live-label--live', this._systemRunning);
      label.classList.toggle('live-label--stopped', !this._systemRunning);
    }
  }

  async _toggleSystem() {
    if (!this._hass) return;
    const newEnabled = !this._systemRunning;
    this._systemRunning = newEnabled;
    this._updateRunButton();
    try {
      const { setSystemEnabled } = await import(`${BASE_PATH}/js/ha-services.js?v=${PANEL_VERSION}`);
      await setSystemEnabled(this._hass, newEnabled);
    } catch (e) {
      // Revert on failure
      this._systemRunning = !newEnabled;
      this._updateRunButton();
    }
  }

  _buildTopBar() {
    const container = this.shadowRoot.getElementById('top-bar');

    const toolbar = document.createElement('app-toolbar');

    const menuBtn = document.createElement('ha-menu-button');
    menuBtn.hass = this._hass;
    menuBtn.narrow = this._narrow ?? window.innerWidth < 870;
    this._menuButton = menuBtn;

    toolbar.appendChild(menuBtn);
    container.appendChild(toolbar);
  }

  _updateActiveNav() {
    const hash = _readPanelRoute().split('/')[0] || 'overview';
    const links = this.shadowRoot.querySelectorAll('.panel-nav__link');
    links.forEach((link) => {
      const linkRoute = link.getAttribute('href').slice(1);
      link.classList.toggle('panel-nav__link--active', linkRoute === hash);
    });
  }

  _navigatePanel(target) {
    if (!target) return;
    if (this._router) {
      this._router.navigateTo(target);
      this._updateActiveNav();
    } else {
      _setPanelHash(target);
    }
  }

  _resetStaleBoot() {
    if (!this._router && this._booting) {
      this._bootGeneration += 1;
      this._booting = false;
      this._clearBootWatchdog();
    }
  }

  _ensureBooted() {
    if (!this._hass || !this.isConnected || this._router) return;
    this._resetStaleBoot();
    if (!this._booting) {
      this._boot();
    }
  }

  _scheduleHashCleanup() {
    if (this._hashCleanupTimer) {
      clearTimeout(this._hashCleanupTimer);
    }
    // Pathname may update after disconnect; retry a few times as a backstop in
    // case pushState/location-changed hooks were not yet installed.
    const delays = [0, 50, 200];
    let step = 0;
    const run = () => {
      _stripLeakedPanelHash();
      step += 1;
      if (step < delays.length) {
        this._hashCleanupTimer = setTimeout(run, delays[step]);
      } else {
        this._hashCleanupTimer = null;
      }
    };
    this._hashCleanupTimer = setTimeout(run, delays[0]);
  }

  _startBootWatchdog(generation) {
    this._clearBootWatchdog();
    this._watchdogTimer = setTimeout(() => {
      this._watchdogTimer = null;
      // Only intervene if THIS boot attempt is still the current one, we are
      // still connected, and it never produced a router (i.e. it stalled).
      if (this.isConnected && !this._router && generation === this._bootGeneration) {
        // Invalidate the stalled attempt so its eventual (or never) resolution
        // cannot race the retry, release the guard, and boot again from scratch.
        this._bootGeneration += 1;
        this._booting = false;
        this._boot();
      }
    }, BOOT_WATCHDOG_MS);
  }

  _clearBootWatchdog() {
    if (this._watchdogTimer) {
      clearTimeout(this._watchdogTimer);
      this._watchdogTimer = null;
    }
  }

  connectedCallback() {
    // ha-panel-custom destroys and recreates this element on every sidebar
    // navigation.  Boot only once we are in the document — set hass() is
    // often called before appendChild, and HA may not call it again when the
    // hass object reference is unchanged.
    this._ensureBooted();
  }

  disconnectedCallback() {
    this._bootGeneration += 1;
    this._booting = false;
    this._clearBootWatchdog();
    if (this._hashCleanupTimer) {
      clearTimeout(this._hashCleanupTimer);
      this._hashCleanupTimer = null;
    }
    if (this._router) {
      this._router.destroy();
      this._router = null;
    }
    this._connection = null;
    this._menuButton = null;
    window.removeEventListener('hashchange', this._onHashChange);
    this._scheduleHashCleanup();
  }
}

  customElements.define('ha-industrial-panel', HaIndustrialPanel);
})();
