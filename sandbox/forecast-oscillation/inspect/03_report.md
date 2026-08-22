# Forecast oscillation diagnostic (03) — forced screenshot U

NOW = `2026-08-22T05:54:00+00:00`; q_cool = 5000 W; UA_fast ≈ 300.5 W/K; expected ΔT ≈ 3.33 K per kW step

## Plant
- C_air = 2.500e+05 J/K; τ_air ≈ 13.9 min; n_int = 10; dt = 900 s

## Forced U through production roll_fast_air_path
- 15-min smooth bowl: max |ΔT| = 0.571 K; flips = 2; p-p = 15.630 K; night max |ΔT| = 0.288 K
- 2 h stairs of the same bowl: max |ΔT| = 0.537 K; flips = 14; p-p = 15.530 K; vs RK45 0.0166 K; night max |ΔT| = 0.308 K
- Large 2 h steps to −1.5 kW: max |ΔT| = 1.212 K; flips = 9; p-p = 19.290 K; night max |ΔT| = 0.295 K
- Constant min(U) ≈ −1.5 kW: max |ΔT| = 1.142 K; flips = 2; night max |ΔT| = 0.268 K
- U = 0 (disturbances only): max |ΔT| = 0.301 K; flips = 3; p-p = 6.135 K
- short 4 h dip to −1.5 kW: max |ΔT| = 0.444 K (stairs 1.582 K); T ∈ [20.62, 26.77] °C

## Cubic spline (Chart.js-like extra wiggle)
- smooth knots p-p 15.630 K → spline 15.638 K (+0.008 K)
- stairs knots p-p 15.530 K → spline 15.535 K (+0.005 K)

## c_air_fraction (engine default 0.05; config ignored)
- 0.01: max |ΔT| = 0.812 K; flips = 17; τ_air = 2.9 min; C_air = 5.000e+04 J/K
- 0.05: max |ΔT| = 0.537 K; flips = 14; τ_air = 13.9 min; C_air = 2.500e+05 J/K
- 0.2: max |ΔT| = 0.397 K; flips = 8; τ_air = 46.9 min; C_air = 1.000e+06 J/K

## GHI None tail (fallback ghi_now), interleaved None, wall offset
- tail leak vs production stairs max |ΔT| = 2.138 K
- interleaved None + smooth U: max |ΔT| = 1.942 K; flips = 78; q_solar max |Δ| = 1739.3 W
- interleaved None U=0: max |ΔT| = 1.941 K; flips = 85
- wall T = air+5 K: max |ΔT| = 0.937 K; p-p = 16.560 K

## Payload (NOW bridge + 15 min predictions)
- T max |ΔT| = 0.540 K; bridge→first pred = -0.490 K
- times monotonic = True; dt ∈ [900, 900] s
- display P vs plant Q max |Δ| = 4.730627585169955e-05 kW

Room config c_air_fraction is ignored by ControlEngine (_build_house_model); plant uses DEFAULT 0.05.

