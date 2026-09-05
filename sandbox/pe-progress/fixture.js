/** Replay a 5-minute PE job: tiled-OE warm-start, then N-step PEM that
 *  never reaches SciPy ftol (the 5-day / 5-minute case). */

export const FTOL = 1e-12;
export const CAP_S = 300;

function jAt(evalIndex) {
  if (evalIndex < 18) {
    return 42.0 * Math.exp(-evalIndex / 7.5) + 6.8;
  }
  const k = evalIndex - 18;
  return 9.4 * Math.exp(-k / 28) + 4.15 + 0.12 * Math.sin(k / 5);
}

export function buildHistory(upto) {
  const hist = [];
  let prev = null;
  for (let i = 0; i <= upto; i += 1) {
    const f = jAt(i);
    const scale = Math.max(Math.abs(f), Math.abs(prev ?? f), 1);
    const rel = prev == null ? 1 : Math.abs(f - prev) / scale;
    hist.push({ nfev: i + 1, f, rel_step: rel });
    prev = f;
  }
  return hist;
}

export function snapshot({ elapsedS, nowS = Date.now() / 1000 }) {
  const elapsed = Math.max(0, Math.min(CAP_S, elapsedS));
  const evalIndex = Math.min(90, Math.floor(elapsed / 3.2));
  const hist = buildHistory(evalIndex);
  const last = hist[hist.length - 1];
  const phase = evalIndex < 18 ? 'tiled_oe' : 'nstep_pem';
  return {
    status: elapsed >= CAP_S ? 'error' : 'running',
    started_at: nowS - elapsed,
    cap_s: CAP_S,
    elapsed_s: elapsed,
    remaining_s: Math.max(0, CAP_S - elapsed),
    phase,
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
