"""Sensor platform for EOS sauna devices."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_TYPE_H2N,
    DEVICE_TYPE_DONGLE,
    DEVICE_TYPE_COMMAND,
    DEVICE_TYPE_SAUNA_SENSOR,    
    DOMAIN,
    SAUNA_SENSOR_STATUS_BY_CODE,
    SAUNA_SENSOR_STATUS_OPTIONS,
)
from .coordinator import EosDataUpdateCoordinator
from .entity import EosBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EOS sensor entities from a config entry."""
    coordinator: EosDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[EosSensorBase] = []
    for device_id, device_data in coordinator.devices.items():
        device_type = coordinator.device_type(device_id)
        if device_type == DEVICE_TYPE_COMMAND:
            entities.extend(
                [
                    EosTemperatureSensor(coordinator, device_id, device_data),
                    EosDurationSensor(coordinator, device_id, device_data, "heater_on_for", "autoOffTime"),
                    EosDurationSensor(coordinator, device_id, device_data, "ready_in", "timeToTarget"),
                    EosLastSessionDurationSensor(coordinator, device_id, device_data),
                    EosSessionsThisWeekSensor(coordinator, device_id, device_data),
                    EosHumiditySensor(coordinator, device_id, device_data),
                    EosTotalHoursSensor(coordinator, device_id, device_data),
                ]
            )
        elif device_type == DEVICE_TYPE_H2N:
            entities.extend(
                [
                    EosTemperatureSensor(coordinator, device_id, device_data),
                    EosDurationSensor(coordinator, device_id, device_data, "heater_on_for", "autoOffTime"),
                    EosDurationSensor(coordinator, device_id, device_data, "ready_in", "timeToTarget"),
                    EosLastSessionDurationSensor(coordinator, device_id, device_data),
                    EosSessionsThisWeekSensor(coordinator, device_id, device_data),
                    EosHumiditySensor(coordinator, device_id, device_data),
                    EosTotalHoursSensor(coordinator, device_id, device_data),
                ]
            )
        elif device_type == DEVICE_TYPE_DONGLE:
            entities.extend(
                [
                    EosTemperatureSensor(coordinator, device_id, device_data),
                    EosDurationSensor(coordinator, device_id, device_data, "heater_on_for", "autoOffTime"),
                    EosDurationSensor(coordinator, device_id, device_data, "ready_in", "timeToTarget"),
                    EosLastSessionDurationSensor(coordinator, device_id, device_data),
                    EosSessionsThisWeekSensor(coordinator, device_id, device_data),
                    EosHumiditySensor(coordinator, device_id, device_data),
                    EosTotalHoursSensor(coordinator, device_id, device_data),
                ]
            )
        elif device_type == DEVICE_TYPE_SAUNA_SENSOR:
            entities.extend(
                [
                    EosTemperatureSensor(coordinator, device_id, device_data),
                    EosHumiditySensor(coordinator, device_id, device_data),
                    EosSaunaSensorTargetTemperature(coordinator, device_id, device_data),
                    EosSaunaSensorStatus(coordinator, device_id, device_data),
                    EosSaunaSensorRssi(coordinator, device_id, device_data),
                    EosSaunaSensorLastSeen(coordinator, device_id, device_data),
                ]
            )
        
    async_add_entities(entities)


class EosSensorBase(EosBaseEntity, SensorEntity):
    """Base class for EOS sensors."""

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
        sensor_type: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, device_data, sensor_type)
        self._sensor_type = sensor_type
        self._attr_translation_key = sensor_type


