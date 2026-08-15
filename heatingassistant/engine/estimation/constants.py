"""Estimation constants, bounds, and identifiability thresholds."""

from __future__ import annotations

import math

import numpy as np

from mbc.identification import nelder_mead as _nelder_mead  # for test compatibility

#: Minimum number of history steps before attempting estimation.
#: 60 steps ≈ 1 hour at the default 60 s sampling interval — the
#: minimum needed to see meaningful thermal dynamics.
#: NOTE: This constant was calibrated for 60 s sampling.  At the default
#: 900 s sampling interval the effective bar is 15 hours, which exceeds
#: any realistic identification window.  Instance methods therefore use
#: ``self._min_history_steps`` (computed from ``dt`` in the constructor)
#: instead of this module-level constant.  It is kept for backward
#: compatibility with external importers (e.g. test suites).
MIN_HISTORY_STEPS = 60

#: Minimum wall-clock time [s] of data required before attempting estimation.
#: Corresponds to ≈ 1 hour — enough to observe meaningful thermal dynamics
#: for rooms with time constants of 2–6 hours.
_MIN_HISTORY_TIME_S: float = 3600.0

#: Minimum wall-clock time [s] for a contiguous simulation window to be used
#: in the open-loop MSE objective.  Short windows cannot constrain slow
#: parameters (R_ext, q_int); 20 minutes is a practical lower bound.
_MIN_SEGMENT_TIME_S: float = 1200.0

#: Shared empty integer index array, returned by the per-room open-window
#: bookkeeping when no room is excluded at a step (avoids per-step allocation).
_EMPTY_IDX: np.ndarray = np.array([], dtype=int)

#: Log-space parameter bounds (hard limits).
_LOG_MASS_LO = math.log(1e4)    # ~10 kJ/K
_LOG_MASS_HI = math.log(5e8)    # ~500 MJ/K
_LOG_R_LO = math.log(1e-5)      # 0.00001 K/W
_LOG_R_HI = math.log(10.0)      # 10 K/W

#: Log-space bounds for inter-room resistances.
_LOG_R_IJ_LO = math.log(1e-4)   # 0.0001 K/W (very thin interior partition)
_LOG_R_IJ_HI = math.log(5.0)    # 5 K/W (well-insulated internal wall)

#: Linear bounds for per-room internal heat gain [W].
_Q_INT_LO = -2_000.0   # allow small negative to absorb model bias
_Q_INT_HI =  5_000.0   # large internal source (server room, sauna…)

#: Linear bounds for contact-gated extra outdoor UA [W/K] while a
#: window/door override contact is open.  Cap is a numerical limit, not
#: a physical law (MODEL-pe-contact-ua-occupancy).
_UA_OPEN_LO = 0.0
_UA_OPEN_HI = 50.0
#: Prior std [W/K] for UA_open (MAP toward 0).  Same order as the
#: assumed-UA bake-off guess (15 W/K).
_UA_OPEN_PRIOR_STD = 15.0

#: Log-space bounds for heater power-scale α (multiplicative on max_power).
_LOG_ALPHA_LO = math.log(0.3)   # 30 % of rated
_LOG_ALPHA_HI = math.log(3.0)   # 300 % of rated

#: Relative MAP-prior weight for the heater power-scale α when heater duty
#: cycle variation is weak (unexcited / constant-on).  A heater's rated
#: power is usually known to within ~±20 %, so α carries real prior
#: information and this weight keeps the joint optimum off the documented
#: "C huge / R huge" degenerate ridge.
_ALPHA_PRIOR_WEIGHT = 25.0

#: Reduced α prior weight when duty-cycle excitation is strong enough that
#: the data can identify the scale without being pinned to the rated value.
#: Underestimating heater influence was traced to this prior dominating the
#: open-loop MSE gradient on excited windows.
_ALPHA_PRIOR_WEIGHT_EXCITED = 4.0

#: Minimum std of inter-room temperature difference for R_ij identifiability.
_MIN_TEMP_DIFF_STD = 0.3   # °C

#: Minimum std of source duty-cycle for α_s identifiability.
_MIN_HEATER_USAGE_STD = 0.05

#: Minimum std of a room's recorded solar gain [W] for the per-room solar
#: scale s_i to be identifiable.  A room with no windows / aperture (or a
#: window covering only the night hours) carries no solar information.
_MIN_SOLAR_STD = 30.0

#: Log-space bounds for the per-room solar-gain scale s_i.
_LOG_SOLAR_LO = math.log(0.2)   # heavy unmodelled shading
_LOG_SOLAR_HI = math.log(3.0)   # preset badly underestimates the aperture

#: Linear-space bounds for the 2R2C envelope split fractions.  Kept inside
#: the hard clips in ``thermal_model.Room`` so the estimator can never
#: construct a degenerate room.
_C_AIR_LO, _C_AIR_HI = 0.02, 0.60
_R_AW_LO, _R_AW_HI = 0.02, 0.90

#: Prior standard deviation for the split fractions (linear space).  The
#: splits are deliberately held on a tight leash: they are only weakly
#: identified, and the typology defaults are decent.  The data must carry
#: real multi-hour excitation to move them.
_SPLIT_PRIOR_STD = 0.1

#: Linear-space bounds for the wall-envelope initial temperature [°C].
_T_WALL_LO = -30.0
_T_WALL_HI =  60.0

#: Prior standard deviation for the wall initial temperature [°C].  A
#: 5 °C width says "the wall probably started close to the measured air
#: temperature, but could be a few degrees off."  The prior mean is set
#: to the first measured air temperature at estimation time.
_T_WALL_PRIOR_STD = 5.0

#: Minimum regularisation weight for the wall initial temperature.  With
#: default regularisation ≤ 0.01 the Gaussian prior on t_wall_init is too
#: weak to keep the parameter in a physically plausible range: the MSE
#: gradient (~4 per window) dwarfs the prior gradient (0.008 at 10 °C off),
#: and the optimiser drives t_wall to ±60 °C, corrupting every other
#: gradient.  A floor of 10 limits the maximum drift from the prior to
#: ~±5 °C while still being negligibly small compared to the strong
#: (1e6) regularisation used in validation tests.
_T_WALL_MIN_LAM = 10.0

#: Number of random restarts in multistart Nelder–Mead.
_N_RESTARTS = 3

#: Standard deviation of the random log-space perturbation between restarts.
_RESTART_PERT = 0.5
