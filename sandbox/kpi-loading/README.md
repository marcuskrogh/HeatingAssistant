# Sandbox: compute timer loading overlay

Serve a representative pair of Overview controller KPIs plus countdown rings.

```bash
python3 sandbox/kpi-loading/harness.py --tag 02
python3 sandbox/kpi-loading/harness.py --serve-only   # then open the printed URL
```

Query `?mode=idle|nmpc|control` pins `nmpc_computing` / `control_computing`.
