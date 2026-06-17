"""Light platform for EOS sauna accessories."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ColorMode
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
    """Set up EOS light entities from a config entry."""
    coordinator: EosDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        EosLight(coordinator, device_id, device_data)
        for device_id, device_data in coordinator.devices.items()
        if coordinator.is_controller(device_id)
    )


class EosLight(EosBaseEntity, LightEntity):
    """Control the sauna lights."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_icon = "mdi:lightbulb"
    _attr_translation_key = "light"

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the light entity."""
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        super().__init__(coordinator, device_id, device_data, "light")

    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        return self.coordinator.light_reports_on(self._device_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the lights on."""
        try:
            await self.coordinator.api.set_light_state(self._device_id, True)
        except Exception:
            _LOGGER.exception("Failed to turn on EOS light for %s", self._device_id)
            raise
        self.coordinator.optimistic_set_accessory_state(self._device_id, "light", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the lights off."""
        try:
            await self.coordinator.api.set_light_state(self._device_id, False)
        except Exception:
            _LOGGER.exception("Failed to turn off EOS light for %s", self._device_id)
            raise
        self.coordinator.optimistic_set_accessory_state(self._device_id, "light", False)
