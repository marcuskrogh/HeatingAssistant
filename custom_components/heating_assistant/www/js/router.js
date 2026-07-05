import { isOnPanelPath, readPanelRoute, setPanelHash } from './panel-hash.js?v=94';

export class Router {
  constructor(container, routes) {
    this._container = container;
    this._routes = routes;
    this._currentPage = null;
    this._onHashChange = this._onHashChange.bind(this);
  }

  start() {
    window.addEventListener('hashchange', this._onHashChange);
    this._navigate();
  }

  destroy() {
    window.removeEventListener('hashchange', this._onHashChange);
    this._destroyCurrentPage();
  }

  update(state) {
    if (this._currentPage && this._currentPage.update) {
      this._currentPage.update(state);
    }
  }

  // Navigate to a hash route.  When the hash is already active, hashchange does
  // not fire — call _navigate() directly so side-menu clicks always render.
  navigateTo(hash) {
    if (!isOnPanelPath()) return;
    const normalized = hash.startsWith('#') ? hash : `#${hash}`;
    if (window.location.hash === normalized) {
      this._navigate();
    } else {
      setPanelHash(normalized);
    }
  }

  _onHashChange() {
    this._navigate();
  }

  _navigate() {
    if (!isOnPanelPath()) return;

    this._destroyCurrentPage();

    const hash = readPanelRoute();
    const parts = hash.split('/');
    const route = parts[0];
    const param = parts.slice(1).join('/');

    const handler = this._routes[route];
    if (handler) {
      this._currentPage = handler(param);
    } else if (this._routes.overview) {
      this._currentPage = this._routes.overview();
    }
  }

  _destroyCurrentPage() {
    if (this._currentPage && this._currentPage.destroy) {
      this._currentPage.destroy();
    }
    this._currentPage = null;
  }
}
