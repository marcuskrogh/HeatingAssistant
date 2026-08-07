"""Unit tests for shared naming helpers."""

from __future__ import annotations

import pytest

from heatingassistant.engine.naming import slugify


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Erik's Room", "eriks_room"),
        ("Living Room", "living_room"),
        ("Bedroom", "bedroom"),
        ("café", "cafe"),
        ("Room 101", "room_101"),
    ],
)
def test_slugify_nfkd_ascii_strips_punctuation(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_empty_string() -> None:
    assert slugify("") == "_"
    assert slugify("---") == "_"
