"""Shared pytest configuration for repository tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

CORE_PATH = Path(__file__).parents[1] / "custom_components" / "hydronicus" / "core"
core_package = ModuleType("hydronicus_core")
core_package.__path__ = [str(CORE_PATH)]
sys.modules.setdefault("hydronicus_core", core_package)
