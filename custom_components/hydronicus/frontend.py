"""Serve the bundled Hydronicus Lovelace card."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

FRONTEND_URL_PATH = "/hydronicus/hydronicus-plant-card.js"
FRONTEND_FILE = Path(__file__).parent / "frontend" / "hydronicus-plant-card.js"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register the integration-owned card bundle with Home Assistant HTTP."""
    http = hass.http
    if http is None:
        # The Home Assistant test harness can omit the HTTP component.  A real
        # frontend always has HTTP available, and WebSocket setup remains
        # usable for non-UI clients in the lightweight harness.
        return
    await http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_PATH, str(FRONTEND_FILE), cache_headers=False)]
    )
