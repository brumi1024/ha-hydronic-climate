"""Home Assistant Repairs synchronization for unresolved topology bindings."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from .const import DOMAIN
from .core.entity_bindings import BindingCategory, EntityBinding

REPAIR_ISSUE_PREFIX = "unresolved_entity"


def _plant_issue_prefix(plant_id: str) -> str:
    """Build the opaque issue-ID prefix for one plant."""
    digest = hashlib.sha256(plant_id.encode("utf-8")).hexdigest()[:16]
    return f"{REPAIR_ISSUE_PREFIX}_{digest}_"


def _issue_id(plant_id: str, binding: EntityBinding) -> str:
    """Build an opaque, stable issue ID without storing the entity reference."""
    stable_key = "|".join((plant_id, binding.object_id, binding.object_type, binding.binding_key))
    digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
    return f"{_plant_issue_prefix(plant_id)}{binding.category.value}_{digest}"


def async_sync_repairs(
    hass: Any, plant_id: str, unresolved_bindings: Iterable[EntityBinding]
) -> None:
    """Create current binding repairs and remove recovered or obsolete repairs."""
    if not hasattr(hass, "bus"):
        # The runtime has a deliberately small Home Assistant-free test seam.
        return
    try:
        from homeassistant.helpers.issue_registry import (
            IssueSeverity,
            async_create_issue,
            async_delete_issue,
            async_get,
        )
    except ImportError:  # pragma: no cover - only used by the lightweight test seam
        return

    current = tuple(unresolved_bindings)
    current_issue_ids = {_issue_id(plant_id, binding) for binding in current}
    registry = async_get(hass)
    for (domain, issue_id), _issue in tuple(registry.issues.items()):
        if domain != DOMAIN or not issue_id.startswith(_plant_issue_prefix(plant_id)):
            continue
        if issue_id not in current_issue_ids:
            async_delete_issue(hass, DOMAIN, issue_id)

    translation_keys = {
        BindingCategory.SENSOR: "missing_sensor_binding",
        BindingCategory.FEEDBACK: "missing_feedback_binding",
        BindingCategory.ACTUATOR: "missing_actuator_binding",
    }
    for binding in current:
        async_create_issue(
            hass,
            DOMAIN,
            _issue_id(plant_id, binding),
            data={
                "object_id": binding.object_id,
                "binding_key": binding.binding_key,
                "binding_category": binding.category.value,
            },
            is_fixable=False,
            is_persistent=False,
            severity=IssueSeverity.ERROR,
            translation_key=translation_keys[binding.category],
            translation_placeholders={
                "object_type": binding.object_type,
                "object_name": binding.object_name,
                "binding_label": binding.label,
            },
        )
