# Report: PE robustness on household-like 2R2C traces

Offline synthetic factorial for SWD-329. **No product winner is declared.**
Judge whether extras / procedures recover true θ well enough to ship.

## Truth

- thermal_mass = 8.000e+06 J/K
- r_external = 0.025 K/W
- solar_scale = 1.5
- power_scale (α) = 1.0
- Prior (wrong on purpose): C=4.000e+06, R=0.05, s=1.0

## Plant extras (known; not production HouseModel)

- Occupancy: bursty household schedule on the air node (none / weak 80 W peak / strong 250 W peak).
- Open window/door: extra outdoor exchange Q = UA (T_out - T_a) (none / weak 8 W/K / strong 25 W/K).
- Occupancy watts are stored on records for the plant only; procedures never pass them to the estimator.

## Procedures (best-effort harness)

1. `today_combined` — combined joint, constant `internal_gain`, SWD-322 `window_open` mask (production open-loop exclusion).
2. `occupancy_tv` — night/empty **clock** fragments (00:00–06:00 and 23:00–24:00) for envelope C,R with `internal_gain` locked 0; then daytime with C,R locked. Does **not** use plant occupancy watts.
3. `window_ua` — include open samples; inject assumed UA = 15 W/K × contact × (T_out − y) into the air-heat disturbance slot. Assumed UA is not plant truth.
4. `both` — occupancy_tv + assumed-UA channel.
5. `separated_joint` — SWD-326 solar-off + heater-off+solar fragments, open samples dropped.
6. `separated_staged` — SWD-326 staged locking, open samples dropped.

## Estimator paths

- `open_loop` — production `KalmanMLEstimator.estimate()` (open-loop simulation MSE, SciPy L-BFGS-B).
- `kalman` — same `estimate()` entry, with the open-loop objective swapped for CD-EKF PED NLL (`_cd_ped_neg_ll_and_grad`). Harness-only; production `estimate()` is unchanged. PED may still score open-window samples (exclusion is an open-loop MSE feature).

## Runtime caps

- n_steps = 96 (24 h at 15 min).
- maxiter = 25; physics-informed start skipped (prior only).
- Grid size: 108 fits (3×3 scenarios × 6 procedures × 2 paths); staged procedures add extra inner fits.
- Marker: `pytest.mark.ondemand` — not CI.

## Relative |error| vs true θ

