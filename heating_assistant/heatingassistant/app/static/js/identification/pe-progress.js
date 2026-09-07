import { ETA_NOISE, ETA_TOL, pointEta, rmseCFromEta } from './pe-eta.js?v=154';
import {
  CHART_DASH_PATTERN,
  CHART_DASH_WIDTH,
  CHART_LINE_WIDTH,
  CHART_TICK_SIZE,
  readTheme,
  sizePlotCanvas,
} from '../components/chart-theme.js?v=157';

function fmtClock(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

function fmtRmse(v) {
  if (!Number.isFinite(v)) return '—';
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

function fmtEta(v) {
  if (!Number.isFinite(v)) return '—';
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

function phaseLabel(snap) {
  const status = snap.status || 'running';
  if (status === 'cancelled') return 'Stopped';
  if (status === 'success') return 'Finished';
  if (status === 'error') {
    if (snap.timed_out) return 'Time limit reached';
    return 'Failed';
  }
  if (snap.phase === 'tiled_oe') return 'Getting a starting guess';
  if (snap.phase === 'nstep_pem') return 'Fitting the model';
  return snap.phase || 'Fitting';
}

function positiveF(v) {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function logTicks(yMin, yMax) {
  const lo = Math.floor(Math.log10(yMin) + 1e-12);
  const hi = Math.ceil(Math.log10(yMax) - 1e-12);
  const ticks = [];
  const step = hi - lo > 8 ? 2 : 1;
  for (let e = lo; e <= hi; e += step) ticks.push(10 ** e);
  return ticks;
}

function fmtTick(v) {
  const e = Math.log10(v);
  if (Math.abs(e - Math.round(e)) < 1e-9) {
    const k = Math.round(e);
    if (k === 0) return '1';
    if (k === 1) return '10';
    if (k === -1) return '0.1';
    return `1e${k}`;
  }
  if (v >= 1 && v < 10) return v.toFixed(0);
  return v.toPrecision(2);
}

export function liveClock(snap, nowS = Date.now() / 1000) {
  const cap = Number(snap.cap_s) || 0;
  const started = Number(snap.started_at);
  const finished = Number(snap.finished_at);
  const running = (snap.status || 'running') === 'running';
  let elapsed = Number(snap.elapsed_s) || 0;
  const end = Number.isFinite(finished) && finished > 1e9 ? finished : nowS;
  if (cap > 0 && Number.isFinite(started) && started > 1e9) {
    elapsed = Math.max(0, end - started);
  }
  const remaining = running && cap > 0 ? Math.max(0, cap - elapsed) : 0;
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
  const padR = 18;
  const padT = 14;
  const padB = 28;
  const etaTol = positiveF(snap.eta_tol) || ETA_TOL;
  const etaNoise = positiveF(snap.eta_noise) || ETA_NOISE;
  const ys = hist.map((p) => pointEta(p)).filter((v) => v != null && v > 0);
  const yMax = Math.max(etaTol * 1.6, ...(ys.length ? ys : [etaTol])) * 1.15;
  const yMin = Math.min(etaNoise * 0.45, ...(ys.length ? ys : [etaNoise]));
  const yFloor = Math.max(yMin * 0.85, 0.08);
  const xMin = 0;
  const xMax = Math.max(8, hist.length - 1);
  const logMin = Math.log10(yFloor);
  const logMax = Math.log10(yMax);

  const xOf = (x) => padL + ((x - xMin) / (xMax - xMin)) * (w - padL - padR);
  const yOf = (y) => {
    const clipped = Math.max(yFloor, y);
    return padT + (1 - (Math.log10(clipped) - logMin) / (logMax - logMin)) * (h - padT - padB);
  };

  const yGood = yOf(etaTol);
  const yBottom = padT + (h - padT - padB);
  ctx.fillStyle = 'rgba(46, 196, 182, 0.10)';
  ctx.fillRect(padL, yGood, w - padL - padR, Math.max(0, yBottom - yGood));

  ctx.strokeStyle = theme.grid;
  ctx.lineWidth = 1;
  ctx.font = `${CHART_TICK_SIZE}px ${theme.fontMono}`;
  ctx.fillStyle = theme.tick;
  logTicks(yFloor, yMax).forEach((v) => {
    if (v < yFloor * 0.999 || v > yMax * 1.001) return;
    if (Math.abs(Math.log10(v) - Math.log10(etaTol)) < 0.22) return;
    const y = yOf(v);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(w - padR, y);
    ctx.stroke();
    ctx.fillText(fmtTick(v), 8, y + 3);
  });

  ctx.setLineDash(CHART_DASH_PATTERN);
  ctx.strokeStyle = theme.accent;
  ctx.lineWidth = CHART_DASH_WIDTH;
  const yOne = yOf(etaNoise);
  ctx.beginPath();
  ctx.moveTo(padL, yOne);
  ctx.lineTo(w - padR, yOne);
  ctx.stroke();

  ctx.setLineDash(CHART_DASH_PATTERN);
  ctx.strokeStyle = theme.warn;
  ctx.lineWidth = CHART_DASH_WIDTH;
  ctx.beginPath();
  ctx.moveTo(padL, yGood);
  ctx.lineTo(w - padR, yGood);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = theme.warn;
  ctx.textAlign = 'right';
  ctx.fillText('tol', w - 4, yGood - 4);
  ctx.textAlign = 'left';

  ctx.strokeStyle = theme.series;
  ctx.lineWidth = CHART_LINE_WIDTH;
  ctx.beginPath();
  hist.forEach((p, i) => {
    const x = xOf(i);
    const y = yOf(pointEta(p) || yFloor);
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
  const status = snap.status || 'running';
  const running = status === 'running';
  const timedOut = Boolean(snap.timed_out);
  let timeClass = 'pe-progress__time-remain';
  if (!running || timedOut || remain <= 30) timeClass += ' pe-progress__time-remain--last';
  else if (remain <= 60) timeClass += ' pe-progress__time-remain--warn';

  const eta = Number.isFinite(Number(snap.eta))
    ? Number(snap.eta)
    : pointEta({ f: snap.f, data_mse: snap.data_mse, n_obs: snap.n_obs, eta: snap.eta });
  const rmse = Number.isFinite(Number(snap.rmse_c))
    ? Number(snap.rmse_c)
    : rmseCFromEta(eta);
  const etaTol = Number(snap.eta_tol) || ETA_TOL;
  const within = Number.isFinite(eta) && eta <= etaTol;
  const rmseClass = within
    ? 'pe-progress__metric-value pe-progress__metric-value--lead pe-progress__metric-value--ok'
    : 'pe-progress__metric-value pe-progress__metric-value--lead';

  const exitLine = snap.exit_label
    || (!running ? (snap.message || '') : '');
  const remainText = running ? `${fmtClock(remain)} left` : 'Done';

  overlay.innerHTML = `
    <div class="pe-progress" role="dialog" aria-live="polite" aria-label="Parameter estimation progress">
      <button type="button" class="pe-progress__close" data-pe-close aria-label="Close">×</button>
      <div class="pe-progress__head">
        <div class="pe-progress__kicker">Parameter estimation</div>
        <p class="pe-progress__title">${phaseLabel(snap)}</p>
      </div>
      <div class="pe-progress__metrics">
        <div>
          <div class="pe-progress__metric-label">RMS error</div>
          <div class="${rmseClass}">${fmtRmse(rmse)} °C</div>
          <div class="pe-progress__metric-sub">${fmtEta(eta)}× noise${within ? ' · within tolerance' : ' · above tolerance'}</div>
        </div>
        <div>
          <div class="pe-progress__metric-label">Evaluations</div>
          <div class="pe-progress__metric-value pe-progress__metric-value--lead">${snap.nfev ?? '—'}</div>
        </div>
      </div>
      <div class="pe-progress__plot-wrap">
        <div class="pe-progress__plot-label">Normalised RMS (1 = sensor noise)</div>
        <div class="pe-progress__plot-frame">
          <canvas class="pe-progress__plot"></canvas>
        </div>
      </div>
      <div class="pe-progress__legend">
        <span><i class="pe-progress__swatch pe-progress__swatch--j"></i>normalised RMS</span>
        <span><i class="pe-progress__swatch pe-progress__swatch--tol"></i>reasonable fit (η ≤ ${fmtEta(etaTol)} ≈ 1 °C)</span>
      </div>
      <div class="pe-progress__time">
        <div class="${timeClass}">${remainText}</div>
        <div class="pe-progress__time-meta">
          ${fmtClock(elapsed)} elapsed · ${fmtClock(cap)} maximum
        </div>
        <div class="pe-progress__bar" aria-hidden="true">
          <div class="pe-progress__bar-fill${!running || timedOut ? ' pe-progress__bar-fill--done' : ''}" style="width:${usedPct}%"></div>
        </div>
      </div>
      ${exitLine ? `<p class="pe-progress__timeout">${exitLine}</p>` : ''}
    </div>
  `;
  const canvas = overlay.querySelector('.pe-progress__plot');
  const paint = () => {
    if (!drawPlot(canvas, snap)) requestAnimationFrame(paint);
  };
  paint();
}
