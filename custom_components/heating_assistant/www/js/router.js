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

  _onHashChange() {
    this._navigate();
  }

  _navigate() {
    this._destroyCurrentPage();

    const hash = window.location.hash.slice(1) || 'overview';
    const parts = hash.split('/');
    const route = parts[0];
    const param = parts.slice(1).join('/');

    if (route === 'room' && param && this._routes.room) {
      this._currentPage = this._routes.room(param);
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