class EosTemperatureSensor(EosSensorBase):
    """Current sauna temperature reported by the device."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "temperature")

    @property
    def native_value(self) -> float | None:
        """Return the current temperature."""
        temp = self._get_latest().get("temp")
        if temp is None:
            return None
        try:
            return float(temp)
        except (TypeError, ValueError):
            _LOGGER.warning("Unexpected temp value for device %s: %r", self._device_id, temp)
            return None


class EosHumiditySensor(EosSensorBase):
    """Current humidity reported by the device, rounded to a whole percent."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "humidity")

    @property
    def native_value(self) -> int | None:
        """Return the current humidity rounded to the nearest whole percent."""
        humidity = self._get_latest().get("hum")
        if humidity is None:
            return None
        try:
            return round(float(humidity))
        except (TypeError, ValueError):
            _LOGGER.warning("Unexpected humidity value for device %s: %r", self._device_id, humidity)
            return None


class EosDurationSensor(EosSensorBase):
    """Duration value reported by the device."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
        sensor_type: str,
        data_key: str,
    ) -> None:
        """Initialize the duration sensor."""
        super().__init__(coordinator, device_id, device_data, sensor_type)
        self._data_key = data_key

    @property
    def native_value(self) -> int | float | None:
        """Return the current duration in minutes."""
        value = self._get_latest().get(self._data_key)
        if value is None:
            return None
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Unexpected %s value for device %s: %r",
                self._data_key,
                self._device_id,
                value,
            )
            return None
        if numeric_value.is_integer():
            return int(numeric_value)
        return numeric_value


class EosLastSessionDurationSensor(EosSensorBase):
    """Minutes the last completed sauna session stayed on (local edge detection)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:clock-time-eight"

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "last_session_duration")

    @property
    def native_value(self) -> float | None:
        """Return last session length in minutes."""
        return self.coordinator.get_last_session_duration_minutes(self._device_id)


class EosSessionsThisWeekSensor(EosSensorBase):
    """Distinct sessionId in telemetry since local Monday 00:00 (API)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:calendar-week"

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "sessions_this_week")

    @property
    def native_value(self) -> int | None:
        """Return distinct session count for the calendar week (Monday start)."""
        return self.coordinator.get_sessions_this_week_telemetry(self._device_id)


class EosSensorTargetTemperature(EosSensorBase):
    """Target temperature configured on the sensor device (from device shadow)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "target_temperature")

    @property
    def native_value(self) -> int | float | None:
        value = self._get_state().get("targetTemp")
        if value is None:
            return None
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return None
        if numeric_value.is_integer():
            return int(numeric_value)
        return numeric_value


class EosSensorStatus(EosSensorBase):
    """Sauna sensor lifecycle status (off / heating / ready / cooling / error)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SAUNA_SENSOR_STATUS_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:thermometer-check"

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "status")

    @property
    def native_value(self) -> str | None:
        raw = self._get_latest().get("saunaStatus")
        if raw is None:
            return None
        try:
            code = int(raw)
        except (TypeError, ValueError):
            return None
        return SAUNA_SENSOR_STATUS_BY_CODE.get(code)


class EosSensorLastSeen(EosSensorBase):
    """Wall-clock timestamp of the latest cloud reading for this sensor."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "last_seen")

    @property
    def native_value(self) -> datetime | None:
        ts_ms = self.coordinator.latest_data_timestamp_ms(self._device_id)
        if ts_ms is None:
            return None
        return datetime.fromtimestamp(ts_ms / 1000, tz=UTC)


class EosSensorRssi(EosSensorBase):
    """Wi-Fi signal strength reported by the sensor."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "rssi")

    @property
    def native_value(self) -> int | None:
        raw = self._get_latest().get("rssi")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None


class EosTotalHoursSensor(EosSensorBase):
    """Total cumulative sauna running hours reported by the device."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        coordinator: EosDataUpdateCoordinator,
        device_id: str,
        device_data: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_id, device_data, "total_hours")

    @property
    def native_value(self) -> int | float | None:
        """Return total sauna running hours."""
        value = self._get_latest().get("totalOnTime")
        if value is None:
            return None
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            _LOGGER.warning("Unexpected totalHours value for device %s: %r", self._device_id, value)
            return None
        if numeric_value.is_integer():
            return int(numeric_value)
        return numeric_value
