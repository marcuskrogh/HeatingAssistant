/** Normalised PE convergence proxy.

Production J is SSE / (n_rooms * R_var) *summed* over residual steps, so it
grows with the dataset and SciPy ftol (relative 1e-12) is not a fit-quality
line.  With n_obs residual steps:

  η = sqrt(J / n_obs) = RMSE / σ_R

σ_R = sqrt(R_var) = 0.5 °C at the production default.  η = 1 matches the
EKF noise model.  η_tol = 2 is a reachable “reasonable fit” (1.0 °C RMS).
*/

export const R_VAR = 0.25;
export const SIGMA_C = Math.sqrt(R_VAR);
export const ETA_TOL = 2;
export const ETA_NOISE = 1;

export function etaFromJ(j, nObs) {
  const jj = Number(j);
  const n = Number(nObs);
  if (!(jj >= 0) || !(n > 0) || !Number.isFinite(jj) || !Number.isFinite(n)) {
    return null;
  }
  return Math.sqrt(jj / n);
}

export function rmseCFromEta(eta) {
  const e = Number(eta);
  if (!(e >= 0) || !Number.isFinite(e)) return null;
  return e * SIGMA_C;
}

export function pointEta(p) {
  if (p == null) return null;
  if (Number.isFinite(Number(p.eta))) return Number(p.eta);
  return etaFromJ(p.f, p.n_obs);
}
