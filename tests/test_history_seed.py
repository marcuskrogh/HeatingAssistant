"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer
(coordinator stubs / recorder rebuild path). Pure helpers live under
``heatingassistant.engine.history.seed`` and are covered by engine unit tests
where applicable.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)
