/** Dataset labels used only for shaded regions — hidden from legend and tooltip. */
export const SHADING_DATASET_LABELS = new Set([
  'Constraint Upper',
  'Constraint Lower',
  'Sensor Min',
  'Heating Capacity',
  'Cooling Capacity',
]);

/** Touch-first or narrow viewports — Chart.js tap tooltips are sticky and obscure plots. */
function isMobileChartViewport() {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(pointer: coarse)').matches
    || window.matchMedia('(max-width: 768px)').matches;
}

const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 300 },
  interaction: { mode: 'index', intersect: false },
  plugins: {
    legend: {
      display: true,
      position: 'top',
      align: 'end',
      labels: {
        color: '#9aa0a8',
        font: { size: 10, family: "system-ui, sans-serif" },
        boxWidth: 12,
        padding: 12,
        usePointStyle: true,
        pointStyle: 'line',
        filter: (item) => !item.text.startsWith('Above') && !item.text.startsWith('Below')
          && !SHADING_DATASET_LABELS.has(item.text),
      },
    },
    tooltip: {
      backgroundColor: '#22262e',
      borderColor: '#363b44',
      borderWidth: 1,
      titleColor: '#e8eaed',
      bodyColor: '#9aa0a8',
      titleFont: { size: 11 },
      bodyFont: { size: 11, family: "'JetBrains Mono', monospace" },
      padding: 10,
      displayColors: true,
      boxPadding: 4,
      filter: (item) => !item.dataset.label?.startsWith('Above') && !item.dataset.label?.startsWith('Below')
        && !SHADING_DATASET_LABELS.has(item.dataset.label),
    },
  },
  scales: {
    x: {
      type: 'time',
      grid: { color: 'rgba(54, 59, 68, 0.5)', drawBorder: false },
      ticks: { color: '#6b7280', font: { size: 10 }, maxRotation: 0 },
      border: { display: false },
    },
    y: {
      grid: { color: 'rgba(54, 59, 68, 0.3)', drawBorder: false },
      ticks: { color: '#6b7280', font: { size: 10, family: "'JetBrains Mono', monospace" } },
      border: { display: false },
    },
  },
};

let chartJsLoaded = false;
let chartJsPromise = null;

export async function loadChartJs() {
  if (chartJsLoaded) return;
  if (chartJsPromise) return chartJsPromise;

  chartJsPromise = new Promise((resolve, reject) => {
    if (window.Chart) {
      chartJsLoaded = true;
      resolve();
      return;
    }

    const script = document.createElement('script');
    script.src = '/ha-industrial-panel/vendor/chart.min.js';
    script.onload = () => {
      const adapter = document.createElement('script');
      adapter.src = '/ha-industrial-panel/vendor/chartjs-adapter-date-fns.min.js';
      adapter.onload = () => {
        chartJsLoaded = true;
        resolve();
      };
      adapter.onerror = reject;
      document.head.appendChild(adapter);
    };
    script.onerror = reject;
    document.head.appendChild(script);
  });

  return chartJsPromise;
}

export class TimeSeriesChart {
  constructor(container, config) {
    this._container = container;
    this._config = config;
    this._chart = null;
    this._canvas = null;
    this._onResize = null;
    this._onOrientationChange = null;
    // Shaded identification-experiment window(s) drawn behind the data; kept on
    // the instance so they survive dataset-only updates and chart re-renders.
    this._bands = [];
  }

