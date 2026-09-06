/** Replay a 5-minute PE job with production-like J jumps: tiled-OE
 *  warm-start (small MSE), then N-step PEM (same recorder, larger J)
 *  plus L-BFGS line-search spikes. Plot is log-scaled against ftol. */

export const FTOL = 1e-12;
export const CAP_S = 300;

function jAt(evalIndex) {
  if (evalIndex < 14) {
    return 38.0 * Math.exp(-evalIndex / 5.0) + 4.2;
  }
  if (evalIndex === 14) return 81240.0;
  if (evalIndex === 15) return 6.1;
  if (evalIndex === 16) return 5.4;
  const k = evalIndex - 16;
  const base = 5.2 * Math.exp(-k / 22) + 3.85;
  if (k === 19) return 54608.05;
  if (k === 8 || k === 27) return base * 1400;
  return base;
}

function phaseAt(evalIndex) {
  return evalIndex < 14 ? 'tiled_oe' : 'nstep_pem';
}

export function buildHistory(upto) {
  const hist = [];
  let prev = null;
  for (let i = 0; i <= upto; i += 1) {
    const f = jAt(i);
    const scale = Math.max(Math.abs(f), Math.abs(prev ?? f), 1);
    const rel = prev == null ? 1 : Math.abs(f - prev) / scale;
    hist.push({ nfev: i + 1, f, rel_step: rel, phase: phaseAt(i) });
    prev = f;
  }
  return hist;
}

export function snapshot({ elapsedS, nowS = Date.now() / 1000 }) {
  const elapsed = Math.max(0, Math.min(CAP_S, elapsedS));
  const evalIndex = Math.min(90, Math.floor(elapsed / 3.2));
  const hist = buildHistory(evalIndex);
  const last = hist[hist.length - 1];
  return {
    status: elapsed >= CAP_S ? 'error' : 'running',
    started_at: nowS - elapsed,
    cap_s: CAP_S,
    elapsed_s: elapsed,
    remaining_s: Math.max(0, CAP_S - elapsed),
    phase: last.phase,
    nfev: last.nfev,
    f: last.f,
    rel_step: last.rel_step,
    ftol: FTOL,
    f_hist: hist,
    message: elapsed >= CAP_S
      ? 'Stopped after 5 minutes (the configured maximum). Parameters were not applied.'
      : null,
  };
}

export const CAPTURE = {
  start: 8,
  mid: 133,
  late: 282,
  timeout: 300,
};
