function fmtClock(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

function fmtJ(v) {
  if (!Number.isFinite(v)) return '—';
  if (v >= 1000) return v.toExponential(2);
  if (v >= 10) return v.toFixed(2);
  if (v >= 1) return v.toFixed(3);
  return v.toExponential(2);
}

function phaseLabel(phase, timedOut) {
  if (timedOut) return 'Time limit reached';
  if (phase === 'tiled_oe') return 'Getting a starting guess';
  if (phase === 'nstep_pem') return 'Fitting the model';
  return phase || 'Fitting';
}

function positiveF(v) {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function logTicks(yMin, yMax) {
  const lo = Math.floor(Math.log10(yMin) + 1e-12);
  const hi = Math.ceil(Math.log10(yMax) - 1e-12);
  const ticks = [];
  const step = hi - lo > 10 ? 2 : 1;
  for (let e = lo; e <= hi; e += step) ticks.push(10 ** e);
  return ticks;
}

function fmtTick(v) {
  const e = Math.log10(v);
  if (Math.abs(e - Math.round(e)) < 1e-9) {
    const k = Math.round(e);
    if (k === 0) return '1';
    if (k === 1) return '10';
    if (k === 2) return '100';
    return `1e${k}`;
  }
  return v.toExponential(0);
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
  const padL = 56;
  const padR = 16;
  const padT = 14;
  const padB = 28;
  const ftol = positiveF(snap.ftol) || 1e-12;
  const ys = hist.map((p) => positiveF(p.f)).filter((v) => v != null);
  const yMax = Math.max(ftol * 10, ...(ys.length ? ys : [1])) * 1.25;
  const yMin = Math.min(ftol, ...(ys.length ? ys : [ftol])) ;
  const yFloor = Math.max(yMin * 0.8, 1e-18);
  const xMin = 0;
  const xMax = Math.max(8, hist.length - 1);
  const logMin = Math.log10(yFloor);
  const logMax = Math.log10(yMax);

  const xOf = (x) => padL + ((x - xMin) / (xMax - xMin)) * (w - padL - padR);
  const yOf = (y) => {
    const clipped = Math.max(yFloor, y);
    return padT + (1 - (Math.log10(clipped) - logMin) / (logMax - logMin)) * (h - padT - padB);
  };

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  ctx.font = '10px ui-monospace, monospace';
  ctx.fillStyle = dim;
  logTicks(yFloor, yMax).forEach((v) => {
    if (v < yFloor * 0.999 || v > yMax * 1.001) return;
    if (Math.abs(Math.log10(v) - Math.log10(ftol)) < 0.35) return;
    const y = yOf(v);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(w - padR, y);
    ctx.stroke();
    ctx.fillText(fmtTick(v), 6, y + 3);
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
  ctx.textAlign = 'right';
  ctx.fillText('ftol', w - padR, Math.min(h - padB - 4, yTol - 4));
  ctx.textAlign = 'left';

  ctx.strokeStyle = jCol;
  ctx.lineWidth = 2.25;
  ctx.beginPath();
  hist.forEach((p, i) => {
    const x = xOf(i);
    const y = yOf(positiveF(p.f) || yFloor);
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
  const timedOut = remain <= 0 || snap.status === 'error';
  let timeClass = 'pe-progress__time-remain';
  if (timedOut || remain <= 30) timeClass += ' pe-progress__time-remain--last';
  else if (remain <= 60) timeClass += ' pe-progress__time-remain--warn';

  overlay.innerHTML = `
    <div class="pe-progress" role="dialog" aria-live="polite" aria-label="Parameter estimation progress">
      <div class="pe-progress__head">
        <div class="pe-progress__kicker">Parameter estimation</div>
        <p class="pe-progress__title">${phaseLabel(snap.phase, timedOut)}</p>
      </div>
      <div class="pe-progress__metrics">
        <div>
          <div class="pe-progress__metric-label">Fit error</div>
          <div class="pe-progress__metric-value pe-progress__metric-value--lead">${fmtJ(snap.f)}</div>
        </div>
        <div>
          <div class="pe-progress__metric-label">Evaluations</div>
          <div class="pe-progress__metric-value pe-progress__metric-value--lead">${snap.nfev ?? '—'}</div>
        </div>
      </div>
      <div class="pe-progress__plot-wrap">
        <div class="pe-progress__plot-label">Fit error (log) toward tolerance</div>
        <canvas class="pe-progress__plot" width="680" height="240"></canvas>
      </div>
      <div class="pe-progress__legend">
        <span><i class="pe-progress__swatch pe-progress__swatch--j"></i>fit error</span>
        <span><i class="pe-progress__swatch pe-progress__swatch--tol"></i>tolerance (ftol ${fmtJ(snap.ftol || 1e-12)})</span>
      </div>
      <div class="pe-progress__time">
        <div class="${timeClass}">${fmtClock(remain)} left</div>
        <div class="pe-progress__time-meta">
          ${fmtClock(elapsed)} elapsed · ${fmtClock(cap)} maximum
        </div>
        <div class="pe-progress__bar" aria-hidden="true">
          <div class="pe-progress__bar-fill${timedOut ? ' pe-progress__bar-fill--done' : ''}" style="width:${usedPct}%"></div>
        </div>
      </div>
      ${timedOut ? `<p class="pe-progress__timeout">${snap.message || 'Stopped at the time limit. Parameters were not applied.'}</p>` : ''}
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
