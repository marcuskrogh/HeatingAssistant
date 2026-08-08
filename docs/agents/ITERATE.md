# Iterate: Ingress UI 404 — static assets missing from pip install

## Prior work
- Task: SWD-263
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/547 (merge `5c2bdd4`)
- Spec context: docs/agents/PLAN-haos-app-mqtt.md / prior ITERATE.md

## Problem
Opening HeatingAssistant from the HA side panel shows:

```
Error response
Error code: 404
Message: file not found.
```

App process is up (SWD-263 fixed argparse), but Ingress `/` calls `_send_file(index.html)` and the
file is absent in the installed package. Wheel build contains **0** static files because
`pyproject.toml` has no setuptools `package-data` for `heatingassistant/app/static/`.

## Acceptance criteria
1. `python -m build` wheel includes `heatingassistant/app/static/index.html` and panel assets.
2. After `pip install` of that wheel, `Path(heatingassistant.app.__file__).with_name("static")/index.html` exists.
3. HTTP `/` and `/ha-industrial-panel/industrial-dashboard.js` return 200 from the installed package layout.
4. Version bump so Supervisor offers Update.

## Out of scope
- Absolute URL / `X-Ingress-Path` base-href rewriting (follow-up if assets 404 after HTML loads).
- Changing panel UX beyond making static assets installable.

## Work packages
1. Add setuptools package-data for static assets + packaging regression test
2. Version bump 2.0.3 + sync App package
3. PR / handoff

## Tracker
- Task: SWD-264
- Relates: SWD-263
- Branch: `cursor/swd-264-ingress-static-01f0`

## Next
`/review-fix SWD-264` — Review and auto-fix (single pass)
