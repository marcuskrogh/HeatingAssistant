# Architecture: Revert PE fake slow origin grid (SWD-561)

## Shape
- Lives: `heatingassistant/engine/parameter_lifecycle.py` (production `origin_stride`);
  `heatingassistant/engine/nmpc_timing.py` (delete `pe_origin_stride` / period helper)
- Depends on: existing `NmpcTiming.fast_substeps` after single-rate coerce
- Seams: lifecycle constructor arg `origin_stride=timing.fast_substeps`;
  tests that lock production wiring (not a second timing helper)
- Will not add: origin-period config, reconstructed slow grid, new PE module

## Neighbourhood
- Opened: PE estimator wiring (same as SWD-558 / SWD-481)
- Major refinement: none — remove the extra helper; reuse NMPC timing as-is

## Tracker
- Task: SWD-561
- Branch: `cursor/swd-561-revert-pe-slow-origin-9845`

## Next
`/implement SWD-561` — Build to this shape
