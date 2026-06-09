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
        filter: (item) => !item.text.startsWith('Above') && !item.text.startsWith('Below'),
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
      filter: (item) => !item.dataset.label?.startsWith('Above') && !item.dataset.label?.startsWith('Below'),
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
      plugins: [nowLinePlugin()],
    });

    // Force Chart.js to recalculate size after any viewport resize (e.g. mobile
    // orientation change back to portrait, where the internal ResizeObserver
    // can get stuck because the canvas holds the old wider dimension).
    this._onResize = () => {
      requestAnimationFrame(() => {
        if (this._chart) this._chart.resize();
      });
    };
    window.addEventListener('resize', this._onResize);
  }

  update(datasets) {
    if (!this._chart) return;
    this._chart.data.datasets = datasets;
    this._chart.update('none');
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

    return opts;
  }
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

/** Like historyToDataPoints but produces gaps for unavailable/unknown entries.
 *  Use for setpoint/constraint history so that off-periods appear as gaps
 *  rather than a bridged line. Requires spanGaps:false on the dataset.
 *
 *  With stepped:'before' and spanGaps:false, Chart.js only draws a segment
 *  between consecutive point pairs inside the same run. A single isolated
 *  point draws nothing. To ensure the pre-gap segment is visible, a cap
 *  point carrying the last valid y is inserted at the transition timestamp,
 *  then a null 1 ms later opens the gap. */
export function historyToEnabledPoints(historyArray) {
  if (!historyArray || !Array.isArray(historyArray)) return [];
  const result = [];
  let lastValidY = null;
  for (const entry of historyArray) {
    const raw = entry.s !== undefined ? entry.s : entry.state;
    const time = new Date(entry.lu ? entry.lu * 1000 : entry.last_updated || entry.last_changed).getTime();
    const val = parseFloat(raw);
    if (isNaN(val)) {
      if (lastValidY !== null) result.push({ x: time, y: lastValidY });
      result.push({ x: time + 1, y: null });
      lastValidY = null;
    } else {
      result.push({ x: time, y: val });
      lastValidY = val;
    }
  }
  return result;
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
