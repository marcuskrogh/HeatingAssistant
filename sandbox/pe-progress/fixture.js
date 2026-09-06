/** Replay a 5-minute PE job in the normalised RMS proxy η = RMSE / σ_R.
 *  Line-search spikes stay visible; the reasonable-fit line at η=2 is
 *  reachable (1 °C RMS at the production R_var = 0.25). */

import { ETA_NOISE, ETA_TOL, R_VAR, etaFromJ, rmseCFromEta } from './proxy.js';

export const CAP_S = 300;
export { ETA_TOL, R_VAR };

const N_OBS_OE = 460;
const N_OBS_NSTEP = 7398;

function etaAt(evalIndex) {
  if (evalIndex < 14) {
    return 5.8 * Math.exp(-evalIndex / 5.2) + 2.15;
  }
  if (evalIndex === 14) return 18.4;
  if (evalIndex === 15) return 2.05;
  if (evalIndex === 16) return 1.92;
  const k = evalIndex - 16;
  const base = 1.85 * Math.exp(-k / 20) + 1.12;
  if (k === 19) return 12.6;
  if (k === 8 || k === 27) return Math.min(16, base * 6.5);
  return base;
}

function phaseAt(evalIndex) {
  return evalIndex < 14 ? 'tiled_oe' : 'nstep_pem';
}

function nObsAt(evalIndex) {
  return phaseAt(evalIndex) === 'tiled_oe' ? N_OBS_OE : N_OBS_NSTEP;
}

export function buildHistory(upto) {
  const hist = [];
  for (let i = 0; i <= upto; i += 1) {
    const eta = etaAt(i);
    const nObs = nObsAt(i);
    const f = eta * eta * nObs;
    hist.push({
      nfev: i + 1,
      f,
      eta,
      n_obs: nObs,
      phase: phaseAt(i),
    });
  }
  return hist;
}

export function snapshot({ elapsedS, nowS = Date.now() / 1000 }) {
  const elapsed = Math.max(0, Math.min(CAP_S, elapsedS));
  const evalIndex = Math.min(90, Math.floor(elapsed / 3.2));
  const hist = buildHistory(evalIndex);
  const last = hist[hist.length - 1];
  const eta = last.eta ?? etaFromJ(last.f, last.n_obs);
  const rmse = rmseCFromEta(eta);
  return {
    status: elapsed >= CAP_S ? 'error' : 'running',
    started_at: nowS - elapsed,
    cap_s: CAP_S,
    elapsed_s: elapsed,
    remaining_s: Math.max(0, CAP_S - elapsed),
    phase: last.phase,
    nfev: last.nfev,
    f: last.f,
    eta,
    n_obs: last.n_obs,
    r_var: R_VAR,
    eta_tol: ETA_TOL,
    eta_noise: ETA_NOISE,
    rmse_c: rmse,
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
