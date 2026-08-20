from __future__ import annotations

import pytest

from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import (
    TOPIC_ROOT,
    bindings,
    cmd,
    entities,
    parse_tag_topic,
    status,
    tag_in,
    tag_out,
)


pytestmark = pytest.mark.unit


def test_topic_builders_follow_contract() -> None:
    assert TOPIC_ROOT == "heatingassistant"
    assert tag_in("haos", "living_temp_1") == "heatingassistant/haos/tag/living_temp_1/in"
    assert tag_out("haos", "living_setpoint") == "heatingassistant/haos/tag/living_setpoint/out"
    assert cmd("haos", "reload") == "heatingassistant/haos/cmd/reload"
    assert cmd("haos", "notify") == "heatingassistant/haos/cmd/notify"
    assert status("haos") == "heatingassistant/haos/status"
    assert bindings("haos") == "heatingassistant/haos/bindings"
    assert entities("haos") == "heatingassistant/haos/entities"


def test_parse_tag_topic_returns_structured_parts() -> None:
    parsed = parse_tag_topic("heatingassistant/haos/tag/living_temp_1/in")

    assert parsed is not None
    assert parsed.instance_id == "haos"
    assert parsed.tag == "living_temp_1"
    assert parsed.direction == "in"


@pytest.mark.parametrize(
    "topic",
    [
        "other/haos/tag/living/in",
        "heatingassistant/haos/tag/living",
        "heatingassistant/haos/cmd/living/in",
        "heatingassistant/haos/tag/living/bad",
        "heatingassistant//tag/living/in",
    ],
)
def test_parse_tag_topic_rejects_non_contract_topics(topic: str) -> None:
    assert parse_tag_topic(topic) is None


def test_topic_builders_reject_ambiguous_topic_parts() -> None:
    with pytest.raises(ValueError):
        tag_in("haos", "living/temp")
    with pytest.raises(ValueError):
        tag_out("haos", "+")


@pytest.mark.asyncio
async def test_in_memory_bus_delivers_matching_wildcard_subscription() -> None:
    bus = InMemoryMqttBus()
    seen: list[tuple[str, str | bytes, int, bool]] = []
    bus.subscribe("heatingassistant/haos/tag/+/in", lambda *message: seen.append(message))

    await bus.publish("heatingassistant/haos/tag/living/in", "payload", qos=1)
    await bus.publish("heatingassistant/other/tag/living/in", "ignored", qos=1)

    assert seen == [("heatingassistant/haos/tag/living/in", "payload", 1, False)]
