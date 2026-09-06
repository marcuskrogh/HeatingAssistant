# PE Jacobian and convergence (sandbox iteration 4)

- Host: `cursor` `x86_64` Python 3.12.3
- Window: 32 steps, N=8, stride=4, dt=900s
- dfdx max rel err: **9.669e-12** (bar 0.0005, pass)
- tiled OE θ-grad max rel err: **7.883e-08** (bar 0.05, pass); J=97.41
- N-step θ-grad max rel err: **2.421e-03** (bar 0.05, pass); J=6.186
- L-BFGS recorder: 32 evals, J in [0.4989, 1442] (ratio 2.89e+03); phases ['nstep_pem', 'tiled_oe']

## Worst N-step parameters

| param | analytic | finite-diff | rel err |
|-------|----------|-------------|---------|
| `log_mass[0]` | -38.69 | -38.79 | 2.421e-03 |
| `c_air[0]` | -843.8 | -845.7 | 2.238e-03 |
| `log_r[0]` | 49.02 | 48.93 | 1.915e-03 |
| `r_aw[0]` | 1003 | 1002 | 1.844e-03 |
| `q_int[0]` | -0.001914 | -0.001914 | 8.888e-07 |
| `log_alpha[0]` | 87.71 | 87.71 | 9.275e-10 |
| `t_wall[0]` | 1.373 | 1.373 | 7.869e-11 |

