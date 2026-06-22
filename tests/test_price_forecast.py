"""Tests for coordinator._async_read_price_forecast and electricity_price module.

Covers:
  - Path A: timestamped dicts (Nord Pool HACS v2 ``raw_today``/``raw_tomorrow``)
  - Path B: plain hourly/quarterly lists (Nord Pool v1 / EDS / generic)
  - EDS: ``hour``+``price`` raw format, quarterly resolution, Carnot ``forecast``
  - Fallback: sensor state only (constant persistence)

The sub-hourly alignment fix is verified explicitly: at 14:45 with dt=15 min
the price for hour 15 must start at step k=1, not step k=4.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant import electricity_price as ep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A fixed wall-clock "now" used throughout: 2024-06-05 14:45:00 UTC
# (local hour 14, 45 minutes into the hour)
UTC = timezone.utc
NOW = datetime(2024, 6, 5, 14, 45, 0, tzinfo=UTC)


def _make_coordinator(
    entity_id: str,
    attributes: dict,
    state_str: str = "0.5",
    *,
    horizon: int = 8,
    dt_s: int = 900,  # 15 min → 4 steps per hour
    net_tariff: float = 0.0,
    surcharge: float = 0.0,
) -> HeatingAssistantCoordinator:
    """Build a bare coordinator with only the attributes needed for price reading."""
    coord = object.__new__(HeatingAssistantCoordinator)
    coord._price_entity = entity_id
    coord._price_net_tariff = net_tariff
    coord._price_spot_surcharge = surcharge
    coord._horizon = horizon
    coord._update_interval_s = dt_s  # backs the ``dt`` property

    hass = MagicMock()
    hass.states.get = lambda eid: (
        SimpleNamespace(state=state_str, attributes=attributes)
        if eid == entity_id
        else None
    )
    coord.hass = hass
    return coord


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# No entity / unavailable
# ---------------------------------------------------------------------------


class TestNoEntity:
    def test_returns_none_when_no_entity_configured(self):
        coord = _make_coordinator(None, {})
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is None

    def test_returns_none_when_entity_unavailable(self):
        coord = object.__new__(HeatingAssistantCoordinator)
        coord._price_entity = "sensor.price"
        coord._price_net_tariff = 0.0
        coord._price_spot_surcharge = 0.0
        coord._horizon = 4
        coord._update_interval_s = 900

        hass = MagicMock()
        hass.states.get = lambda _: SimpleNamespace(state="unavailable", attributes={})
        coord.hass = hass

        result = _run(coord._async_read_price_forecast(NOW))
        assert result is None

    def test_returns_none_when_entity_unknown(self):
        coord = object.__new__(HeatingAssistantCoordinator)
        coord._price_entity = "sensor.price"
        coord._price_net_tariff = 0.0
        coord._price_spot_surcharge = 0.0
        coord._horizon = 4
        coord._update_interval_s = 900

        hass = MagicMock()
        hass.states.get = lambda _: SimpleNamespace(state="unknown", attributes={})
        coord.hass = hass

        result = _run(coord._async_read_price_forecast(NOW))
        assert result is None

    def test_returns_none_when_state_not_found(self):
        coord = object.__new__(HeatingAssistantCoordinator)
        coord._price_entity = "sensor.price"
        coord._price_net_tariff = 0.0
        coord._price_spot_surcharge = 0.0
        coord._horizon = 4
        coord._update_interval_s = 900

        hass = MagicMock()
        hass.states.get = lambda _: None
        coord.hass = hass

        result = _run(coord._async_read_price_forecast(NOW))
        assert result is None


# ---------------------------------------------------------------------------
# Path A – raw_today / raw_tomorrow (Nord Pool HACS v2)
# ---------------------------------------------------------------------------


def _raw_entry(hour: int, value: float, date: str = "2024-06-05") -> dict:
    """Build a Nord Pool v2 raw entry for the given hour."""
    start = f"{date}T{hour:02d}:00:00+00:00"
    end = f"{date}T{hour + 1:02d}:00:00+00:00" if hour < 23 else f"{date}T23:59:59+00:00"
    return {"start": start, "end": end, "value": value}


class TestPathA:
    """raw_today / raw_tomorrow with explicit start timestamps."""

    def test_basic_alignment_picks_correct_hourly_price(self):
        # NOW = 14:45.  Steps are 15 min each.
        # Step k=0 → 14:45 → price[14]
        # Step k=1 → 15:00 → price[15]
        prices = {h: float(h) / 10 for h in range(24)}
        raw_today = [_raw_entry(h, prices[h]) for h in range(24)]
        coord = _make_coordinator("sensor.price", {"raw_today": raw_today})

        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        assert len(result) == 8
        # k=0: 14:45 → hour 14
        assert result[0] == pytest.approx(prices[14])
        # k=1: 15:00 → hour 15 (not hour 14!)
        assert result[1] == pytest.approx(prices[15])
        # k=2: 15:15 → still hour 15
        assert result[2] == pytest.approx(prices[15])
        # k=5: 16:00 → hour 16
        assert result[5] == pytest.approx(prices[16])

    def test_hour_boundary_transition_is_exact(self):
        """Price must step at the exact boundary, not steps_per_hour late."""
        # NOW = 14:45, dt=15min.  At k=1 (15:00 exact) price should be p[15].
        raw_today = [_raw_entry(h, float(h)) for h in range(24)]
        coord = _make_coordinator("sensor.price", {"raw_today": raw_today}, dt_s=900)

        result = _run(coord._async_read_price_forecast(NOW))
        # Previously the bug would return price[14] for k=0..3 (all 4 steps in
        # the hour) and only switch to price[15] at k=4.
        assert result[0] == pytest.approx(14.0)   # k=0: 14:45
        assert result[1] == pytest.approx(15.0)   # k=1: 15:00 ← must be 15, not 14
        assert result[2] == pytest.approx(15.0)   # k=2: 15:15
        assert result[3] == pytest.approx(15.0)   # k=3: 15:30

    def test_uses_raw_tomorrow_for_horizon_beyond_midnight(self):
        # NOW = 23:45 (UTC).  Steps span midnight into tomorrow.
        now_late = datetime(2024, 6, 5, 23, 45, 0, tzinfo=UTC)
        raw_today = [_raw_entry(h, 1.0) for h in range(24)]
        raw_tomorrow = [
            {"start": f"2024-06-06T{h:02d}:00:00+00:00",
             "end": f"2024-06-06T{h + 1:02d}:00:00+00:00",
             "value": 2.0}
            for h in range(24)
        ]
        coord = _make_coordinator(
            "sensor.price",
            {"raw_today": raw_today, "raw_tomorrow": raw_tomorrow},
            horizon=8,
        )
        result = _run(coord._async_read_price_forecast(now_late))
        assert result is not None
        # k=0: 23:45 → today price 1.0
        assert result[0] == pytest.approx(1.0)
        # k=1: 00:00 → tomorrow price 2.0
        assert result[1] == pytest.approx(2.0)

    def test_adder_applied_to_raw_today_prices(self):
        raw_today = [_raw_entry(h, 0.5) for h in range(24)]
        coord = _make_coordinator(
            "sensor.price",
            {"raw_today": raw_today},
            net_tariff=0.1,
            surcharge=0.05,
        )
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        assert all(abs(v - 0.65) < 1e-9 for v in result)

    def test_negative_raw_price_clamped_to_zero(self):
        raw_today = [_raw_entry(h, -0.3) for h in range(24)]
        coord = _make_coordinator("sensor.price", {"raw_today": raw_today})
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        assert all(v == 0.0 for v in result)

    def test_malformed_entries_skipped(self):
        raw_today = [
            {"start": "not-a-date", "value": 1.0},   # bad start
            {"start": "2024-06-05T14:00:00+00:00"},   # missing value
            _raw_entry(14, 0.5),                       # good
        ]
        coord = _make_coordinator("sensor.price", {"raw_today": raw_today})
        result = _run(coord._async_read_price_forecast(NOW))
        # Only the valid entry is used; result should still be a list.
        assert result is not None
        assert len(result) == coord._horizon

    def test_raw_entry_value_key_alternatives(self):
        """price and total keys should be accepted in addition to value."""
        raw_today_price = [
            {"start": f"2024-06-05T{h:02d}:00:00+00:00", "price": 0.7}
            for h in range(24)
        ]
        raw_today_total = [
            {"start": f"2024-06-05T{h:02d}:00:00+00:00", "total": 0.8}
            for h in range(24)
        ]
        for attrs in (
            {"raw_today": raw_today_price},
            {"raw_today": raw_today_total},
        ):
            coord = _make_coordinator("sensor.price", attrs)
            result = _run(coord._async_read_price_forecast(NOW))
            assert result is not None
            assert len(result) == coord._horizon

    def test_only_raw_tomorrow_present(self):
        """raw_tomorrow alone (edge case: day roll at midnight) should work."""
        raw_tomorrow = [
            {"start": f"2024-06-06T{h:02d}:00:00+00:00", "value": float(h)}
            for h in range(24)
        ]
        coord = _make_coordinator("sensor.price", {"raw_tomorrow": raw_tomorrow})
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        assert len(result) == coord._horizon


# ---------------------------------------------------------------------------
# Path B – plain hourly lists (today / tomorrow)
# ---------------------------------------------------------------------------


class TestPathB:
    """today / tomorrow plain list format with elapsed-steps correction."""

    def _make_24h_prices(self, base: float = 0.0) -> list:
        return [base + h * 0.01 for h in range(24)]

    def test_basic_alignment_uses_current_hour(self):
        # NOW = 14:45 → current hour 14
        prices = self._make_24h_prices()
        coord = _make_coordinator("sensor.price", {"today": prices})
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        assert result[0] == pytest.approx(prices[14])

    def test_elapsed_steps_correction_at_quarter_past(self):
        """At 14:45 (3 steps into hour with dt=15 min) step k=1 must be hour 15."""
        prices = [float(h) for h in range(48)]  # today + tomorrow coverage
        coord = _make_coordinator(
            "sensor.price",
            {"today": prices[:24], "tomorrow": prices[24:]},
            dt_s=900,
        )
        result = _run(coord._async_read_price_forecast(NOW))
        # k=0: 14:45 → hour 14 (elapsed_steps=3, (3+0)//4 = 0 → +14 = 14)
        assert result[0] == pytest.approx(14.0)
        # k=1: 15:00 → hour 15 (elapsed_steps=3, (3+1)//4 = 1 → +14 = 15)
        assert result[1] == pytest.approx(15.0)
        # k=2: 15:15 → still hour 15
        assert result[2] == pytest.approx(15.0)
        # k=4: 15:45 → still hour 15 (elapsed_steps=3, (3+4)//4=1 → +14=15)
        assert result[4] == pytest.approx(15.0)
        # k=5: 16:00 → hour 16 (elapsed_steps=3, (3+5)//4=2 → +14=16)
        assert result[5] == pytest.approx(16.0)

    def test_elapsed_steps_at_top_of_hour(self):
        """At exactly :00 elapsed_steps=0 so all first-4 steps stay in hour 14."""
        now_exact = datetime(2024, 6, 5, 14, 0, 0, tzinfo=UTC)
        prices = [float(h) for h in range(24)]
        coord = _make_coordinator("sensor.price", {"today": prices}, dt_s=900)
        result = _run(coord._async_read_price_forecast(now_exact))
        # k=0..3: hour 14; k=4: hour 15
        assert result[0] == pytest.approx(14.0)
        assert result[3] == pytest.approx(14.0)
        assert result[4] == pytest.approx(15.0)

    def test_prices_today_alias_works(self):
        prices = self._make_24h_prices(0.3)
        coord = _make_coordinator("sensor.price", {"prices_today": prices})
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        assert result[0] == pytest.approx(prices[14])

    def test_tomorrow_prices_used_when_horizon_extends_past_midnight(self):
        now_late = datetime(2024, 6, 5, 23, 45, 0, tzinfo=UTC)
        today_prices = [1.0] * 24
        tomorrow_prices = [2.0] * 24
        coord = _make_coordinator(
            "sensor.price",
            {"today": today_prices, "tomorrow": tomorrow_prices},
            horizon=8,
            dt_s=900,
        )
        result = _run(coord._async_read_price_forecast(now_late))
        assert result is not None
        # k=0: 23:45 → hour 23 → 1.0
        assert result[0] == pytest.approx(1.0)
        # k=1: 00:00 → hour 24 → tomorrow[0] = 2.0
        assert result[1] == pytest.approx(2.0)

    def test_dict_entries_with_value_key(self):
        # Use h+1 to avoid h=0 (value=0.0) being skipped by the `or` chain.
        prices = [{"value": float(h + 1) * 0.1} for h in range(24)]
        coord = _make_coordinator("sensor.price", {"today": prices})
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        # current_hour=14, elapsed_steps=3, k=0: hour_idx=14 → prices[14]=1.5
        assert result[0] == pytest.approx(prices[14]["value"])

    def test_adder_applied(self):
        prices = [0.5] * 24
        coord = _make_coordinator(
            "sensor.price",
            {"today": prices},
            net_tariff=0.1,
            surcharge=0.05,
        )
        result = _run(coord._async_read_price_forecast(NOW))
        assert all(abs(v - 0.65) < 1e-9 for v in result)

    def test_last_price_used_when_horizon_exceeds_data(self):
        # Only today prices (24 h) and very long horizon
        prices = [float(h) for h in range(24)]
        coord = _make_coordinator("sensor.price", {"today": prices}, horizon=200)
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        assert len(result) == 200
        # After hour 23 the last price (23.0) should be repeated
        assert result[-1] == pytest.approx(23.0)


# ---------------------------------------------------------------------------
# Fallback – sensor state only
# ---------------------------------------------------------------------------


class TestFallback:
    def test_flat_persistence_when_no_list_attributes(self):
        coord = _make_coordinator(
            "sensor.price", {}, state_str="0.42", horizon=6
        )
        result = _run(coord._async_read_price_forecast(NOW))
        assert result == [pytest.approx(0.42)] * 6

    def test_adder_applied_to_fallback(self):
        coord = _make_coordinator(
            "sensor.price", {},
            state_str="0.5",
            net_tariff=0.1,
            surcharge=0.05,
            horizon=4,
        )
        result = _run(coord._async_read_price_forecast(NOW))
        assert result == [pytest.approx(0.65)] * 4

    def test_returns_none_when_state_not_parseable(self):
        coord = _make_coordinator(
            "sensor.price", {}, state_str="n/a", horizon=4
        )
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is None

    def test_fallback_negative_clamped_to_zero(self):
        coord = _make_coordinator(
            "sensor.price", {}, state_str="-0.1", horizon=4
        )
        result = _run(coord._async_read_price_forecast(NOW))
        assert result == [0.0] * 4


# ---------------------------------------------------------------------------
# Path A takes priority over Path B
# ---------------------------------------------------------------------------


class TestPathPriority:
    def test_raw_today_takes_precedence_over_today(self):
        """When both raw_today and today are present, raw_today wins at lookup."""
        raw_today = [_raw_entry(h, 99.0) for h in range(24)]
        today = [0.01] * 24
        coord = _make_coordinator(
            "sensor.price",
            {"raw_today": raw_today, "today": today},
        )
        result = _run(coord._async_read_price_forecast(NOW))
        # Should use raw_today (99.0), not today (0.01)
        assert result is not None
        assert result[0] == pytest.approx(99.0)


# ---------------------------------------------------------------------------
# Energi Data Service (EDS) formats
# ---------------------------------------------------------------------------


def _eds_raw_entry(
    hour: int,
    price: float,
    date: str = "2024-06-05",
    minute: int = 0,
) -> dict:
    """Build an EDS-style raw entry using the ``hour`` + ``price`` keys."""
    return {
        "hour": f"{date}T{hour:02d}:{minute:02d}:00+00:00",
        "price": price,
    }


class TestEDS:
    """Energi Data Service HACS integration attribute formats."""

    def test_eds_raw_hour_key_alignment(self):
        """EDS raw_today uses ``hour`` not ``start`` — must still align."""
        raw_today = [_eds_raw_entry(h, float(h)) for h in range(24)]
        coord = _make_coordinator("sensor.price", {"raw_today": raw_today})
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        assert result[0] == pytest.approx(14.0)   # k=0: 14:45 → hour 14
        assert result[1] == pytest.approx(15.0)   # k=1: 15:00 → hour 15

    def test_eds_quarterly_raw_resolution(self):
        """96-entry quarterly raw_today gives distinct 15-min prices."""
        raw_today = []
        for h in range(24):
            for q in range(4):
                price = h * 10.0 + q
                raw_today.append(_eds_raw_entry(h, price, minute=q * 15))
        coord = _make_coordinator("sensor.price", {"raw_today": raw_today}, dt_s=900)
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        # NOW = 14:45 → quarter 3 of hour 14 → price 14*10+3 = 143
        assert result[0] == pytest.approx(143.0)
        # k=1: 15:00 → hour 15 quarter 0 → 150
        assert result[1] == pytest.approx(150.0)

    def test_eds_quarterly_today_list(self):
        """96-element ``today`` list is treated as 15-min intervals."""
        prices = [float(i) for i in range(96)]
        coord = _make_coordinator("sensor.price", {"today": prices}, dt_s=900)
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        # 14:45 = index 14*4+3 = 59
        assert result[0] == pytest.approx(59.0)
        # 15:00 = index 60
        assert result[1] == pytest.approx(60.0)

    def test_forecast_fills_tomorrow_before_known_prices(self):
        """When tomorrow is empty, forecast supplies tomorrow's prices."""
        raw_today = [_eds_raw_entry(h, 1.0) for h in range(24)]
        forecast = [
            _eds_raw_entry(h, 5.0, date="2024-06-05") for h in range(24)
        ] + [
            _eds_raw_entry(h, 9.0, date="2024-06-06") for h in range(24)
        ]
        now_late = datetime(2024, 6, 5, 23, 45, 0, tzinfo=UTC)
        coord = _make_coordinator(
            "sensor.price",
            {"raw_today": raw_today, "tomorrow": [], "forecast": forecast},
            horizon=8,
        )
        result = _run(coord._async_read_price_forecast(now_late))
        assert result is not None
        assert result[0] == pytest.approx(1.0)   # k=0: 23:45 today
        assert result[1] == pytest.approx(9.0)   # k=1: 00:00 tomorrow from forecast

    def test_known_tomorrow_overrides_forecast(self):
        """Known tomorrow prices beat forecast predictions (same priority tier)."""
        raw_today = [_eds_raw_entry(h, 1.0) for h in range(24)]
        raw_tomorrow = [
            _eds_raw_entry(h, 2.0, date="2024-06-06") for h in range(24)
        ]
        forecast = [
            _eds_raw_entry(h, 5.0, date="2024-06-05") for h in range(24)
        ] + [
            _eds_raw_entry(h, 9.0, date="2024-06-06") for h in range(24)
        ]
        now_late = datetime(2024, 6, 5, 23, 45, 0, tzinfo=UTC)
        coord = _make_coordinator(
            "sensor.price",
            {
                "raw_today": raw_today,
                "raw_tomorrow": raw_tomorrow,
                "forecast": forecast,
            },
            horizon=8,
        )
        result = _run(coord._async_read_price_forecast(now_late))
        assert result is not None
        assert result[1] == pytest.approx(2.0)   # known tomorrow, not forecast 9.0

    def test_forecast_extends_beyond_tomorrow(self):
        """Five-day forecast fills horizon beyond today+tomorrow."""
        raw_today = [_eds_raw_entry(h, 1.0, date="2024-06-06") for h in range(24)]
        raw_tomorrow = [
            _eds_raw_entry(h, 2.0, date="2024-06-07") for h in range(24)
        ]
        forecast = [
            _eds_raw_entry(h, 1.0, date="2024-06-06") for h in range(24)
        ] + [
            _eds_raw_entry(h, 2.0, date="2024-06-07") for h in range(24)
        ] + [
            _eds_raw_entry(h, 7.0, date="2024-06-08") for h in range(24)
        ]
        now_mid = datetime(2024, 6, 6, 10, 0, 0, tzinfo=UTC)
        coord = _make_coordinator(
            "sensor.price",
            {
                "raw_today": raw_today,
                "raw_tomorrow": raw_tomorrow,
                "forecast": forecast,
            },
            horizon=49,
            dt_s=3600,
        )
        result = _run(coord._async_read_price_forecast(now_mid))
        assert result is not None
        # k=24 → 2024-06-07 10:00 (known tomorrow) → 2.0
        assert result[24] == pytest.approx(2.0)
        # k=48 → 2024-06-08 10:00 (forecast only) → 7.0
        assert result[48] == pytest.approx(7.0)

    def test_quarterly_raw_beats_hourly_today_list(self):
        """Sub-hourly raw entries override coarser hourly today list."""
        raw_today = [
            _eds_raw_entry(14, 99.0, minute=45),
        ]
        today = [1.0] * 24
        coord = _make_coordinator("sensor.price", {"raw_today": raw_today, "today": today})
        result = _run(coord._async_read_price_forecast(NOW))
        assert result is not None
        # At 14:45 the quarterly raw entry at 14:45 (99.0) beats hourly at 14:00 (1.0)
        assert result[0] == pytest.approx(99.0)


class TestElectricityPriceModule:
    """Direct unit tests for the electricity_price helpers."""

    def test_infer_list_interval_seconds(self):
        assert ep.infer_list_interval_seconds(24) == 3600.0
        assert ep.infer_list_interval_seconds(96) == 900.0
        assert ep.infer_list_interval_seconds(48) == 1800.0

    def test_collect_price_series_merges_all_sources(self):
        attrs = {
            "raw_today": [_eds_raw_entry(0, 1.0)],
            "forecast": [_eds_raw_entry(0, 9.0)],
        }
        series = ep.collect_price_series(attrs, NOW)
        priorities = {tp[2] for tp in series}
        sources = {tp[3] for tp in series}
        assert ep._PRIORITY_KNOWN in priorities
        assert ep._PRIORITY_FORECAST in priorities
        assert ep._SOURCE_TODAY in sources
        assert ep._SOURCE_FORECAST in sources
