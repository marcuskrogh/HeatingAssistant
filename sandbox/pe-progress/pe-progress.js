function fmtClock(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

function fmtJ(v) {
  if (!Number.isFinite(v)) return '—';
  if (v >= 10) return v.toFixed(2);
  if (v >= 1) return v.toFixed(3);
  return v.toExponential(2);
}

function fmtRel(v) {
  if (!Number.isFinite(v)) return '—';
  return v.toExponential(2);
}

function phaseLabel(phase) {
  if (phase === 'tiled_oe') return 'Tiled OE warm-start';
  if (phase === 'nstep_pem') return 'N-step PEM';
  return phase || 'Optimising';
}

function drawPlot(canvas, snap) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  const cs = getComputedStyle(canvas);
  const bg = cs.getPropertyValue('--bg-primary').trim() || '#1a1d23';
  const grid = cs.getPropertyValue('--border').trim() || '#363b44';
  const jCol = cs.getPropertyValue('--chart-temp').trim() || '#4fc3f7';
  const warn = cs.getPropertyValue('--warning').trim() || '#f5a623';
  const dim = cs.getPropertyValue('--text-dim').trim() || '#6b7280';
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  const hist = snap.f_hist || [];
  if (hist.length < 1) return;
  const padL = 52;
  const padR = 16;
  const padT = 12;
  const padB = 28;
  const xs = hist.map((_, i) => i);
  const ys = hist.map((p) => Math.max(1e-16, p.rel_step));
  const ftol = Math.max(1e-16, Number(snap.ftol) || 1e-12);
  const yMin = Math.min(ftol / 30, ...ys) * 0.7;
  const yMax = Math.max(ftol * 8, ...ys) * 1.15;
  const xMin = 0;
  const xMax = Math.max(8, xs[xs.length - 1]);

  const xOf = (x) => padL + ((x - xMin) / (xMax - xMin)) * (w - padL - padR);
  const yOf = (y) => {
    const a = Math.log10(yMin);
    const b = Math.log10(yMax);
    const t = (Math.log10(y) - a) / (b - a);
    return padT + (1 - t) * (h - padT - padB);
  };

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  const decades = [];
  const d0 = Math.floor(Math.log10(yMin));
  const d1 = Math.ceil(Math.log10(yMax));
  for (let d = d0; d <= d1; d += 1) decades.push(10 ** d);
  ctx.font = '10px ui-monospace, monospace';
  ctx.fillStyle = dim;
  decades.forEach((v) => {
    if (v < yMin || v > yMax) return;
    const y = yOf(v);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(w - padR, y);
    ctx.stroke();
    ctx.fillText(v.toExponential(0), 6, y + 3);
  });

  ctx.setLineDash([5, 4]);
  ctx.strokeStyle = warn;
  ctx.lineWidth = 1.5;
  const yTol = yOf(ftol);
  ctx.beginPath();
  ctx.moveTo(padL, yTol);
  ctx.lineTo(w - padR, yTol);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = warn;
  ctx.fillText(`ftol ${ftol.toExponential(0)}`, w - padR - 78, yTol - 6);

  ctx.strokeStyle = jCol;
  ctx.lineWidth = 2;
  ctx.beginPath();
  hist.forEach((p, i) => {
    const x = xOf(i);
    const y = yOf(Math.max(yMin, p.rel_step));
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = dim;
  ctx.fillText('evaluation', w / 2 - 28, h - 8);
}

export function renderPeProgress(overlay, snap) {
  const remain = Number(snap.remaining_s) || 0;
  const cap = Number(snap.cap_s) || 1;
  const elapsed = Number(snap.elapsed_s) || 0;
  const usedPct = Math.min(100, (elapsed / cap) * 100);
  let clockClass = 'pe-progress__remaining';
  if (remain <= 30) clockClass += ' pe-progress__remaining--last';
  else if (remain <= 60) clockClass += ' pe-progress__remaining--warn';

  overlay.innerHTML = `
    <div class="pe-progress" role="dialog" aria-live="polite" aria-label="Parameter estimation progress">
      <div class="pe-progress__head">
        <div class="pe-progress__kicker">Parameter estimation</div>
        <p class="pe-progress__title">${phaseLabel(snap.phase)}</p>
      </div>
      <div class="pe-progress__clock">
        <div class="${clockClass}">${fmtClock(remain)}</div>
        <div class="pe-progress__clock-label">Time remaining of ${fmtClock(cap)} cap</div>
        <div class="pe-progress__bar" aria-hidden="true">
          <div class="pe-progress__bar-fill" style="width:${usedPct}%"></div>
        </div>
      </div>
      <div class="pe-progress__metrics">
        <div>
          <div class="pe-progress__metric-label">Objective J</div>
          <div class="pe-progress__metric-value">${fmtJ(snap.f)}</div>
        </div>
        <div>
          <div class="pe-progress__metric-label">Relative step</div>
          <div class="pe-progress__metric-value">${fmtRel(snap.rel_step)}</div>
        </div>
        <div>
          <div class="pe-progress__metric-label">Evaluations</div>
          <div class="pe-progress__metric-value">${snap.nfev ?? '—'}</div>
        </div>
      </div>
      <div class="pe-progress__plot-wrap">
        <div class="pe-progress__plot-label">L-BFGS relative step vs ftol</div>
        <canvas class="pe-progress__plot" width="680" height="168"></canvas>
      </div>
      <div class="pe-progress__legend">
        <span><i class="pe-progress__swatch pe-progress__swatch--j"></i>relative change in J</span>
        <span><i class="pe-progress__swatch pe-progress__swatch--tol"></i>ftol (stop if below)</span>
      </div>
      <p class="pe-progress__foot">
        J is the regularised N-step path error (plus MAP). The optimiser stops when
        the relative change in J stays below ftol, or when this timer hits zero —
        then parameters are not applied.
      </p>
    </div>
  `;
  const canvas = overlay.querySelector('.pe-progress__plot');
  drawPlot(canvas, snap);
}

export function mountPeProgressHost(root) {
  const stub = document.createElement('div');
  stub.className = 'sysid-stub';
  stub.innerHTML = `
    <button class="nav-back" type="button">← PARAMETER ESTIMATION</button>
    <div class="section-header">LIVING ROOM</div>
    <div class="card-stub">
      Thermal mass, R_ext and heater scale fields stay on the page while the fit runs.
      <div class="tuning-actions__status--running">Running parameter estimation…</div>
    </div>
  `;
  const overlay = document.createElement('div');
  overlay.className = 'pe-progress-overlay';
  stub.appendChild(overlay);
  root.appendChild(stub);
  return overlay;
}
