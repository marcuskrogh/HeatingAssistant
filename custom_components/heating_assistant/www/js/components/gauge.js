export function createGauge({ value, min, max, label, format, severity }) {
  const container = document.createElement('div');
  container.className = 'card gauge';

  const normalized = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const color = getGaugeColor(value, severity);
  const arcLength = 180;
  const dashOffset = arcLength - normalized * arcLength;
  const displayValue = format ? format(value) : value?.toFixed(1) ?? '—';

  container.innerHTML = `
    <svg class="gauge__svg" viewBox="0 0 120 70">
      <path class="gauge__track"
        d="M 10 65 A 50 50 0 0 1 110 65" />
      <path class="gauge__fill"
        d="M 10 65 A 50 50 0 0 1 110 65"
        style="stroke: ${color}; stroke-dasharray: ${arcLength}; stroke-dashoffset: ${dashOffset};" />
    </svg>
    <span class="gauge__value">${displayValue}</span>
    <span class="gauge__label">${label}</span>
  `;

  container.dataset.entityKey = label;
  return container;
}

export function updateGauge(container, { value, min, max, format, severity }) {
  const normalized = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const color = getGaugeColor(value, severity);
  const arcLength = 180;
  const dashOffset = arcLength - normalized * arcLength;
  const displayValue = format ? format(value) : value?.toFixed(1) ?? '—';

  const fill = container.querySelector('.gauge__fill');
  if (fill) {
    fill.style.strokeDashoffset = dashOffset;
    fill.style.stroke = color;
  }

  const valueEl = container.querySelector('.gauge__value');
  if (valueEl) valueEl.textContent = displayValue;
}

function getGaugeColor(value, severity) {
  if (!severity) return 'var(--accent)';
  if (severity.inverse) {
    if (value <= severity.good) return 'var(--good)';
    if (value <= severity.warning) return 'var(--warning)';
    return 'var(--alarm)';
  }
  if (value >= severity.good) return 'var(--good)';
  if (value >= severity.warning) return 'var(--warning)';
  return 'var(--alarm)';
}
