# Single-start N-step MLE sandbox

Measure: production PE multistart versus one L-BFGS on the N-step PEM
objective from the configured prior.

```bash
MPLBACKEND=Agg PYTHONPATH=. python3 sandbox/pe-single-mle/harness.py
```

Inspectables land in `inspect/`. Production source is not edited.