  async render(datasets, dynamicLimits) {
    await loadChartJs();

    this._container.innerHTML = `
      <div class="chart-container card">
        <div class="chart-container__title">${this._config.title}</div>
        <div style="position: relative; height: ${this._config.height || 200}px; width: 100%; overflow: hidden;">
          <canvas class="chart-container__canvas"></canvas>
        </div>
      </div>
    `;

    this._canvas = this._container.querySelector('canvas');
    const ctx = this._canvas.getContext('2d');

    const options = this._buildOptions(dynamicLimits);
    this._chart = new window.Chart(ctx, {
      type: 'line',
      data: { datasets },
      options,
      plugins: [experimentBandPlugin(), nowLinePlugin()],
    });
    // Re-attach any experiment bands set before the chart was rendered.
    this._chart.$experimentBands = this._bands;

    // Force Chart.js to recalculate size after any viewport resize. Clearing the
    // canvas's inline pixel dimensions first lets the parent container report its
    // true current width rather than being constrained by the previous render size
    // (critical when shrinking — e.g. landscape back to portrait).
    this._onResize = () => {
      if (this._canvas) {
        this._canvas.style.width = '';
        this._canvas.style.height = '';
      }
      requestAnimationFrame(() => {
        if (this._chart) this._chart.resize();
      });
    };

    // Mobile browsers fire orientationchange before the viewport geometry has
    // fully settled, so the subsequent resize event may still read stale layout
    // dimensions. Handle it separately with a double RAF so both the DOM reflow
    // and the CSS recalculation complete before Chart.js measures the new size.
    this._onOrientationChange = () => {
      if (this._canvas) {
        this._canvas.style.width = '';
        this._canvas.style.height = '';
      }
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (this._chart) this._chart.resize();
        });
      });
    };

    window.addEventListener('resize', this._onResize);
    window.addEventListener('orientationchange', this._onOrientationChange);
  }

  update(datasets) {
    if (!this._chart) return;
    this._chart.data.datasets = datasets;
    this._chart.update('none');
  }

  /** Set the shaded identification-experiment window(s) to overlay on the plot.
   *  ``bands`` is an array of ``{ start, end, excitationEnd?, label? }`` with
   *  timestamps in ms.  Persisted so forecast-only updates keep the shading. */
  setExperimentBands(bands) {
    this._bands = Array.isArray(bands) ? bands : [];
    if (this._chart) {
      this._chart.$experimentBands = this._bands;
      this._chart.update('none');
    }
  }

  destroy() {
    if (this._chart) {
      this._chart.destroy();
      this._chart = null;
    }
    if (this._onResize) {
      window.removeEventListener('resize', this._onResize);
      this._onResize = null;
    }
    if (this._onOrientationChange) {
      window.removeEventListener('orientationchange', this._onOrientationChange);
      this._onOrientationChange = null;
    }
  }

  _buildOptions(dynamicLimits) {
    const opts = JSON.parse(JSON.stringify(CHART_DEFAULTS));

    if (this._config.yLabel) {
      opts.scales.y.title = {
        display: true,
        text: this._config.yLabel,
        color: '#6b7280',
        font: { size: 10 },
      };
    }

    if (this._config.y2) {
      opts.scales.y2 = {
        position: 'right',
        grid: { drawOnChartArea: false, color: 'rgba(54, 59, 68, 0.3)' },
        ticks: { color: '#6b7280', font: { size: 10, family: "'JetBrains Mono', monospace" } },
        border: { display: false },
      };
      if (this._config.y2Label) {
        opts.scales.y2.title = {
          display: true,
          text: this._config.y2Label,
          color: '#6b7280',
          font: { size: 10 },
        };
      }
    }

    if (this._config.yMin !== undefined) opts.scales.y.min = this._config.yMin;
    if (this._config.yMax !== undefined) opts.scales.y.max = this._config.yMax;

    if (dynamicLimits) {
      if (dynamicLimits.yMin !== undefined) opts.scales.y.min = dynamicLimits.yMin;
      if (dynamicLimits.yMax !== undefined) opts.scales.y.max = dynamicLimits.yMax;
      if (dynamicLimits.y2Min !== undefined && opts.scales.y2) opts.scales.y2.min = dynamicLimits.y2Min;
      if (dynamicLimits.y2Max !== undefined && opts.scales.y2) opts.scales.y2.max = dynamicLimits.y2Max;
      // Optional shared x-range so stacked plots line up on the same horizon.
      if (dynamicLimits.xMin !== undefined) opts.scales.x.min = dynamicLimits.xMin;
      if (dynamicLimits.xMax !== undefined) opts.scales.x.max = dynamicLimits.xMax;
    }

    this._applyAxisFormatting(opts);

    if (isMobileChartViewport()) {
      opts.plugins.tooltip.enabled = false;
      // Chart.js shows tap-triggered tooltips via touch/click; drop those events on mobile.
      opts.events = [];
    }

    return opts;
  }

  /** Apply optional per-axis tick and tooltip value formatters from config. */
  _applyAxisFormatting(opts) {
    if (this._config.yTickFormat) {
      opts.scales.y.ticks.callback = (value) => this._config.yTickFormat(value);
    }
    if (this._config.y2 && this._config.y2TickFormat) {
      opts.scales.y2.ticks.callback = (value) => this._config.y2TickFormat(value);
    }

    const yValueFormat = this._config.yValueFormat;
    const y2ValueFormat = this._config.y2ValueFormat;
    if (!yValueFormat && !y2ValueFormat) return;

    const existing = opts.plugins.tooltip.callbacks || {};
    opts.plugins.tooltip.callbacks = {
      ...existing,
      label: (context) => {
        const val = context.parsed.y;
        if (val == null) return context.dataset.label;
        const axisId = context.dataset.yAxisID || 'y';
        const fmt = axisId === 'y2' ? y2ValueFormat : yValueFormat;
        if (fmt) return `${context.dataset.label}: ${fmt(val)}`;
        return `${context.dataset.label}: ${val}`;
      },
    };
  }
}

