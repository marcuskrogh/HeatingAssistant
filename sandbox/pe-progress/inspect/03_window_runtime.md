# PE eval time vs window (this host)

One-room synthetic history on the production NMPC grid (`dt = 900 s`, `N = 144`,
origin every 8 fast steps). Median of two `J+grad` calls after one warmup.

| Window | Fast steps | Origins | N-step s/eval | Tiled OE s/eval | Evals in 1 min | Evals in 5 min |
|--------|------------|---------|---------------|-----------------|----------------|----------------|
| 6 h    | 24         | 3       | 0.018         | 0.005           | ~3400          | ~17000         |
| 12 h   | 48         | 6       | 0.051         | 0.010           | ~1170          | ~5900          |
| 1 d    | 96         | 12      | 0.16          | 0.019           | ~370           | ~1860          |
| 2 d    | 192        | 24      | 0.54          | 0.037           | ~112           | ~560           |
| 3 d    | 288        | 36      | 0.92          | 0.056           | ~65            | ~325           |
| 5 d    | 480        | 60      | 1.73          | 0.093           | ~35            | ~174           |

6 h is shorter than the 36 h look-ahead, so each origin scores a short remainder
and is cheap. From about 2 d onward cost tracks window length (and origin count).

A production job spends some of the cap on tiled-OE L-BFGS first, then N-step
starts (`maxiter` 500, `ftol` 1e-12). The 5 min column is an upper bound on
N-step evaluations if the whole cap went to PEM.

This cloud VM is not the HAOS box. Extra rooms grow `nx` and `θ` (EKF `P`
Jacobians).
