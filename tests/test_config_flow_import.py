"""Regression tests for config flow dependency imports."""

from __future__ import annotations

import ast
from pathlib import Path


def test_selector_is_imported_from_homeassistant_helpers() -> None:
    """The config flow must use Home Assistant's public selector module."""
    config_flow_path = (
        Path(__file__).parents[1] / "custom_components" / "hydronicus" / "config_flow.py"
    )
    tree = ast.parse(config_flow_path.read_text())
    imports = {
        (node.module, imported.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for imported in node.names
    }

    assert ("homeassistant.helpers", "selector") in imports
    assert ("homeassistant", "selector") not in imports
