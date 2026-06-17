"""Fan platform for EOS sauna accessories."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_TYPE_COMMAND, DEVICE_TYPE_H2N, DEVICE_TYPE_DONGLE, DEVICE_TYPE_SAUNA_SENSOR, DOMAIN
from .coordinator import EosDataUpdateCoordinator
from .entity import EosBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EOS fan entities from a config entry."""
    coordinator: EosDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        EosFan(coordinator, device_id, device_data)
        for device_id, device_data in coordinator.devices.items()
        # Fan entities are not created yet; no EOS device type currently supports fan control.
        if False
    )


class EosFan(EosBaseEntity, FanEntity):
    """Control the ventilation fan."""

    _attr_icon = "mdi:fan"
    _attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the fan entity."""
        super().__init__(coordinator, device_id, device_data, "fan")

    @property
    def name(self) -> str:
        """Return the entity name."""
        return f"{self._resolve_display_name()} Fan"

    @property
    def is_on(self) -> bool:
        """Return true if the fan is on."""
        return self.coordinator.fan_reports_on(self._device_id)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on."""
        try:
            await self.coordinator.api.set_fan_state(self._device_id, True)
        except Exception:
            _LOGGER.exception("Failed to turn on EOS fan for %s", self._device_id)
            raise
        self.coordinator.optimistic_set_accessory_state(self._device_id, "fan", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        try:
            await self.coordinator.api.set_fan_state(self._device_id, False)
        except Exception:
            _LOGGER.exception("Failed to turn off EOS fan for %s", self._device_id)
            raise
        self.coordinator.optimistic_set_accessory_state(self._device_id, "fan", False)
