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

function phaseLabel(phase, timedOut) {
  if (timedOut) return 'Time limit reached';
  if (phase === 'tiled_oe') return 'Getting a starting guess';
  if (phase === 'nstep_pem') return 'Fitting the model';
  return phase || 'Fitting';
}

function niceTicks(maxV, count = 5) {
  if (!(maxV > 0)) return [0];
  const raw = maxV / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  let step = mag;
  if (norm > 5) step = 10 * mag;
  else if (norm > 2) step = 5 * mag;
  else if (norm > 1) step = 2 * mag;
  const ticks = [];
  for (let v = 0; v <= maxV + step * 0.01; v += step) ticks.push(v);
  return ticks;
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
  const padL = 44;
  const padR = 16;
  const padT = 14;
  const padB = 28;
  const ys = hist.map((p) => Math.max(0, p.f));
  const yMax = Math.max(1, ...ys) * 1.08;
  const xMin = 0;
  const xMax = Math.max(8, hist.length - 1);

  const xOf = (x) => padL + ((x - xMin) / (xMax - xMin)) * (w - padL - padR);
  const yOf = (y) => padT + (1 - y / yMax) * (h - padT - padB);

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  ctx.font = '10px ui-monospace, monospace';
  ctx.fillStyle = dim;
  niceTicks(yMax).forEach((v) => {
    if (v > yMax) return;
    const y = yOf(v);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(w - padR, y);
    ctx.stroke();
    ctx.fillText(v >= 10 ? v.toFixed(0) : v.toFixed(1), 8, y + 3);
  });

  ctx.setLineDash([5, 4]);
  ctx.strokeStyle = warn;
  ctx.lineWidth = 1.5;
  const yZero = yOf(0);
  ctx.beginPath();
  ctx.moveTo(padL, yZero);
  ctx.lineTo(w - padR, yZero);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = warn;
  ctx.fillText('0', 14, yZero - 6);

  ctx.strokeStyle = jCol;
  ctx.lineWidth = 2.25;
  ctx.beginPath();
  hist.forEach((p, i) => {
    const x = xOf(i);
    const y = yOf(Math.max(0, p.f));
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
  let clockClass = 'pe-progress__remaining';
  if (timedOut || remain <= 30) clockClass += ' pe-progress__remaining--last';
  else if (remain <= 60) clockClass += ' pe-progress__remaining--warn';

  overlay.innerHTML = `
    <div class="pe-progress" role="dialog" aria-live="polite" aria-label="Parameter estimation progress">
      <div class="pe-progress__clock">
        <div class="${clockClass}">${fmtClock(remain)}</div>
        <div class="pe-progress__clock-label">Time remaining</div>
        <div class="pe-progress__clock-cap">of ${fmtClock(cap)} maximum</div>
        <div class="pe-progress__bar" aria-hidden="true">
          <div class="pe-progress__bar-fill${timedOut ? ' pe-progress__bar-fill--done' : ''}" style="width:${usedPct}%"></div>
        </div>
      </div>
      <div class="pe-progress__head">
        <div class="pe-progress__kicker">Parameter estimation</div>
        <p class="pe-progress__title">${phaseLabel(snap.phase, timedOut)}</p>
      </div>
      <div class="pe-progress__metrics">
        <div>
          <div class="pe-progress__metric-label">Fit error</div>
          <div class="pe-progress__metric-value">${fmtJ(snap.f)}</div>
        </div>
        <div>
          <div class="pe-progress__metric-label">Elapsed</div>
          <div class="pe-progress__metric-value">${fmtClock(elapsed)}</div>
        </div>
        <div>
          <div class="pe-progress__metric-label">Evaluations</div>
          <div class="pe-progress__metric-value">${snap.nfev ?? '—'}</div>
        </div>
      </div>
      <div class="pe-progress__plot-wrap">
        <div class="pe-progress__plot-label">Fit error (toward zero)</div>
        <canvas class="pe-progress__plot" width="680" height="200"></canvas>
      </div>
      <div class="pe-progress__legend">
        <span><i class="pe-progress__swatch pe-progress__swatch--j"></i>fit error</span>
        <span><i class="pe-progress__swatch pe-progress__swatch--tol"></i>target (zero)</span>
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
