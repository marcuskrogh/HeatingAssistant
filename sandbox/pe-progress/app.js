import { CAPTURE, snapshot } from './fixture.js';
import { mountPeProgressHost, renderPeProgress } from './pe-progress.js';

class PeProgressHost extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'open' });
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/ha-industrial-panel/css/industrial.css';
    root.appendChild(css);
    const extra = document.createElement('link');
    extra.rel = 'stylesheet';
    extra.href = './pe-progress.css';
    root.appendChild(extra);

    const wrap = document.createElement('div');
    wrap.style.background = 'var(--bg-primary)';
    wrap.style.minHeight = '100%';
    root.appendChild(wrap);
    const overlay = mountPeProgressHost(wrap);

    const params = new URLSearchParams(location.search);
    const mode = params.get('mode') || 'mid';
    const live = params.get('live') === '1';
    const freezeS = Number(params.get('t') || CAPTURE[mode] || CAPTURE.mid);

    const paint = (elapsedS) => {
      renderPeProgress(overlay, snapshot({ elapsedS }));
    };

    const ready = () => {
      document.documentElement.dataset.ready = '1';
    };

    Promise.all(
      [...root.querySelectorAll('link')].map(
        (link) =>
          new Promise((resolve) => {
            link.addEventListener('load', resolve, { once: true });
            link.addEventListener('error', resolve, { once: true });
          }),
      ),
    ).then(() => {
      if (!live) {
        paint(freezeS);
        ready();
        return;
      }
      let elapsed = Number(params.get('from') || 0);
      paint(elapsed);
      ready();
      const timer = window.setInterval(() => {
        elapsed += 1;
        paint(elapsed);
        if (elapsed >= 300) window.clearInterval(timer);
      }, 50);
    });
  }
}

customElements.define('pe-progress-host', PeProgressHost);
