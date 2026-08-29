"""House-heating wrapper around mbc successive-linearisation MPC."""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from mbc.control import StandardLinearisedContinuousMPC


class HeatingLinearisedMPC(StandardLinearisedContinuousMPC):
    """House-heating wrapper around mbc's :class:`StandardLinearisedContinuousMPC`.

    All optimal control — condensed QP assembly, soft-output corridor slacks,
    absolute-input regularisation, signed-magnitude (bidirectional) price
    slacks, successive linearisation and CD-EKF estimation — is performed by the
    native mbc classes.  This wrapper only adds house-heating glue:

    * **Linearisation point.**  The Jacobians are evaluated at the comfort
      *setpoint* equilibrium — room setpoints, walls at their steady state for
      those setpoints and the current outdoor temperature, and emitter-filter
      states at ``u_eq`` — rather than at the current estimate, so the local
      model stays accurate during transients.  With ``Q = 0`` and the absolute-
      input cost this yields pure zone control (minimum-cost action inside the
      comfort corridor is ``u_abs = 0``).
    * **Horizon translation.**  The per-step / per-room / per-source quantities
      the coordinator produces (disturbance forecast, setpoints, comfort
      offsets, Q/R scales, input bounds and the electricity-price linear cost on
      the absolute draw) are mapped onto the native horizon-profile setters.
    * **Estimate-only.**  :meth:`estimate_only` runs the estimator without
      solving the OCP, for when the controller is stopped.

    The absolute-input quadratic cost (``‖u_abs‖²_R`` rather than ``‖u_dev‖²_R``)
    is supplied natively: :meth:`~StandardLinearisedContinuousMPC.compute` sets
    the OCP ``input_equilibrium`` to ``u_ss`` automatically each solve.
    """

    def step(
        self,
        y: np.ndarray,
        d: np.ndarray,
        p: Optional[np.ndarray] = None,
        t: float = 0.0,
        D_forecast: Optional[np.ndarray] = None,
        x_ref_abs_seq: Optional[np.ndarray] = None,
        offset_seq: Optional[np.ndarray] = None,
        q_scale_seq: Optional[np.ndarray] = None,
        r_scale_seq: Optional[np.ndarray] = None,
        u_min_seq: Optional[np.ndarray] = None,
        u_max_seq: Optional[np.ndarray] = None,
        price_seq: Optional[np.ndarray] = None,
        elec_heat: Optional[np.ndarray] = None,
        elec_cool: Optional[np.ndarray] = None,
        bid_mask: Optional[np.ndarray] = None,
        price_weight: float = 0.0,
        dt_h: float = 0.25,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run one MPC step and return ``(u_abs, U_abs, X_abs)``.

        Parameters mirror the coordinator's trajectory: ``D_forecast`` is the
        absolute disturbance forecast ``(N, nd)``; ``x_ref_abs_seq`` the absolute
        per-room setpoints ``(N, n_rooms)``; ``offset_seq`` the comfort-corridor
        half-widths ``(N, n_rooms)``; ``q_scale_seq``/``r_scale_seq`` per-step
        per-room/per-source weight multipliers; ``u_min_seq``/``u_max_seq`` the
        per-step *absolute* input box; and ``price_seq``/``elec_*``/``bid_mask``/
        ``price_weight``/``dt_h`` the electricity-price linear cost on the
        absolute electrical draw.  ``None`` falls back to the static behaviour
        for that quantity.
        """
        model = self._model
        nz = model.nz
        nu = model.nu
        n = model._n_rooms
        nx_phys = model._nx_phys
        d_now = np.asarray(d, dtype=float).reshape(model.nd)
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)

        # ── Linearisation point: the comfort-setpoint equilibrium ────────────
        # Air states are set to the room setpoints and wall states to their
        # steady-state values, giving a consistent (x_ss, u_ss) equilibrium
        # pair when the setpoint is feasible.
        x_ss = self._x_ref_abs.copy()
        x_ss[n:nx_phys] = model.wall_equilibrium(x_ss[:n], float(d_now[0]))
        u_ss = model.compute_u_eq(x_ss, d_now, p_, t)
        for j in range(nu):
            k_f = model._filter_idx_for_source[j]
            if k_f >= 0:
                x_ss[nx_phys + k_f] = u_ss[j]

        # ── Infeasibility correction ──────────────────────────────────────────
        # When the HP cannot maintain the setpoint at full output, compute_u_eq
        # clips u_ss to u_max and (x_ss=[setpoint], u_ss=u_max) is NOT a true
        # equilibrium: the room actually cools even at max input.  The mbc
        # deviation model assumes equilibrium and predicts stable temperatures,
        # so the MPC never sees the room drop and suppresses actuation.
        # One Newton step on the air-temperature drift finds the achievable
        # equilibrium temperature, making (x_ss_corrected, u_ss) consistent.
        air_drift = model.f(x_ss, u_ss, d_now, p_, t)[:n]
        if np.any(air_drift < -1e-6):
            J = model.dfdx(x_ss, u_ss, d_now, p_, t)
            rho = model._wall_eq_ratio          # (n,) per-room wall-to-air ratio
            F_eff = J[:n, :n] + J[:n, n:2 * n] * rho.reshape(1, -1)
            try:
                delta_air = np.linalg.solve(F_eff, -air_drift)
                x_ss_air_orig = x_ss[:n].copy()
                # Correction moves setpoint down; clip to [T_outdoor, setpoint].
                x_ss[:n] = np.clip(x_ss[:n] + delta_air, float(d_now[0]), x_ss_air_orig)
                x_ss[n:nx_phys] = model.wall_equilibrium(x_ss[:n], float(d_now[0]))
                u_ss = model.compute_u_eq(x_ss, d_now, p_, t)
                for j in range(nu):
                    k_f = model._filter_idx_for_source[j]
                    if k_f >= 0:
                        x_ss[nx_phys + k_f] = u_ss[j]
            except np.linalg.LinAlgError:
                pass

        d_ss = d_now.copy()

        # ── Configure the native horizon profile for this solve ──────────────
        self.clear_horizon_profile()
        self.set_linearisation_point(x_ss, u_ss, d_ss)

        if D_forecast is not None:
            self.set_disturbance_profile(
                np.asarray(D_forecast, dtype=float).reshape(self._N, model.nd)
            )

        if x_ref_abs_seq is not None:
            # Native tracks z = Cz·x against z_ref + dev.  At the setpoint
            # equilibrium the base z_ref is zero, so dev is the per-step setpoint
            # in deviation coordinates relative to the linearisation point.
            dev = np.asarray(x_ref_abs_seq, dtype=float) - x_ss[:nz].reshape(1, -1)
            self.set_output_reference_profile(dev)

        if offset_seq is not None:
            self.set_soft_output_band_half_width_profile(
                np.asarray(offset_seq, dtype=float)
            )
        else:
            # Always set per-room comfort offsets from the model so each room
            # uses its own configured offset rather than the global maximum
            # that was baked in at construction time.
            room_offsets = np.array(
                [
                    float(
                        getattr(self._model._model.rooms[name], "comfort_offset", 2.0)
                        or 2.0
                    )
                    for name in self._model._room_list
                ],
                dtype=float,
            )
            self.set_soft_output_band_half_width_profile(
                np.tile(room_offsets.reshape(1, -1), (self._N, 1))
            )

        if q_scale_seq is not None:
            self.set_output_tracking_weight_scale_profile(
                np.asarray(q_scale_seq, dtype=float)
            )

        if r_scale_seq is not None:
            self.set_input_regularisation_weight_scale_profile(
                np.asarray(r_scale_seq, dtype=float)
            )

        if u_min_seq is not None and u_max_seq is not None:
            # Absolute input-bound profiles; native shifts them into deviation
            # coordinates by u_ss inside compute().
            self.set_input_bound_profiles(
                np.asarray(u_min_seq, dtype=float),
                np.asarray(u_max_seq, dtype=float),
            )

        self._configure_price(
            price_seq, elec_heat, elec_cool, bid_mask, price_weight, dt_h
        )

        u_abs, U_abs, X_abs = self.compute(y, d, p, t)

        # Safety clip the whole trajectory to the physical actuator range as a
        # guard against tiny solver-tolerance excursions (native clips step 0).
        u_min_abs, u_max_abs = model.u_bounds
        U_abs = np.clip(
            U_abs, u_min_abs.reshape(1, -1), u_max_abs.reshape(1, -1)
        )
        u_abs = U_abs[0].copy()
        self._u_prev = u_abs.copy()
        return u_abs, U_abs, X_abs

    def _configure_price(
        self,
        price_seq: Optional[np.ndarray],
        elec_heat: Optional[np.ndarray],
        elec_cool: Optional[np.ndarray],
        bid_mask: Optional[np.ndarray],
        price_weight: float,
        dt_h: float,
    ) -> None:
        """Map the electricity-price cost onto the native input-linear-cost profile.

        The price charges the *absolute* electrical draw (kW × h × €/kWh).
        Non-bidirectional sources are priced directly on their (signed) input:
        heating-only sources pay ``elec_heat`` per ``+u``, cooling-only sources
        pay ``elec_cool`` per ``|u|`` (a negative gradient).  Bidirectional
        sources use the native signed-magnitude decomposition ``u = s⁺ − s⁻``
        with asymmetric coefficients ``elec_heat`` on ``s⁺`` and ``elec_cool`` on
        ``s⁻``, so the effective cost is ``π·c·|u|`` for the active direction.
        """
        if not (price_seq is not None and price_weight > 0.0 and elec_heat is not None):
            return

        model = self._model
        N = self._N
        nu = model.nu
        price = np.maximum(np.asarray(price_seq, dtype=float).reshape(-1), 0.0)
        n_price = len(price)
        elec_heat = np.asarray(elec_heat, dtype=float).reshape(nu)
        elec_cool = (
            np.asarray(elec_cool, dtype=float).reshape(nu)
            if elec_cool is not None
            else elec_heat
        )
        bid = (
            np.asarray(bid_mask, dtype=bool).reshape(nu)
            if bid_mask is not None
            else np.zeros(nu, dtype=bool)
        )
        _, u_max_abs = model.u_bounds

        coeff = np.zeros((N, nu))
        pos = np.zeros((N, nu))
        neg = np.zeros((N, nu))
        for k in range(N):
            p_k = float(price[min(k, n_price - 1)])
            w = price_weight * p_k * dt_h
            for i in range(nu):
                if bid[i]:
                    pos[k, i] = w * elec_heat[i] * 1e-3
                    neg[k, i] = w * elec_cool[i] * 1e-3
                elif float(u_max_abs[i]) > 0.0:
                    coeff[k, i] = w * elec_heat[i] * 1e-3
                else:
                    coeff[k, i] = -w * elec_cool[i] * 1e-3

        bid_idx = np.flatnonzero(bid)
        if bid_idx.size > 0:
            self.set_input_linear_cost_profile(
                coeff,
                signed_magnitude_input_indices=bid_idx,
                positive_slack_coefficient_profile=pos[:, bid_idx],
                negative_slack_coefficient_profile=neg[:, bid_idx],
            )
        else:
            self.set_input_linear_cost_profile(coeff)

    def estimate_only(
        self,
        y: np.ndarray,
        d: np.ndarray,
        p: Optional[np.ndarray] = None,
        t: float = 0.0,
    ) -> np.ndarray:
        """Run the CD-EKF predict+update without solving the OCP.

        Used when the controller is stopped: state estimation and innovation
        logging must keep running so the filtered temperatures stay grounded in
        reality, but the (expensive) MPC optimisation is skipped.

        Calls the estimator's combined step (predict for the nominal Ts then
        measurement update) directly rather than delegating to
        :meth:`~StandardLinearisedContinuousMPC.propagate`.  ``propagate``
        gates the predict phase on ``t - t_last > 0``, but because the full
        optimization path always passes ``t=0.0`` the stored ``_t_last`` is
        permanently 0 — making ``dt`` identically zero and silently dropping
        every predict call.  Without predict, process noise is never added to
        the covariance ``P``: over many cycles ``P → 0``, the Kalman gain
        collapses to zero, and the filter stops tracking the actual
        temperature.  When the system is later re-enabled the first real
        predict step inflates ``P`` and the subsequent update snaps the frozen
        estimate to the current measurement, producing the large discrete jump
        reported by the user.

        Calling ``_estimator.step`` directly guarantees predict always runs
        for exactly ``Ts``, keeping ``P`` at its healthy steady-state value
        and the gain at a level that lets the filter track temperature changes
        throughout the stopped period.

        ``_d_prev`` is advanced to the current disturbance (matching
        ``propagate``'s bookkeeping) so the next cycle's predict uses the
        right disturbance; ``_u_prev`` is left untouched so the coordinator
        can correct it to the actually-delivered input via
        :meth:`HeatingMPCController.notify_applied_u` before the next cycle.

        Returns the filtered state estimate ``x_hat``.
        """
        d_now = np.asarray(d, dtype=float).reshape(self._model.nd)
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)
        y_arr = np.asarray(y, dtype=float).reshape(self._model.nym)
        x_hat, _ = self._estimator.step(y_arr, self._u_prev, self._d_prev, p_, t)
        self._d_prev = d_now.copy()
        return np.asarray(x_hat, dtype=float)

