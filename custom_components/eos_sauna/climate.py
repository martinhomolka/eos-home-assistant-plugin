"""Climate platform for EOS sauna devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EosDataUpdateCoordinator
from .entity import EosBaseEntity
from .profile import profile_slot_label_pairs

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EOS climate entities from a config entry."""
    coordinator: EosDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        EosClimate(coordinator, device_id, device_data)
        for device_id, device_data in coordinator.devices.items()
        if coordinator.is_controller(device_id)
    )


class EosClimate(EosBaseEntity, ClimateEntity):
    """Climate entity representing an EOS sauna."""

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_HUMIDITY
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 30
    _attr_max_temp = 90
    _attr_target_temperature_step = 1
    _attr_min_humidity = 0
    _attr_max_humidity = 100
    _attr_target_humidity_step = 1
    _attr_name = None

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, device_id, device_data, "climate")
        self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        """Convert a raw API value into a float when possible."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    @property
    def current_temperature(self) -> float | None:
        """Return the current sauna temperature."""
        return self._coerce_float(self._get_latest().get("temp"))

    @property
    def current_humidity(self) -> int | None:
        """Return current relative humidity when reported."""
        numeric_value = self._coerce_float(self._get_latest().get("hum"))
        return round(numeric_value) if numeric_value is not None else None

    def _active_profile_targets(self) -> dict[str, Any] | None:
        """Return the profile dict for the currently selected program slot."""
        idx = self._active_profile_index()
        profiles = self._get_state().get("profiles")
        return (
            None
            if idx is None or not isinstance(profiles, dict)
            else profiles.get(str(idx))
            if isinstance(profiles.get(str(idx)), dict)
            else None
        )

    def _reported_target_humidity(self) -> float | None:
        """Target humidity from active profile, shadow, or telemetry."""
        prof = self._active_profile_targets()
        if prof is not None:
            target_hum = self._coerce_float(prof.get("targetHum"))
            if target_hum is not None:
                return target_hum
        return self._coerce_float(self._get_state().get("targetHum"))

    def _humidity_for_api(self, override: int | None) -> int:
        """Humidity to send with PATCH (explicit override or last reported)."""
        if override is not None:
            return self._clamp_target_humidity(override)
        reported = self._reported_target_humidity()
        if reported is not None:
            return self._clamp_target_humidity(round(reported))
        return self._clamp_target_humidity(round(self.min_humidity))

    def _clamp_target_humidity(self, value: int) -> int:
        """Clamp humidity to entity limits."""
        min_humidity = round(self.min_humidity)
        max_humidity = round(self.max_humidity)
        return max(min_humidity, min(max_humidity, value))

    @property
    def target_humidity(self) -> int | None:
        """Return the target humidity (%)."""
        humidity = self._reported_target_humidity()
        return round(humidity) if humidity is not None else None

    def _active_profile_index(self) -> int | None:
        """Return the selected program slot (0-3), if known.

        Some controllers (EOS_H2N) never publish ``activeProfile`` in their shadow, so
        fall back to the slot whose stored target matches the live ``targetTemp``. That
        only ever picks a slot the device is demonstrably already using; when nothing
        matches we return None rather than guessing and overwriting a saved program.
        """
        state = self._get_state()
        raw = state.get("activeProfile")
        if raw is not None:
            return int(raw)

        target = state.get("targetTemp")
        profiles = state.get("profiles")
        if target is not None and isinstance(profiles, dict):
            for key, prof in profiles.items():
                if isinstance(prof, dict) and prof.get("targetTemp") == target:
                    try:
                        return int(key)
                    except (TypeError, ValueError):
                        continue
        return None

    def _reported_target_temperature(self) -> float | None:
        """Target temperature from active profile, shadow, or telemetry."""
        prof = self._active_profile_targets()
        if prof is not None:
            target_temp = self._coerce_float(prof.get("targetTemp"))
            if target_temp is not None:
                return target_temp
        return self._coerce_float(self._get_state().get("targetTemp"))

    def _reported_max_temperature(self) -> float | None:
        """Maximum allowed target temperature from device state settings."""
        settings = self._get_state().get("settings")
        if not isinstance(settings, dict):
            return None
        return self._coerce_float(settings.get("maxTemp"))

    @property
    def max_temp(self) -> float:
        """Return the maximum supported target temperature."""
        reported = self._reported_max_temperature()
        return reported if reported is not None else self._attr_max_temp

    @property
    def target_temperature(self) -> float | None:
        """Return the target sauna temperature."""
        return self._reported_target_temperature()

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current sauna power state."""
        return HVACMode.HEAT if self.coordinator.sauna_reports_heat_on(self._device_id) else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current heating action."""
        return (
            HVACAction.OFF
            if self.hvac_mode == HVACMode.OFF
            else HVACAction.HEATING
            if self.coordinator.sauna_reports_heat_on(self._device_id)
            else HVACAction.IDLE
        )

    @property
    def preset_modes(self) -> list[str]:
        """Sauna program slots as Home Assistant presets."""
        return [label for _, label in profile_slot_label_pairs(self.coordinator, self._device_id)]

    @property
    def preset_mode(self) -> str | None:
        """Label for the active program slot."""
        active = self._active_profile_index()
        if active is None:
            return None
        pairs = profile_slot_label_pairs(self.coordinator, self._device_id)
        return next((label for i, label in pairs if i == active), None)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Activate a saved program on the controller."""
        for i, label in profile_slot_label_pairs(self.coordinator, self._device_id):
            if label == preset_mode:
                await self.coordinator.api.set_profile(self._device_id, i)
                self.coordinator.optimistic_set_profile(self._device_id, i)
                return
        raise HomeAssistantError(f"Invalid preset {preset_mode!r} for device {self._device_id}")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature (and optional humidity in the same call)."""
        if (temperature := kwargs.get("temperature")) is None:
            return
        temperature_value = float(temperature)
        humidity_kw = kwargs.get("humidity")
        humidity_override = int(humidity_kw) if humidity_kw is not None else None
        active_profile = self._active_profile_index()
        await self.coordinator.api.set_target(
            device_id=self._device_id,
            temperature=round(temperature_value),
            humidity=self._humidity_for_api(humidity_override),
            profile_index=active_profile,
        )
        self.coordinator.optimistic_set_target(
            device_id=self._device_id,
            temperature=round(temperature_value),
            humidity=self._humidity_for_api(humidity_override),
            profile_index=active_profile,
        )

    async def async_set_humidity(self, humidity: int) -> None:
        """Set target humidity."""
        humidity_value = self._clamp_target_humidity(int(humidity))
        temp = self.target_temperature if self.target_temperature is not None else self._reported_target_temperature()
        if temp is None:
            _LOGGER.warning("Cannot set humidity for %s: no target temperature available", self._device_id)
            return

        active_profile = self._active_profile_index()
        await self.coordinator.api.set_target(
            device_id=self._device_id,
            temperature=round(float(temp)),
            humidity=humidity_value,
            profile_index=active_profile,
        )
        self.coordinator.optimistic_set_target(
            device_id=self._device_id,
            temperature=round(float(temp)),
            humidity=humidity_value,
            profile_index=active_profile,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn the sauna on or off."""
        if hvac_mode not in (HVACMode.HEAT, HVACMode.OFF):
            _LOGGER.warning("Unsupported HVAC mode requested for %s: %s", self._device_id, hvac_mode)
            return
        await self.coordinator.api.set_sauna_state(
            self._device_id,
            hvac_mode == HVACMode.HEAT,
        )
        self.coordinator.optimistic_set_sauna_state(self._device_id, hvac_mode == HVACMode.HEAT)

    async def async_turn_on(self) -> None:
        """Turn the sauna on."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the sauna off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
