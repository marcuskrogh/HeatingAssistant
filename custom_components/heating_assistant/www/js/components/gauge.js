export function createGauge({ value, min, max, label, format, severity }) {
  const container = document.createElement('div');
  container.className = 'card gauge';

  const normalized = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const color = getGaugeColor(value, severity);
  const displayValue = format ? format(value) : value?.toFixed(1) ?? '—';

  container.innerHTML = `
    <span class="gauge__label">${label}</span>
    <span class="gauge__value">${displayValue}</span>
    <div class="gauge__bar-track">
      <div class="gauge__bar-fill" style="width: ${(normalized * 100).toFixed(1)}%; background: ${color};"></div>
    </div>
  `;

  container.dataset.entityKey = label;
  return container;
}

export function updateGauge(container, { value, min, max, format, severity }) {
  const normalized = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const color = getGaugeColor(value, severity);
  const displayValue = format ? format(value) : value?.toFixed(1) ?? '—';

  const fill = container.querySelector('.gauge__bar-fill');
  if (fill) {
    fill.style.width = `${(normalized * 100).toFixed(1)}%`;
    fill.style.background = color;
  }

  const valueEl = container.querySelector('.gauge__value');
  if (valueEl) valueEl.textContent = displayValue;
}

function getGaugeColor(value, severity) {
  if (!severity) return 'var(--accent)';
  if (severity.inverse) {
    if (value <= severity.good) return 'var(--accent)';
    if (value <= severity.warning) return 'var(--warning)';
    return 'var(--alarm)';
  }
  if (value >= severity.good) return 'var(--accent)';
  if (value >= severity.warning) return 'var(--warning)';
  return 'var(--alarm)';
}
