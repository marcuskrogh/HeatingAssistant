"""
pytest configuration for mbc/tests.

Ensures the repository root is on sys.path so that ``import mbc`` resolves
to the local source tree regardless of how pytest is invoked.
"""

from __future__ import annotations

import os
import sys

# Add repo root to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
