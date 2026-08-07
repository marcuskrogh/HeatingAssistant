from __future__ import annotations

import json

import pytest

from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


def test_payload_encode_decode_round_trip() -> None:
    payload = MqttTagPayload(value=21.5, status="GOOD", reason=None, ts=1_700_000_000.25)

    encoded = payload.encode()
    decoded = MqttTagPayload.decode(encoded)

    assert json.loads(encoded) == {
        "value": 21.5,
        "status": "GOOD",
        "reason": None,
        "ts": 1_700_000_000.25,
    }
    assert decoded == payload


def test_payload_decode_accepts_bytes() -> None:
    decoded = MqttTagPayload.decode(
        b'{"value":null,"status":"BAD","reason":"entity_unavailable","ts":null}'
    )

    assert decoded.value is None
    assert decoded.status == "BAD"
    assert decoded.reason == "entity_unavailable"
    assert decoded.ts is None


@pytest.mark.parametrize("status", ["bad", "OK", "", None])
def test_payload_rejects_invalid_status(status: str | None) -> None:
    with pytest.raises(ValueError):
        MqttTagPayload(value=1, status=status)  # type: ignore[arg-type]


def test_payload_decode_requires_canonical_keys() -> None:
    with pytest.raises(ValueError, match="missing keys"):
        MqttTagPayload.decode('{"value": 1, "status": "GOOD"}')


def test_payload_decode_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        MqttTagPayload.decode("not-json")