| Scenario | Procedure | Path | OK | C | R_ext | solar_scale | α | notes |
|----------|-----------|------|----|---|-------|-------------|---|-------|
| occ_none__win_none | today_combined | open_loop | yes | 23.4% | 50.5% | 48.1% | 23.5% |  |
| occ_none__win_none | today_combined | kalman | yes | 65.4% | 8.3% | 36.4% | 8.8% |  |
| occ_none__win_none | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 53.7% | 66.9% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_none | occupancy_tv | kalman | yes | 32.7% | 18.7% | 20.3% | 6.7% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_none | window_ua | open_loop | yes | 23.4% | 50.5% | 48.1% | 23.5% | notes=['include_open_assumed_ua'] |
| occ_none__win_none | window_ua | kalman | yes | 65.4% | 8.3% | 36.4% | 8.8% | notes=['include_open_assumed_ua'] |
| occ_none__win_none | both | open_loop | yes | 53.4% | 259.3% | 53.7% | 66.9% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_none | both | kalman | yes | 32.7% | 18.7% | 20.3% | 6.7% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_none | separated_joint | open_loop | yes | 52.1% | 515.3% | 33.3% | 31.5% |  |
| occ_none__win_none | separated_joint | kalman | yes | 62.8% | 45.5% | 33.3% | 33.9% |  |
| occ_none__win_none | separated_staged | open_loop | yes | 52.1% | 515.3% | 66.7% | 60.0% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_none | separated_staged | kalman | yes | 62.8% | 45.5% | 39.9% | 22.1% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_weak | today_combined | open_loop | yes | 46.7% | 181.0% | 69.9% | 54.3% |  |
| occ_none__win_weak | today_combined | kalman | yes | 64.6% | 11.0% | 34.8% | 11.3% |  |
| occ_none__win_weak | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 71.0% | 36.0% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_weak | occupancy_tv | kalman | yes | 32.7% | 18.7% | 49.7% | 23.3% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_weak | window_ua | open_loop | yes | 47.2% | 165.0% | 74.4% | 50.6% | notes=['include_open_assumed_ua'] |
| occ_none__win_weak | window_ua | kalman | yes | 64.4% | 9.7% | 35.5% | 14.5% | notes=['include_open_assumed_ua'] |
| occ_none__win_weak | both | open_loop | yes | 53.4% | 259.3% | 54.6% | 68.9% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_weak | both | kalman | yes | 32.7% | 18.7% | 52.2% | 20.7% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_weak | separated_joint | open_loop | yes | 52.3% | 297.2% | 33.3% | 15.5% |  |
| occ_none__win_weak | separated_joint | kalman | yes | 53.8% | 0.1% | 33.3% | 9.0% |  |
| occ_none__win_weak | separated_staged | open_loop | yes | 52.3% | 297.2% | 78.2% | 63.1% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_weak | separated_staged | kalman | yes | 53.8% | 0.1% | 42.9% | 8.4% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_strong | today_combined | open_loop | yes | 27.5% | 35.4% | 67.8% | 7.9% |  |
| occ_none__win_strong | today_combined | kalman | yes | 68.4% | 2.3% | 31.6% | 9.4% |  |
| occ_none__win_strong | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 64.2% | 22.7% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_strong | occupancy_tv | kalman | yes | 32.7% | 18.7% | 37.7% | 13.7% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_strong | window_ua | open_loop | yes | 40.9% | 198.0% | 74.3% | 56.5% | notes=['include_open_assumed_ua'] |
| occ_none__win_strong | window_ua | kalman | yes | 32.0% | 44.5% | 33.7% | 7.6% | notes=['include_open_assumed_ua'] |
| occ_none__win_strong | both | open_loop | yes | 53.4% | 259.3% | 59.5% | 67.4% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_strong | both | kalman | yes | 32.7% | 18.7% | 21.0% | 11.0% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_strong | separated_joint | open_loop | yes | 4.4% | 148.3% | 33.3% | 24.3% |  |
| occ_none__win_strong | separated_joint | kalman | yes | 63.0% | 4.2% | 33.3% | 18.2% |  |
| occ_none__win_strong | separated_staged | open_loop | yes | 4.4% | 148.3% | 76.9% | 43.7% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_none__win_strong | separated_staged | kalman | yes | 63.0% | 4.2% | 32.7% | 1.1% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_none | today_combined | open_loop | yes | 44.6% | 37.0% | 43.6% | 14.8% |  |
| occ_weak__win_none | today_combined | kalman | yes | 68.8% | 4.7% | 36.7% | 4.7% |  |
| occ_weak__win_none | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 56.8% | 68.6% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_none | occupancy_tv | kalman | yes | 32.7% | 18.7% | 41.1% | 10.7% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_none | window_ua | open_loop | yes | 44.6% | 37.0% | 43.6% | 14.8% | notes=['include_open_assumed_ua'] |
| occ_weak__win_none | window_ua | kalman | yes | 68.8% | 4.7% | 36.7% | 4.7% | notes=['include_open_assumed_ua'] |
| occ_weak__win_none | both | open_loop | yes | 53.4% | 259.3% | 56.8% | 68.6% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_none | both | kalman | yes | 32.7% | 18.7% | 41.1% | 10.7% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_none | separated_joint | open_loop | yes | 7.3% | 41.6% | 33.3% | 20.2% |  |
| occ_weak__win_none | separated_joint | kalman | yes | 53.0% | 33.4% | 33.3% | 40.0% |  |
| occ_weak__win_none | separated_staged | open_loop | yes | 7.3% | 41.6% | 43.6% | 13.2% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_none | separated_staged | kalman | yes | 53.0% | 33.4% | 37.1% | 8.3% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_weak | today_combined | open_loop | yes | 77.9% | 85.5% | 64.6% | 33.6% |  |
| occ_weak__win_weak | today_combined | kalman | yes | 64.3% | 6.5% | 35.7% | 11.2% |  |
| occ_weak__win_weak | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 80.1% | 32.1% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_weak | occupancy_tv | kalman | yes | 32.7% | 18.7% | 31.2% | 18.9% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_weak | window_ua | open_loop | yes | 73.8% | 271.6% | 76.6% | 64.9% | notes=['include_open_assumed_ua'] |
| occ_weak__win_weak | window_ua | kalman | yes | 64.1% | 7.9% | 35.2% | 12.4% | notes=['include_open_assumed_ua'] |
| occ_weak__win_weak | both | open_loop | yes | 53.4% | 259.3% | 53.3% | 63.4% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_weak | both | kalman | yes | 32.7% | 18.7% | 57.7% | 29.1% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_weak | separated_joint | open_loop | yes | 10.9% | 72.3% | 33.3% | 38.3% |  |
| occ_weak__win_weak | separated_joint | kalman | yes | 56.3% | 7.0% | 33.3% | 15.4% |  |
| occ_weak__win_weak | separated_staged | open_loop | yes | 10.9% | 72.3% | 53.8% | 25.5% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_weak | separated_staged | kalman | yes | 56.3% | 7.0% | 45.3% | 4.1% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_strong | today_combined | open_loop | yes | 27.6% | 40.3% | 69.7% | 5.9% |  |
| occ_weak__win_strong | today_combined | kalman | yes | 68.1% | 1.9% | 33.1% | 10.2% |  |
| occ_weak__win_strong | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 65.3% | 23.0% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_strong | occupancy_tv | kalman | yes | 32.7% | 18.7% | 52.9% | 2.2% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_strong | window_ua | open_loop | yes | 46.5% | 177.9% | 74.6% | 52.1% | notes=['include_open_assumed_ua'] |
| occ_weak__win_strong | window_ua | kalman | yes | 29.0% | 23.6% | 34.0% | 8.8% | notes=['include_open_assumed_ua'] |
| occ_weak__win_strong | both | open_loop | yes | 53.4% | 259.3% | 61.4% | 55.0% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_strong | both | kalman | yes | 32.7% | 18.7% | 24.7% | 5.4% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_strong | separated_joint | open_loop | yes | 0.7% | 126.0% | 33.3% | 35.3% |  |
| occ_weak__win_strong | separated_joint | kalman | yes | 31.7% | 20.6% | 33.3% | 8.7% |  |
| occ_weak__win_strong | separated_staged | open_loop | yes | 0.7% | 126.0% | 76.4% | 38.3% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_weak__win_strong | separated_staged | kalman | yes | 31.7% | 20.6% | 33.3% | 0.4% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_none | today_combined | open_loop | yes | 68.3% | 18.3% | 43.7% | 6.2% |  |
| occ_strong__win_none | today_combined | kalman | yes | 67.0% | 24.7% | 39.3% | 8.3% |  |
| occ_strong__win_none | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 64.4% | 68.6% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_none | occupancy_tv | kalman | yes | 32.7% | 18.7% | 21.6% | 25.6% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_none | window_ua | open_loop | yes | 68.3% | 18.3% | 43.7% | 6.2% | notes=['include_open_assumed_ua'] |
| occ_strong__win_none | window_ua | kalman | yes | 67.0% | 24.7% | 39.3% | 8.3% | notes=['include_open_assumed_ua'] |
| occ_strong__win_none | both | open_loop | yes | 53.4% | 259.3% | 64.4% | 68.6% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_none | both | kalman | yes | 32.7% | 18.7% | 21.6% | 25.6% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_none | separated_joint | open_loop | yes | 31.3% | 44.5% | 33.3% | 15.7% |  |
| occ_strong__win_none | separated_joint | kalman | yes | 62.3% | 58.5% | 33.3% | 28.7% |  |
| occ_strong__win_none | separated_staged | open_loop | yes | 31.3% | 44.5% | 53.6% | 0.3% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_none | separated_staged | kalman | yes | 62.3% | 58.5% | 63.5% | 16.0% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_weak | today_combined | open_loop | yes | 61.3% | 14.6% | 43.8% | 12.4% |  |
| occ_strong__win_weak | today_combined | kalman | yes | 62.2% | 1.6% | 36.5% | 10.0% |  |
| occ_strong__win_weak | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 86.1% | 70.0% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_weak | occupancy_tv | kalman | yes | 32.7% | 18.7% | 29.4% | 31.1% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_weak | window_ua | open_loop | yes | 66.1% | 28.5% | 49.0% | 0.6% | notes=['include_open_assumed_ua'] |
| occ_strong__win_weak | window_ua | kalman | yes | 60.7% | 4.2% | 37.2% | 16.3% | notes=['include_open_assumed_ua'] |
| occ_strong__win_weak | both | open_loop | yes | 53.4% | 259.3% | 62.8% | 61.8% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_weak | both | kalman | yes | 32.7% | 18.7% | 39.7% | 35.1% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_weak | separated_joint | open_loop | yes | 30.2% | 45.5% | 33.3% | 21.0% |  |
| occ_strong__win_weak | separated_joint | kalman | yes | 50.7% | 23.6% | 33.3% | 10.2% |  |
| occ_strong__win_weak | separated_staged | open_loop | yes | 30.2% | 45.5% | 70.8% | 26.7% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_weak | separated_staged | kalman | yes | 50.7% | 23.6% | 38.3% | 2.2% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_strong | today_combined | open_loop | yes | 45.0% | 22.8% | 60.1% | 20.7% |  |
| occ_strong__win_strong | today_combined | kalman | yes | 60.6% | 7.2% | 35.2% | 8.3% |  |
| occ_strong__win_strong | occupancy_tv | open_loop | yes | 53.4% | 259.3% | 68.0% | 21.5% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_strong | occupancy_tv | kalman | yes | 32.7% | 18.7% | 51.2% | 29.9% | notes=['stage1_night_envelope', 'stage2_day_disturbance'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_strong | window_ua | open_loop | yes | 67.7% | 66.7% | 58.0% | 23.7% | notes=['include_open_assumed_ua'] |
| occ_strong__win_strong | window_ua | kalman | yes | 63.1% | 2.1% | 36.6% | 6.5% | notes=['include_open_assumed_ua'] |
| occ_strong__win_strong | both | open_loop | yes | 53.4% | 259.3% | 66.9% | 67.9% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_strong | both | kalman | yes | 32.7% | 18.7% | 58.8% | 20.0% | notes=['stage1_night_envelope', 'stage2_day_disturbance', 'plus_assumed_ua'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_strong | separated_joint | open_loop | yes | 9.1% | 38.2% | 33.3% | 72.3% |  |
| occ_strong__win_strong | separated_joint | kalman | yes | 59.5% | 7.0% | 33.3% | 12.7% |  |
| occ_strong__win_strong | separated_staged | open_loop | yes | 9.1% | 38.2% | 53.6% | 19.7% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |
| occ_strong__win_strong | separated_staged | kalman | yes | 59.5% | 7.0% | 49.7% | 0.1% | notes=['stage1_solar_off_envelope', 'stage2_skipped_short'] locked=['r_external', 'thermal_mass'] |

## Recovered values

| Scenario | Procedure | Path | C | R_ext | solar_scale | α |
|----------|-----------|------|---|-------|-------------|---|
| occ_none__win_none | today_combined | open_loop | 6.128e+06 | 0.0376 | 0.779 | 0.765 |
| occ_none__win_none | today_combined | kalman | 2.765e+06 | 0.0229 | 0.953 | 1.088 |
| occ_none__win_none | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.694 | 0.331 |
| occ_none__win_none | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 1.196 | 1.067 |
| occ_none__win_none | window_ua | open_loop | 6.128e+06 | 0.0376 | 0.779 | 0.765 |
| occ_none__win_none | window_ua | kalman | 2.765e+06 | 0.0229 | 0.953 | 1.088 |
| occ_none__win_none | both | open_loop | 3.729e+06 | 0.0898 | 0.694 | 0.331 |
| occ_none__win_none | both | kalman | 5.380e+06 | 0.0297 | 1.196 | 1.067 |
| occ_none__win_none | separated_joint | open_loop | 3.832e+06 | 0.1538 | 1.000 | 0.685 |
| occ_none__win_none | separated_joint | kalman | 2.976e+06 | 0.0364 | 1.000 | 0.661 |
| occ_none__win_none | separated_staged | open_loop | 3.832e+06 | 0.1538 | 0.500 | 0.400 |
| occ_none__win_none | separated_staged | kalman | 2.976e+06 | 0.0364 | 0.901 | 0.779 |
| occ_none__win_weak | today_combined | open_loop | 4.268e+06 | 0.0703 | 0.452 | 0.457 |
| occ_none__win_weak | today_combined | kalman | 2.831e+06 | 0.0222 | 0.978 | 1.113 |
| occ_none__win_weak | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.435 | 0.640 |
| occ_none__win_weak | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 0.754 | 1.233 |
| occ_none__win_weak | window_ua | open_loop | 4.227e+06 | 0.0662 | 0.385 | 0.494 |
| occ_none__win_weak | window_ua | kalman | 2.847e+06 | 0.0226 | 0.967 | 1.145 |
| occ_none__win_weak | both | open_loop | 3.729e+06 | 0.0898 | 0.681 | 0.311 |
| occ_none__win_weak | both | kalman | 5.380e+06 | 0.0297 | 0.717 | 1.207 |
| occ_none__win_weak | separated_joint | open_loop | 3.817e+06 | 0.0993 | 1.000 | 0.845 |
| occ_none__win_weak | separated_joint | kalman | 3.698e+06 | 0.0250 | 1.000 | 0.910 |
| occ_none__win_weak | separated_staged | open_loop | 3.817e+06 | 0.0993 | 0.327 | 0.369 |
| occ_none__win_weak | separated_staged | kalman | 3.698e+06 | 0.0250 | 0.857 | 1.084 |
| occ_none__win_strong | today_combined | open_loop | 5.802e+06 | 0.0338 | 0.483 | 1.079 |
| occ_none__win_strong | today_combined | kalman | 2.526e+06 | 0.0244 | 1.026 | 0.906 |
| occ_none__win_strong | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.536 | 0.773 |
| occ_none__win_strong | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 0.935 | 0.863 |
| occ_none__win_strong | window_ua | open_loop | 4.724e+06 | 0.0745 | 0.386 | 0.435 |
| occ_none__win_strong | window_ua | kalman | 5.436e+06 | 0.0361 | 0.995 | 0.924 |
| occ_none__win_strong | both | open_loop | 3.729e+06 | 0.0898 | 0.607 | 0.326 |
| occ_none__win_strong | both | kalman | 5.380e+06 | 0.0297 | 1.184 | 0.890 |
| occ_none__win_strong | separated_joint | open_loop | 8.354e+06 | 0.0621 | 1.000 | 1.243 |
| occ_none__win_strong | separated_joint | kalman | 2.963e+06 | 0.0240 | 1.000 | 0.818 |
| occ_none__win_strong | separated_staged | open_loop | 8.354e+06 | 0.0621 | 0.347 | 0.563 |
| occ_none__win_strong | separated_staged | kalman | 2.963e+06 | 0.0240 | 1.009 | 0.989 |
| occ_weak__win_none | today_combined | open_loop | 4.430e+06 | 0.0342 | 0.846 | 0.852 |
| occ_weak__win_none | today_combined | kalman | 2.499e+06 | 0.0262 | 0.949 | 0.953 |
| occ_weak__win_none | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.649 | 0.314 |
| occ_weak__win_none | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 0.884 | 1.107 |
| occ_weak__win_none | window_ua | open_loop | 4.430e+06 | 0.0342 | 0.846 | 0.852 |
| occ_weak__win_none | window_ua | kalman | 2.499e+06 | 0.0262 | 0.949 | 0.953 |
| occ_weak__win_none | both | open_loop | 3.729e+06 | 0.0898 | 0.649 | 0.314 |
| occ_weak__win_none | both | kalman | 5.380e+06 | 0.0297 | 0.884 | 1.107 |
| occ_weak__win_none | separated_joint | open_loop | 7.415e+06 | 0.0354 | 1.000 | 1.202 |
| occ_weak__win_none | separated_joint | kalman | 3.759e+06 | 0.0333 | 1.000 | 0.600 |
| occ_weak__win_none | separated_staged | open_loop | 7.415e+06 | 0.0354 | 0.846 | 0.868 |
| occ_weak__win_none | separated_staged | kalman | 3.759e+06 | 0.0333 | 0.944 | 0.917 |
| occ_weak__win_weak | today_combined | open_loop | 1.770e+06 | 0.0464 | 0.531 | 0.664 |
| occ_weak__win_weak | today_combined | kalman | 2.857e+06 | 0.0234 | 0.965 | 1.112 |
| occ_weak__win_weak | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.298 | 0.679 |
| occ_weak__win_weak | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 1.032 | 1.189 |
| occ_weak__win_weak | window_ua | open_loop | 2.098e+06 | 0.0929 | 0.351 | 0.351 |
| occ_weak__win_weak | window_ua | kalman | 2.873e+06 | 0.0230 | 0.972 | 1.124 |
| occ_weak__win_weak | both | open_loop | 3.729e+06 | 0.0898 | 0.700 | 0.366 |
| occ_weak__win_weak | both | kalman | 5.380e+06 | 0.0297 | 0.635 | 1.291 |
| occ_weak__win_weak | separated_joint | open_loop | 8.869e+06 | 0.0431 | 1.000 | 1.383 |
| occ_weak__win_weak | separated_joint | kalman | 3.495e+06 | 0.0267 | 1.000 | 0.846 |
| occ_weak__win_weak | separated_staged | open_loop | 8.869e+06 | 0.0431 | 0.694 | 0.745 |
| occ_weak__win_weak | separated_staged | kalman | 3.495e+06 | 0.0267 | 0.821 | 1.041 |
| occ_weak__win_strong | today_combined | open_loop | 5.792e+06 | 0.0351 | 0.454 | 1.059 |
| occ_weak__win_strong | today_combined | kalman | 2.555e+06 | 0.0255 | 1.003 | 0.898 |
| occ_weak__win_strong | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.521 | 0.770 |
| occ_weak__win_strong | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 0.707 | 1.022 |
| occ_weak__win_strong | window_ua | open_loop | 4.281e+06 | 0.0695 | 0.381 | 0.479 |
| occ_weak__win_strong | window_ua | kalman | 5.679e+06 | 0.0309 | 0.990 | 0.912 |
| occ_weak__win_strong | both | open_loop | 3.729e+06 | 0.0898 | 0.580 | 0.450 |
| occ_weak__win_strong | both | kalman | 5.380e+06 | 0.0297 | 1.129 | 0.946 |
| occ_weak__win_strong | separated_joint | open_loop | 7.944e+06 | 0.0565 | 1.000 | 1.353 |
| occ_weak__win_strong | separated_joint | kalman | 5.468e+06 | 0.0301 | 1.000 | 0.913 |
| occ_weak__win_strong | separated_staged | open_loop | 7.944e+06 | 0.0565 | 0.354 | 0.617 |
| occ_weak__win_strong | separated_staged | kalman | 5.468e+06 | 0.0301 | 1.000 | 0.996 |
| occ_strong__win_none | today_combined | open_loop | 2.535e+06 | 0.0296 | 0.845 | 1.062 |
| occ_strong__win_none | today_combined | kalman | 2.642e+06 | 0.0312 | 0.910 | 0.917 |
| occ_strong__win_none | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.533 | 0.314 |
| occ_strong__win_none | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 1.176 | 1.256 |
| occ_strong__win_none | window_ua | open_loop | 2.535e+06 | 0.0296 | 0.845 | 1.062 |
| occ_strong__win_none | window_ua | kalman | 2.642e+06 | 0.0312 | 0.910 | 0.917 |
| occ_strong__win_none | both | open_loop | 3.729e+06 | 0.0898 | 0.533 | 0.314 |
| occ_strong__win_none | both | kalman | 5.380e+06 | 0.0297 | 1.176 | 1.256 |
| occ_strong__win_none | separated_joint | open_loop | 5.493e+06 | 0.0361 | 1.000 | 1.157 |
| occ_strong__win_none | separated_joint | kalman | 3.019e+06 | 0.0396 | 1.000 | 0.713 |
| occ_strong__win_none | separated_staged | open_loop | 5.493e+06 | 0.0361 | 0.695 | 0.997 |
| occ_strong__win_none | separated_staged | kalman | 3.019e+06 | 0.0396 | 0.548 | 0.840 |
| occ_strong__win_weak | today_combined | open_loop | 3.096e+06 | 0.0286 | 0.843 | 1.124 |
| occ_strong__win_weak | today_combined | kalman | 3.020e+06 | 0.0246 | 0.953 | 1.100 |
| occ_strong__win_weak | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.208 | 0.300 |
| occ_strong__win_weak | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 1.058 | 1.311 |
| occ_strong__win_weak | window_ua | open_loop | 2.714e+06 | 0.0321 | 0.765 | 1.006 |
| occ_strong__win_weak | window_ua | kalman | 3.145e+06 | 0.0240 | 0.942 | 1.163 |
| occ_strong__win_weak | both | open_loop | 3.729e+06 | 0.0898 | 0.558 | 0.382 |
| occ_strong__win_weak | both | kalman | 5.380e+06 | 0.0297 | 0.904 | 1.351 |
| occ_strong__win_weak | separated_joint | open_loop | 5.583e+06 | 0.0364 | 1.000 | 1.210 |
| occ_strong__win_weak | separated_joint | kalman | 3.946e+06 | 0.0309 | 1.000 | 0.898 |
| occ_strong__win_weak | separated_staged | open_loop | 5.583e+06 | 0.0364 | 0.438 | 1.267 |
| occ_strong__win_weak | separated_staged | kalman | 3.946e+06 | 0.0309 | 0.925 | 1.022 |
| occ_strong__win_strong | today_combined | open_loop | 4.401e+06 | 0.0307 | 0.599 | 1.207 |
| occ_strong__win_strong | today_combined | kalman | 3.150e+06 | 0.0232 | 0.973 | 1.083 |
| occ_strong__win_strong | occupancy_tv | open_loop | 3.729e+06 | 0.0898 | 0.479 | 0.785 |
| occ_strong__win_strong | occupancy_tv | kalman | 5.380e+06 | 0.0297 | 0.732 | 1.299 |
| occ_strong__win_strong | window_ua | open_loop | 2.582e+06 | 0.0417 | 0.630 | 0.763 |
| occ_strong__win_strong | window_ua | kalman | 2.954e+06 | 0.0245 | 0.950 | 1.065 |
| occ_strong__win_strong | both | open_loop | 3.729e+06 | 0.0898 | 0.496 | 0.321 |
| occ_strong__win_strong | both | kalman | 5.380e+06 | 0.0297 | 0.618 | 1.200 |
| occ_strong__win_strong | separated_joint | open_loop | 7.270e+06 | 0.0346 | 1.000 | 1.723 |
| occ_strong__win_strong | separated_joint | kalman | 3.238e+06 | 0.0267 | 1.000 | 0.873 |
| occ_strong__win_strong | separated_staged | open_loop | 7.270e+06 | 0.0346 | 0.697 | 1.197 |
| occ_strong__win_strong | separated_staged | kalman | 3.238e+06 | 0.0267 | 0.754 | 0.999 |

## Open

Whether any procedure or extra is worth shipping in Parameter Estimation is a human decision on this report.

