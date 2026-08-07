"""Monkeypatches for integration setup tests (stores, hass config)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def patch_setup_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub heavy stores so ``async_setup_entry`` can run without real HA."""

    class _FakeIdHistoryStore:
        async def async_setup(self):
            return None

        async def async_query_recent(self, _n):
            return []

    class _FakeDatasetStore:
        async def async_load(self):
            return None

    class _FakeExperimentStore:
        async def async_load(self):
            return None

    monkeypatch.setattr(
        "heatingassistant.engine.history.store.IdentificationHistoryStore",
        lambda *_a, **_kw: _FakeIdHistoryStore(),
    )
    monkeypatch.setattr(
        "custom_components.heating_assistant.datasets.DatasetStore",
        lambda *_a, **_kw: _FakeDatasetStore(),
    )
    monkeypatch.setattr(
        "custom_components.heating_assistant.experiments.ExperimentStore",
        lambda *_a, **_kw: _FakeExperimentStore(),
    )


def ensure_hass_config(hass: SimpleNamespace, config_dir: str = "/tmp/ha_test") -> None:
    """Ensure hass exposes ``config.config_dir`` for storage-backed components."""
    if not hasattr(hass, "config"):
        hass.config = SimpleNamespace(config_dir=config_dir)
    if not hasattr(hass, "async_add_executor_job"):
        hass.async_add_executor_job = AsyncMock(
            side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)
        )
