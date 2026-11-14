"""SWD-596: control log v2 and read-only /api/export."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import tracemalloc
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heatingassistant.app.control_log import (
    ControlLogStore,
    parse_export_query,
    plan_status,
    retention_from_options,
)
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from tests.test_app_http import app_server

pytestmark = pytest.mark.unit

_TS = 900
_COPENHAGEN = ZoneInfo("Europe/Copenhagen")


def _tick(t: float, **extra: object) -> dict:
    record = {
        "kind": "tick",
        "origin": "control_log",
        "t": t,
        "dt_actual": _TS,
        "d_outdoor": 5.0,
        "rooms": {
            "living_room": {
                "y_fused": 21.0,
                "x_hat": 20.5,
                "q_model_W": 1000.0,
            }
        },
        "sources": {"heater": {"u_applied": 0.2}},
        "app_version": "2026.11.0",
    }
    record.update(extra)
    return record


def test_retention_defaults_and_override() -> None:
    assert retention_from_options(None) == {"tick": 400, "plan": 60, "params": 400}
    assert retention_from_options({"control_log_retention_days": 30}) == {
        "tick": 30,
        "plan": 30,
        "params": 30,
    }
    assert retention_from_options(
        {"control_log_retention_days": {"tick": 10, "plan": 4, "params": 10}}
    ) == {"tick": 10, "plan": 4, "params": 10}


def test_plan_status_labels() -> None:
    assert plan_status(result={"accepted": True}, applied=True, watchdog_before=False, watchdog_after=False) == "accepted"
    assert plan_status(result={"accepted": False}, applied=False, watchdog_before=False, watchdog_after=False) == "rejected"
    assert plan_status(result={"timed_out": True}, applied=False, watchdog_before=False, watchdog_after=True) == "timeout"
    assert plan_status(result={"accepted": False}, applied=False, watchdog_before=False, watchdog_after=True) == "watchdog"


def test_one_tick_per_step_over_24h(tmp_path: Path) -> None:
    store = ControlLogStore(tmp_path, "house")
    store.setup()
    start = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
    for step in range(24 * 3600 // _TS):
        store.append(_tick(start + step * _TS, dt_actual=None if step == 0 else _TS))
    rows = list(store.iter_records("tick", start, start + 24 * 3600))
    assert len(rows) == 96
    assert rows[0]["dt_actual"] is None
    assert rows[1]["dt_actual"] == _TS
    assert all(row["schema"] == 2 for row in rows)


def test_restart_keeps_exportable_rows(tmp_path: Path) -> None:
    store = ControlLogStore(tmp_path, "house")
    store.setup()
    stamp = datetime(2026, 6, 1, 12, tzinfo=timezone.utc).timestamp()
    store.append(_tick(stamp, dt_actual=None))
    restarted = ControlLogStore(tmp_path, "house")
    restarted.setup()
    rows = list(restarted.iter_records("tick", stamp - 1, stamp + 1))
    assert len(rows) == 1
    assert rows[0]["rooms"]["living_room"]["x_hat"] == 20.5
    assert restarted.last_t("tick") == pytest.approx(stamp)


def test_gzip_rollover_and_retention_leave_id_history(tmp_path: Path) -> None:
    store = ControlLogStore(tmp_path, "house")
    store.setup()
    today = date(2026, 11, 14)
    tick_old = datetime(2026, 11, 14, tzinfo=timezone.utc).timestamp() - 401 * 86400
    tick_keep = datetime(2026, 11, 12, tzinfo=timezone.utc).timestamp()
    plan_old = datetime(2026, 11, 14, tzinfo=timezone.utc).timestamp() - 61 * 86400
    plan_keep = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
    store.append(_tick(tick_keep))
    store.append({"kind": "plan", "t": plan_keep, "status": "accepted", "plan_id": "keep"})
    store.append(_tick(tick_old))
    store.append({"kind": "plan", "t": plan_old, "status": "rejected", "plan_id": "drop"})
    store.maintenance(today)
    names = sorted(path.name for path in store.directory.iterdir())
    assert any(name.startswith("2026-11-12.tick.jsonl.gz") for name in names)
    assert not any("tick.jsonl" in name and not name.endswith(".gz") for name in names if name.startswith("2026-11-12"))
    assert not any(name.startswith("2025-10-09") or "2025-10" in name for name in names)
    gz = next(store.directory.glob("2026-11-12.tick.jsonl.gz"))
    kept = list(store.iter_records("tick", tick_keep - 10, tick_keep + 10))
    assert len(kept) == 1
    assert gz.is_file()
    plans = list(store.iter_records("plan", plan_keep - 10, plan_keep + 10))
    assert [row["plan_id"] for row in plans] == ["keep"]
    assert list(store.iter_records("plan", plan_old - 10, plan_old + 10)) == []
    id_dir = tmp_path / "id_history" / "house"
    id_dir.mkdir(parents=True)
    id_file = id_dir / "2020-01-01.jsonl"
    id_file.write_text('{"timestamp":1}\n', encoding="utf-8")
    store.maintenance(today)
    assert id_file.read_text(encoding="utf-8") == '{"timestamp":1}\n'


def test_export_schema_headers_resolution_dst_and_empty(tmp_path: Path) -> None:
    store = ControlLogStore(tmp_path, "house")
    store.setup()
    rooms = [("living_room", "Living Room")]
    sources = [("heater", "heater")]
    spring_before = datetime(2026, 3, 29, 1, 30, tzinfo=_COPENHAGEN).timestamp()
    spring_after = datetime(2026, 3, 29, 3, 30, tzinfo=_COPENHAGEN).timestamp()
    fall_cest = datetime(2026, 10, 25, 2, 30, tzinfo=timezone(timedelta(hours=2))).timestamp()
    fall_cet = datetime(2026, 10, 25, 2, 30, tzinfo=timezone(timedelta(hours=1))).timestamp()
    for stamp in (spring_before, spring_after, fall_cest, fall_cet):
        store.append(_tick(stamp, dt_actual=1.0))
    schema = __import__(
        "heatingassistant.app.control_log", fromlist=["export_schema"]
    ).export_schema(rooms=rooms, sources=sources)
    headers = schema["kinds"]["tick"]["csv_headers"]
    spec = parse_export_query(
        {
            "start": ["2026-03-29T00:30:00+01:00"],
            "end": ["2026-03-29T04:30:00+02:00"],
            "kind": ["tick"],
            "format": ["csv"],
            "resolution": ["raw"],
        }
    )
    lines = list(store.iter_export(spec, rooms=rooms, sources=sources))
    assert lines[0].rstrip("\n").split(",") == headers
    body = list(csv.DictReader(io.StringIO("".join(lines))))
    assert len(body) == 2
    assert body[0]["living_room_x_hat_degC"] == "20.5"

    fall = parse_export_query(
        {
            "start": ["2026-10-25T02:00:00+02:00"],
            "end": ["2026-10-25T03:00:00+01:00"],
            "kind": ["tick"],
            "format": ["jsonl"],
        }
    )
    fall_rows = [
        json.loads(line)
        for line in store.iter_export(fall, rooms=rooms, sources=sources)
    ]
    assert len(fall_rows) == 2
    assert fall_rows[0]["t"] != fall_rows[1]["t"]

    empty = parse_export_query(
        {
            "start": ["2026-01-01T00:00:00Z"],
            "end": ["2026-01-01T00:00:00Z"],
            "kind": ["tick"],
            "format": ["csv"],
        }
    )
    # Equal endpoints include a row only when one exists. This window has none.
    empty_lines = list(store.iter_export(empty, rooms=rooms, sources=sources))
    assert len(empty_lines) == 1
    assert empty_lines[0].startswith("t_s,")

    inverted = parse_export_query(
        {"start": ["2026-02-02T00:00:00Z"], "end": ["2026-02-01T00:00:00Z"], "format": ["jsonl"]}
    )
    assert list(store.iter_export(inverted, rooms=rooms, sources=sources)) == []

    hourly = parse_export_query(
        {
            "start": ["2026-03-29T00:00:00Z"],
            "end": ["2026-03-29T06:00:00Z"],
            "resolution": ["1h"],
            "format": ["jsonl"],
        }
    )
    hour_rows = [json.loads(line) for line in store.iter_export(hourly, rooms=rooms, sources=sources)]
    assert hour_rows
    assert all(int(row["t"]) % 3600 == 0 for row in hour_rows)

    with pytest.raises(ValueError, match="retention"):
        parse_export_query(
            {
                "start": ["2024-01-01T00:00:00Z"],
                "end": ["2026-01-01T00:00:00Z"],
                "kind": ["tick"],
            }
        )


def test_backfill_from_id_history_without_touching_it(tmp_path: Path) -> None:
    store = ControlLogStore(tmp_path, "house")
    store.setup()
    id_dir = tmp_path / "id_history" / "house"
    id_dir.mkdir(parents=True)
    stamp = datetime(2026, 4, 2, 10, tzinfo=timezone.utc).timestamp()
    id_dir.joinpath("2026-04-02.jsonl").write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "y": [19.5],
                "u": [0.1],
                "d_outdoor": 3.0,
                "d_solar": {"Living Room": 12.0},
                "y_pred": [19.4],
                "window_open": {"Living Room": False},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    later = stamp + 3600
    store.append(_tick(later))
    spec = parse_export_query(
        {
            "start": ["2026-04-02T00:00:00Z"],
            "end": ["2026-04-03T00:00:00Z"],
            "kind": ["tick"],
            "format": ["jsonl"],
            "room": ["living_room"],
        }
    )
    rows = [
        json.loads(line)
        for line in store.iter_export(
            spec,
            rooms=[("living_room", "Living Room")],
            sources=[("heater", "heater")],
            id_history_dir=id_dir,
        )
    ]
    assert [row["origin"] for row in rows] == ["id_history", "control_log"]
    assert rows[0]["rooms"]["living_room"]["y_fused"] == 19.5
    assert rows[0]["rooms"]["living_room"]["x_hat"] is None
    assert rows[0]["d_outdoor"] == 3.0
    assert id_dir.joinpath("2026-04-02.jsonl").is_file()


def test_seven_day_export_streams_in_bounded_memory(tmp_path: Path) -> None:
    store = ControlLogStore(tmp_path, "house")
    store.setup()
    start = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()
    # Enough bytes that loading the export would show up, few enough to stay fast.
    steps = 7 * 24 * 30
    pad = "x" * 240
    for step in range(steps):
        store.append(_tick(start + step * 120, pad=pad))
    spec = parse_export_query(
        {
            "start": ["2026-05-01T00:00:00Z"],
            "end": ["2026-05-08T00:00:00Z"],
            "kind": ["tick"],
            "format": ["jsonl"],
            "resolution": ["raw"],
        },
        retention_days={"tick": 400, "plan": 60, "params": 400},
    )
    lines = store.iter_export(
        spec,
        rooms=[("living_room", "Living Room")],
        sources=[("heater", "heater")],
    )
    assert hasattr(lines, "__next__")
    tracemalloc.start()
    total = 0
    count = 0
    for line in lines:
        total += len(line)
        count += 1
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count == steps
    assert total > 1_000_000, total
    # The generator must not hold the exported body. Peak stays well under the bytes yielded.
    assert peak < total / 4, (peak, total)


def test_meter_power_from_coarse_energy_counter(tmp_path: Path) -> None:
    store = ControlLogStore(tmp_path, "house")
    store.setup()
    assert store.note_meter(1_000.0, None) is None
    assert store.note_meter(1_000.0, 10.0) is None
    assert store.note_meter(1_000.0 + 600, 10.0) is None
    power = store.note_meter(1_000.0 + 3600, 10.1)
    assert power == pytest.approx(100.0)
    restarted = ControlLogStore(tmp_path, "house")
    restarted.setup()
    assert restarted.note_meter(1_000.0 + 7200, 10.2) == pytest.approx(100.0)


def test_runtime_replay_logs_dt_actual_and_survives_log_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1_700_000_000.0}
    monkeypatch.setattr(
        "heatingassistant.app.runtime.time.time",
        lambda: clock["t"],
    )
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "house",
            "system_enabled": False,
            "rooms": [{"name": "Living Room", "setpoint": 21.0, "temp_tags": ["living_temp"]}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                    "output_tag": "heater",
                }
            ],
        },
    )
    runtime.control_engine.compute_actions = lambda *_args, **_kwargs: {"heater": 0.25}  # type: ignore[method-assign]

    async def replay() -> None:
        for _step in range(96):
            await runtime.run_control_cycle()
            clock["t"] += _TS

    asyncio.run(replay())
    rows = list(
        runtime.control_log.iter_records(
            "tick",
            1_700_000_000.0,
            1_700_000_000.0 + 96 * _TS,
        )
    )
    assert len(rows) == 96
    assert rows[0]["dt_actual"] in (None, 0) or rows[0]["dt_actual"] == pytest.approx(0, abs=1e-6)
    assert rows[1]["dt_actual"] == pytest.approx(_TS)
    assert rows[-1]["rooms"]["living_room"]["name"] == "Living Room"
    assert runtime.actuator_outputs["heater"] == 0.25

    def _boom(_record: dict) -> None:
        raise OSError("disk full")

    runtime.control_log.append = _boom  # type: ignore[method-assign]
    again = asyncio.run(runtime.run_control_cycle())
    assert again["heater"] == 0.25


def test_export_http_schema_and_csv(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "house",
            "rooms": [{"name": "Living Room", "setpoint": 21.0}],
            "heat_sources": [
                {"name": "heater", "type": "electric_heater", "room": "Living Room", "max_power": 1000.0}
            ],
        },
    )
    stamp = datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()
    runtime.control_log.append(_tick(stamp))
    with app_server(runtime) as base_url:
        from urllib.request import urlopen

        with urlopen(f"{base_url}/api/export/schema", timeout=5) as response:
            schema = json.loads(response.read().decode("utf-8"))
        assert schema["schema"] == 2
        assert schema["kinds"]["plan"]["formats"] == ["jsonl"]
        path = (
            "/api/export?start=2026-11-01T00:00:00Z&end=2026-11-03T00:00:00Z"
            "&kind=tick&format=csv&resolution=raw"
        )
        with urlopen(base_url + path, timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/csv")
            body = response.read().decode("utf-8")
        header = body.splitlines()[0].split(",")
        assert header == schema["kinds"]["tick"]["csv_headers"]
        assert "20.5" in body
