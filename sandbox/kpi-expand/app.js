import { createGauge } from '/ha-industrial-panel/js/components/gauge.js';
import { createCountdown, COUNTDOWN_NMPC, setCountdownComputing } from '/ha-industrial-panel/js/components/countdown.js';
import { bindKpiExpandSection } from './kpi-expand.js';
import { nextControlDetail, nextNmpcDetail } from '/ha-industrial-panel/js/kpi-detail-catalog.js';
import {
  nmpcLoadDetail,
  nmpcLoadPercent,
  overallHealthDetail,
  regulatorLoadDetail,
  regulatorLoadPercent,
} from './load-catalog.js';

const NOW_S = Date.now() / 1000;

function fixtureState({ nmpc = false } = {}) {
  return {
    'sensor.heating_assistant_system_summary': {
      state: '4200',
      attributes: {
        system_enabled: true,
        system_quality: 'healthy',
        mqtt_connected: true,
        issue_summary: '',
        has_heat_pump: true,
        effective_system_cop: 3.1,
      },
    },
    'sensor.heating_assistant_mpc_performance': {
      state: '0.18',
      attributes: {
        dt_s: 900,
        nmpc_period_s: 7200,
        last_nmpc_ts: NOW_S - 450,
        nmpc_computing: nmpc,
        control_computing: false,
        last_nmpc_duration_s: 24.7,
        nmpc_result_ts: NOW_S - 400,
        last_control_ran_ts: NOW_S - 80,
        mean_tracking_error: 0.12,
      },
    },
  };
}

class HaKpiHost extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'open' });
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/ha-industrial-panel/css/industrial.css';
    root.appendChild(css);
    const overlay = document.createElement('link');
    overlay.rel = 'stylesheet';
    overlay.href = './expand.css';
    root.appendChild(overlay);

    const wrap = document.createElement('div');
    wrap.style.background = 'var(--bg-primary)';
    wrap.style.padding = '16px';
    wrap.style.borderRadius = '8px';

    const overviewLabel = document.createElement('div');
    overviewLabel.className = 'section-header';
    overviewLabel.textContent = 'OVERVIEW · SYSTEM STATUS';
    wrap.appendChild(overviewLabel);
    const systemGrid = document.createElement('div');
    systemGrid.className = 'grid-kpi';
    wrap.appendChild(systemGrid);

    const roomLabel = document.createElement('div');
    roomLabel.className = 'section-header';
    roomLabel.textContent = 'ROOM VIEW · ROOM KPIS';
    wrap.appendChild(roomLabel);
    const roomGrid = document.createElement('div');
    roomGrid.className = 'grid-kpi';
    wrap.appendChild(roomGrid);
    root.appendChild(wrap);

    const params = new URLSearchParams(location.search);
    const mode = params.get('mode') || 'collapsed';
    const state = fixtureState({ nmpc: mode === 'computing' });

    const systemExpand = bindKpiExpandSection(systemGrid);
    const health = createGauge({
      value: 100, min: 0, max: 100, label: 'OVERALL HEALTH', format: () => 'HEALTHY',
    });
    systemExpand.register(health, { key: 'overall-health', detail: overallHealthDetail });
    const nmpcLoad = nmpcLoadPercent(state) ?? 0;
    const nmpcGauge = createGauge({
      value: nmpcLoad,
      min: 0,
      max: 100,
      label: 'NMPC LOAD',
      format: (v) => `${v.toFixed(0)}%`,
      severity: { good: 25, warning: 50, inverse: true },
    });
    systemExpand.register(nmpcGauge, { key: 'nmpc-load', detail: nmpcLoadDetail });

    const roomExpand = bindKpiExpandSection(roomGrid);
    const regulator = regulatorLoadPercent(state) ?? 0;
    const regulatorGauge = createGauge({
      value: regulator,
      min: 0,
      max: 100,
      label: 'REGULATOR LOAD',
      format: (v) => `${v.toFixed(0)}%`,
      severity: { good: 25, warning: 50, inverse: true },
    });
    roomExpand.register(regulatorGauge, { key: 'regulator-load', detail: regulatorLoadDetail });
    const range = createGauge({
      value: 96, min: 0, max: 100, label: 'TIME IN RANGE', format: (v) => `${v.toFixed(0)}%`,
    });
    roomExpand.register(range, {
      key: 'time-in-range',
      detail: () => ({
        description: 'Share of the last 24 hours this room stayed inside its comfort band.',
        sections: [{
          title: 'Comfort',
          rows: [
            { label: 'Time in range', value: '96%' },
            { label: 'Band', value: '20.0–22.0°C' },
          ],
        }],
      }),
    });
    const power = createGauge({
      value: 1.2, min: 0, max: 4, label: 'HEATING POWER', format: (v) => `${v.toFixed(1)} kW`,
    });
    roomExpand.register(power, {
      key: 'heating-power',
      detail: () => ({
        description: 'Measured heater power for this room.',
        sections: [{
          title: 'Power',
          rows: [
            { label: 'Live', value: '1.2 kW' },
            { label: 'Gauge max', value: '4.0 kW' },
          ],
        }],
      }),
    });
    const control = createCountdown(state, false);
    roomExpand.register(control.element, { key: 'next-control', detail: nextControlDetail });
    const nmpc = createCountdown(state, { ...COUNTDOWN_NMPC, small: false });
    roomExpand.register(nmpc.element, { key: 'next-nmpc', detail: nextNmpcDetail });
    for (let i = 1; i <= 4; i += 1) {
      const filler = createGauge({
        value: 20 + i * 5,
        min: 0,
        max: 100,
        label: `SPARE ${i}`,
        format: (v) => `${v.toFixed(0)}%`,
      });
      roomExpand.register(filler, {
        key: `spare-${i}`,
        detail: () => ({
          description: 'Filler card so a click below the fold must follow the card to the top.',
          sections: [{ title: 'Spare', rows: [{ label: 'Index', value: String(i) }] }],
        }),
      });
    }

    systemExpand.paint(state);
    roomExpand.paint(state);
    setCountdownComputing(nmpc.element, Boolean(state['sensor.heating_assistant_mpc_performance'].attributes.nmpc_computing));
    if (mode === 'nmpc') systemExpand.open('nmpc-load');
    if (mode === 'regulator') roomExpand.open('regulator-load');
    if (mode === 'computing') roomExpand.open('next-nmpc');

    const lead = document.getElementById('lead');
    if (lead && !lead.dataset.filled) {
      lead.dataset.filled = '1';
      lead.append(` Capture mode: ${mode}.`);
    }

    Promise.all(
      [...root.querySelectorAll('link')].map(
        (link) =>
          new Promise((resolve) => {
            link.addEventListener('load', resolve, { once: true });
            link.addEventListener('error', resolve, { once: true });
          }),
      ),
    ).then(() => {
      document.documentElement.dataset.ready = '1';
    });
  }
}

customElements.define('ha-kpi-host', HaKpiHost);