/** Shade the time span of any scheduled/ongoing identification experiment so an
 *  upcoming or in-progress run is obvious on every room-level plot.  The bands
 *  are read from ``chart.$experimentBands`` and drawn as a plain translucent
 *  region behind the data series in the brand accent (teal) — just enough to
 *  indicate the experiment without edges or labels overpowering the traces. */
function experimentBandPlugin() {
  // Mirrors the CSS ``--accent`` (#00d4aa) so the overlay matches the design.
  const FILL = 'rgba(0, 212, 170, 0.10)';

  return {
    id: 'experimentBands',
    beforeDatasetsDraw(chart) {
      const bands = chart.$experimentBands;
      if (!bands || !bands.length) return;
      const xScale = chart.scales.x;
      if (!xScale) return;
      const { ctx, chartArea } = chart;
      const { top, bottom, left, right } = chartArea;

      ctx.save();
      ctx.beginPath();
      ctx.rect(left, top, right - left, bottom - top);
      ctx.clip();
      ctx.fillStyle = FILL;

      for (const band of bands) {
        if (band == null || band.start == null || band.end == null) continue;
        const xs = xScale.getPixelForValue(band.start);
        const xe = xScale.getPixelForValue(band.end);
        if (xe < left || xs > right) continue;  // entirely off-screen
        const x0 = Math.max(xs, left);
        const x1 = Math.min(xe, right);
        ctx.fillRect(x0, top, Math.max(0, x1 - x0), bottom - top);
      }

      ctx.restore();
    },
  };
}

function nowLinePlugin() {
  return {
    id: 'nowLine',
    afterDraw(chart) {
      const now = Date.now();
      const xScale = chart.scales.x;
      if (!xScale) return;
      const x = xScale.getPixelForValue(now);
      if (x < xScale.left || x > xScale.right) return;

      const ctx = chart.ctx;
      ctx.save();
      ctx.beginPath();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = 'rgba(108, 122, 137, 0.6)';
      ctx.lineWidth = 1;
      ctx.moveTo(x, chart.chartArea.top);
      ctx.lineTo(x, chart.chartArea.bottom);
      ctx.stroke();

      ctx.fillStyle = 'rgba(108, 122, 137, 0.8)';
      ctx.font = '9px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText('NOW', x, chart.chartArea.top - 4);

      ctx.restore();
    },
  };
}

export function makeDataset(label, data, color, options = {}) {
  const ds = {
    label,
    data,
    borderColor: color,
    backgroundColor: options.backgroundColor || 'transparent',
    borderWidth: options.borderWidth || 1.5,
    pointRadius: options.pointRadius ?? 0,
    pointHoverRadius: options.pointHoverRadius ?? 3,
    tension: options.stepped ? 0 : 0.2,
    fill: options.fill || false,
    borderDash: options.dashed ? [5, 5] : (options.borderDash || []),
    yAxisID: options.yAxisID || 'y',
    order: options.order,
    spanGaps: options.spanGaps ?? true,
  };

  if (options.stepped) ds.stepped = options.stepped;
  if (options.showLine === false) ds.showLine = false;
  if (options.pointBackgroundColor) ds.pointBackgroundColor = options.pointBackgroundColor;
  if (options.pointBorderColor) ds.pointBorderColor = options.pointBorderColor;
  if (options.segment) ds.segment = options.segment;

  return ds;
}

