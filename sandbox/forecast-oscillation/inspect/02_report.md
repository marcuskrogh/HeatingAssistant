# Forecast oscillation diagnostic (02)

NOW = `2026-08-22T05:54:00+00:00`; NMPC accepted=True applied=True

## Discrete map (frozen U=−0.22, T_out=18, q_solar=0)
- max |ΔT| = 1.0966 K; sign flips = 0
- implicit Euler vs RK45 max |ΔT| = 0.0733 K
- n_int vs 100: 1→0.5194 K, 10→0.0657 K, 40→0.0113 K, 100→0.0000 K

## Plant
- eig max real = -4.13e-06; any unstable = False; max |Im| = 0
- C_air = 2.500e+05 J/K; C_wall = 4.750e+06 J/K; τ_em = 60.0 s; n_int = 10; dt = 900 s

## Production remaining-U* resim (screenshot-like weather + GHI)
- T ∈ [21.50, 24.25] °C; max |ΔT| = 0.559 K; sign flips = 24
- P ∈ [-0.562, 0.076] kW; max |ΔP| = 0.304 kW; sign flips = 0
- q_solar ∈ [0.0, 854.1] W; max |Δq| = 179.6 W; sign flips = 8
- T_out max |Δ| = 0.388 K; sign flips = 3
- vs independent resim 0.0000 K; vs RK45 0.0168 K
- remaining U ∈ [-0.112, 0.011]; unique slow values = 18

## Ablations (same U*)
- no solar: max |ΔT| = 0.632 K, flips = 20 (moves production by 9.126 K)
- persist T_out: max |ΔT| = 0.534 K, flips = 28
- 2 h T_out ZOH: max |ΔT| = 0.572 K (vs 15 min outdoor 0.112 K)

## Candidate: hold d on the same slow grid as U*
- T max |ΔT| = 0.490 K; sign flips = 22 (production 0.559 K / 24 flips)
- q_solar max |Δq| = 503.3 W (production 179.6 W)
- vs production max |ΔT| = 0.551 K

## Energy / timestamps
- implicit-Euler storage vs f residual RMS 3.873 W, max 31.773 W
- payload times monotonic = True; dt ∈ [900, 900] s

Room config c_air_fraction is ignored by ControlEngine (_build_house_model); plant uses DEFAULT 0.05.

