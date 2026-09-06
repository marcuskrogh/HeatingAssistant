import {
  CHART_DASH_PATTERN,
  CHART_DASH_WIDTH,
  CHART_LINE_WIDTH,
  CHART_TICK_SIZE,
  readTheme,
  sizePlotCanvas,
} from '../components/chart-theme.js?v=155';

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

export function liveClock(snap, nowS = Date.now() / 1000) {
  const cap = Number(snap.cap_s) || 0;
  const started = Number(snap.started_at);
  let elapsed = Number(snap.elapsed_s) || 0;
  if (cap > 0 && Number.isFinite(started) && started > 1e9) {
    elapsed = Math.max(0, nowS - started);
  }
  const remaining = cap > 0 ? Math.max(0, cap - elapsed) : 0;
  return { cap, elapsed, remaining };
}

function drawPlot(canvas, snap) {
  const sized = sizePlotCanvas(canvas);
  if (sized.skipped) return false;
  const { ctx, cssW: w, cssH: h } = sized;
  const theme = readTheme(canvas);
  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, w, h);

  const hist = snap.f_hist || [];
  if (hist.length < 1) return true;
  const padL = 44;
  const padR = 16;
  const padT = 14;
  const padB = 28;
  const ys = hist.map((p) => Math.max(0, Number(p.f) || 0));
  const yMax = Math.max(1, ...ys) * 1.08;
  const xMin = 0;
  const xMax = Math.max(8, hist.length - 1);

  const xOf = (x) => padL + ((x - xMin) / (xMax - xMin)) * (w - padL - padR);
  const yOf = (y) => padT + (1 - y / yMax) * (h - padT - padB);

  ctx.strokeStyle = theme.grid;
  ctx.lineWidth = 1;
  ctx.font = `${CHART_TICK_SIZE}px ${theme.fontMono}`;
  ctx.fillStyle = theme.tick;
  niceTicks(yMax).forEach((v) => {
    if (v > yMax) return;
    const y = yOf(v);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(w - padR, y);
    ctx.stroke();
    ctx.fillText(v >= 10 ? v.toFixed(0) : v.toFixed(1), 8, y + 3);
  });

  ctx.setLineDash(CHART_DASH_PATTERN);
  ctx.strokeStyle = theme.warn;
  ctx.lineWidth = CHART_DASH_WIDTH;
  const yZero = yOf(0);
  ctx.beginPath();
  ctx.moveTo(padL, yZero);
  ctx.lineTo(w - padR, yZero);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = theme.warn;
  ctx.fillText('0', 14, yZero - 6);

  ctx.strokeStyle = theme.series;
  ctx.lineWidth = CHART_LINE_WIDTH;
  ctx.beginPath();
  hist.forEach((p, i) => {
    const x = xOf(i);
    const y = yOf(Math.max(0, Number(p.f) || 0));
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = theme.tick;
  ctx.font = `${CHART_TICK_SIZE}px ${theme.fontSans}`;
  ctx.fillText('evaluation', w / 2 - 28, h - 8);
  return true;
}

export function renderPeProgress(overlay, snap) {
  const clock = liveClock(snap);
  const remain = clock.remaining;
  const cap = clock.cap || 1;
  const elapsed = clock.elapsed;
  const usedPct = Math.min(100, (elapsed / cap) * 100);
  const timedOut = snap.status === 'error' || (clock.cap > 0 && remain <= 0);
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
        <div class="pe-progress__plot-frame">
          <canvas class="pe-progress__plot"></canvas>
        </div>
      </div>
      <div class="pe-progress__legend">
        <span><i class="pe-progress__swatch pe-progress__swatch--j"></i>Fit error</span>
        <span><i class="pe-progress__swatch pe-progress__swatch--tol"></i>Target (zero)</span>
      </div>
      ${timedOut ? `<p class="pe-progress__timeout">${snap.message || 'Stopped at the time limit. Parameters were not applied.'}</p>` : ''}
    </div>
  `;
  const canvas = overlay.querySelector('.pe-progress__plot');
  const paint = () => {
    if (!drawPlot(canvas, snap)) requestAnimationFrame(paint);
  };
  paint();
}
