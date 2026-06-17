"""Binary sensor platform for EOS sauna devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_TYPE_COMMAND, DEVICE_TYPE_H2N, DEVICE_TYPE_DONGLE, DEVICE_TYPE_SAUNA_SENSOR, DOMAIN
from .coordinator import EosDataUpdateCoordinator
from .entity import EosBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EOS binary sensor entities from a config entry."""
    coordinator: EosDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []
    for device_id, device_data in coordinator.devices.items():
        device_type = coordinator.device_type(device_id)
        if device_type == DEVICE_TYPE_COMMAND:
            entities.append(EosSteamerBinarySensor(coordinator, device_id, device_data))
        elif device_type == DEVICE_TYPE_H2N:
            entities.append(EosSteamerBinarySensor(coordinator, device_id, device_data))
        elif device_type == DEVICE_TYPE_DONGLE:
            entities.append(EosSteamerBinarySensor(coordinator, device_id, device_data))
        elif device_type == DEVICE_TYPE_SAUNA_SENSOR:
            entities.append(EosPresenceBinarySensor(coordinator, device_id, device_data))
    async_add_entities(entities)


class EosSteamerBinarySensor(EosBaseEntity, BinarySensorEntity):
    """Report whether the steamer is currently on."""

    _attr_icon = "mdi:weather-fog"
    _attr_translation_key = "steamer"

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the steamer binary sensor."""
        super().__init__(coordinator, device_id, device_data, "steamer")

    @property
    def is_on(self) -> bool:
        """Return true if the steamer is on."""
        return self.coordinator.steamer_reports_on(self._device_id)


class EosPresenceBinarySensor(EosBaseEntity, BinarySensorEntity):
    """Presence detected when latest PIR reading exceeds the configured threshold."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_translation_key = "presence"

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the presence binary sensor."""
        super().__init__(coordinator, device_id, device_data, "presence")

    @property
    def is_on(self) -> bool | None:
        """Return true when PIR reading exceeds the configured threshold."""
        presence = self._get_latest().get("presence")
        threshold = self._get_state().get("PIRThreshold")
        if presence is None or threshold is None:
            return None
        try:
            return float(presence) > float(threshold)
        except (TypeError, ValueError):
            return None
