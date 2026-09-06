# Normalised RMS proxy (sandbox iteration 5)

- σ_R = sqrt(R_var) = **0.50 °C** (R_var=0.25)
- η = sqrt(J / n_obs) = RMSE / σ_R.  Tolerance **η ≤ 2** → RMS ≤ **1.00 °C**.
- Bench window 32 steps: n_obs OE=30, N-step=58
- Prior tiled OE: J=97.4 → η=1.80 (0.90 °C)
- Prior N-step: J=6.19 → η=0.33 (0.16 °C)
- After short L-BFGS: η=0.09 (0.05 °C) within tolerance
- 5-day n_obs=7398; screenshot J=54608 → η=2.72 (1.36 °C)

