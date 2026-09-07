# Single-start N-step MLE vs production multistart

- Grid: dt=900.0 s, N=144, stride=8
- Window: 24 h (96 steps)
- Truth: C=8000000 J/K, R=0.025
- Prior: C=12000000 J/K, R=0.04
- Cap: none (to L-BFGS stop)
- Bar: η ≤ 2.0 (same as production overlay); significantly worse if candidate misses the bar while baseline meets it, or η > 1.5× baseline.

| method | success | nfev | s | η | RMS °C | C | C rel err | R | R rel err |
|--------|---------|------|---|---|--------|---|-----------|---|-----------|
| production_multistart | True | 432 | 64.0 | 0.099 | 0.049 | 3929704 | 50.9% | 0.0502 | 100.6% |
| single_mle | True | 99 | 15.9 | 0.099 | 0.049 | 3929704 | 50.9% | 0.0502 | 100.6% |

**Verdict:** accept: single MLE matches fit bar with fewer evaluations — promote dropping extra starts

Single N-step MLE from the configured prior, one L-BFGS. Production path is untouched; the candidate monkeypatches `_multistart_joint_nlp` in this process only.