/** From multiple raw sensor histories, build min/max span points aligned to a
 *  reference timeline (typically the control measurement history). Each sensor
 *  is forward-filled so the band is one continuous region along that path. */
export function sensorHistoriesToMinMaxSpan(sensorSeries, referencePoints) {
  if (!sensorSeries || sensorSeries.length < 2) return { min: [], max: [] };

  const times = (referencePoints || [])
    .map((p) => (p && p.x != null ? p.x : p))
    .filter((t) => t != null)
    .sort((a, b) => a - b);
  if (times.length === 0) return { min: [], max: [] };

  const indices = sensorSeries.map(() => 0);
  const lastValues = sensorSeries.map(() => null);
  const min = [];
  const max = [];

  for (const t of times) {
    const values = [];
    for (let i = 0; i < sensorSeries.length; i++) {
      const series = sensorSeries[i];
      while (indices[i] < series.length && series[indices[i]].x <= t) {
        if (series[indices[i]].y != null) lastValues[i] = series[indices[i]].y;
        indices[i]++;
      }
      if (lastValues[i] != null) values.push(lastValues[i]);
    }
    if (values.length >= 2) {
      min.push({ x: t, y: Math.min(...values) });
      max.push({ x: t, y: Math.max(...values) });
    }
  }

  return { min, max };
}

export function historyToDataPoints(historyArray) {
  if (!historyArray || !Array.isArray(historyArray)) return [];
  return historyArray
    .map((entry) => {
      const val = parseFloat(entry.s !== undefined ? entry.s : entry.state);
      if (isNaN(val)) return null;
      const time = new Date(entry.lu ? entry.lu * 1000 : entry.last_updated || entry.last_changed);
      return { x: time.getTime(), y: val };
    })
    .filter(Boolean);
}

export function forecastToDataPoints(forecastArray, field) {
  if (!forecastArray || !Array.isArray(forecastArray)) return [];
  return forecastArray
    .map((entry) => {
      const val = entry[field];
      if (val === undefined || val === null) return null;
      const num = parseFloat(val);
      if (isNaN(num)) return null;
      const time = new Date(entry.time);
      return { x: time.getTime(), y: num };
    })
    .filter(Boolean);
}

/** Like historyToDataPoints but preserves unavailable/unknown entries as
 *  {x, y: null}. Use for setpoint/constraint history so that off-periods
 *  (where the sensor returns unavailable) appear as gaps rather than a
 *  bridged line. Requires spanGaps:false on the dataset. */
export function historyToEnabledPoints(historyArray) {
  if (!historyArray || !Array.isArray(historyArray)) return [];
  return historyArray.map((entry) => {
    const raw = entry.s !== undefined ? entry.s : entry.state;
    const time = new Date(entry.lu ? entry.lu * 1000 : entry.last_updated || entry.last_changed);
    const val = parseFloat(raw);
    return { x: time.getTime(), y: isNaN(val) ? null : val };
  });
}

/** Like forecastToDataPoints but preserves disabled steps as {x, y: null}.
 *  Use for setpoint/constraint datasets so that off/schedule-off periods
 *  appear as gaps rather than frost-protection values. Requires spanGaps:false
 *  on the dataset so Chart.js renders the break. */
export function forecastToEnabledPoints(forecastArray, field) {
  if (!forecastArray || !Array.isArray(forecastArray)) return [];
  return forecastArray.map((entry) => {
    const time = new Date(entry.time).getTime();
    if (entry.enabled === false) return { x: time, y: null };
    const val = entry[field];
    if (val === undefined || val === null) return { x: time, y: null };
    const num = parseFloat(val);
    return { x: time, y: isNaN(num) ? null : num };
  });
}

export async function createSparkline(canvas, datasets) {
  await loadChartJs();
  return new window.Chart(canvas.getContext('2d'), {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false, type: 'time' },
        y: { display: false },
      },
      elements: { point: { radius: 0 }, line: { tension: 0.2 } },
    },
  });
}
