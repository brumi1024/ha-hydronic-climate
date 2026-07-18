"""Enable the custom integration for shadow-runtime benchmark tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def enable_hydronic_custom_integration(enable_custom_integrations) -> None:
    """Allow Home Assistant to discover Hydronicus in this test package."""
