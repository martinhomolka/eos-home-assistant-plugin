"""Shared entity helpers for the EOS integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EosDataUpdateCoordinator


class EosBaseEntity(CoordinatorEntity[EosDataUpdateCoordinator]):
    """Base class for EOS entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
        unique_suffix: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_data = device_data
        self._attr_unique_id = f"{DOMAIN}_{device_id}_{unique_suffix}"

    def _resolve_display_name(self) -> str:
        """Return the device display name."""
        return self.coordinator.device_names.get(self._device_id, self._device_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device = self.coordinator.devices.get(self._device_id, self._device_data)
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._resolve_display_name(),
            manufacturer="EOS Saunatechnik",
            model=device.get("type", "Sauna Controller"),
        )

    def _get_state(self) -> dict[str, Any]:
        """Return the device state payload for this device."""
        return self.coordinator.get_device_shadow_state(self._device_id)

    def _get_latest(self) -> dict[str, Any]:
        """Return the latest telemetry payload for this device."""
        return self.coordinator.get_latest_measurement(self._device_id)
