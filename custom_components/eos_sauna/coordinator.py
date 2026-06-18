"""Data update coordinator for EOS."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EosAPI, EosAPIError, EosAuthenticationError
from .const import (
    CONF_STAGE,
    CONTROLLER_DEVICE_TYPES,
    DEFAULT_STAGE,
    DEVICE_TYPE_COMMAND, 
    DEVICE_TYPE_H2N, 
    DEVICE_TYPE_DONGLE,
    DEVICE_TYPE_SAUNA_SENSOR,
    DOMAIN,
    SENSOR_DEVICE_TYPES,
    UPDATE_INTERVAL,
)
from .session_tracking import EosSessionTracking, parse_measurement_timestamp_ms

_LOGGER = logging.getLogger(__name__)


class EosDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching EOS data."""

    @staticmethod
    def _get_update_interval(entry: ConfigEntry) -> timedelta:
        """Return the configured polling interval."""
        raw = entry.options.get("poll_interval_seconds")
        if raw is not None:
            return timedelta(seconds=int(raw))
        return UPDATE_INTERVAL

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._get_update_interval(entry),
        )
        self.api = EosAPI(
            email=entry.data[CONF_EMAIL],
            password=entry.data[CONF_PASSWORD],
            session=aiohttp_client.async_get_clientsession(hass),
            stage=entry.data.get(CONF_STAGE, DEFAULT_STAGE),
        )

        self.devices: dict[str, dict[str, Any]] = {}
        self.device_names: dict[str, str] = {}
        self.device_states: dict[str, dict[str, Any]] = {}
        self.latest_data: dict[str, dict[str, Any]] = {}
        self.sessions = EosSessionTracking(self.api)

    def is_controller(self, device_id: str) -> bool:
        """Return True if the device is a controller type (full sauna control)."""
        return self.device_type(device_id) in CONTROLLER_DEVICE_TYPES

    def is_sensor_only(self, device_id: str) -> bool:
        """Return True if the device is a sensor-only type."""
        return self.device_type(device_id) in SENSOR_DEVICE_TYPES

    def _ensure_device_shadow_state(self, device_id: str) -> dict[str, Any]:
        """Return a mutable shadow state object for optimistic updates."""
        payload = self.device_states.setdefault(device_id, {})
        state = payload.get("state")
        if not isinstance(state, dict):
            state = {}
            payload["state"] = state
        return state

    def _notify_optimistic_update(self) -> None:
        """Notify listeners after mutating cached optimistic state."""
        self.async_set_updated_data(
            {
                "devices": self.devices,
                "device_names": self.device_names,
                "telemetry_session_counts": self.sessions.telemetry_counts_for_coordinator_data(),
            }
        )

    def optimistic_set_sauna_state(self, device_id: str, turn_on: bool) -> None:
        """Optimistically update cached sauna power state after a successful command."""
        state = self._ensure_device_shadow_state(device_id)
        state["saunaStatus"] = int(turn_on)
        self.sessions.tick_local_for_device(device_id, heat_on=self.sauna_reports_heat_on(device_id))
        self._notify_optimistic_update()

    def optimistic_set_profile(self, device_id: str, profile_index: int) -> None:
        """Optimistically update cached active profile after a successful command."""
        state = self._ensure_device_shadow_state(device_id)
        state["activeProfile"] = profile_index
        self._notify_optimistic_update()

    def optimistic_set_target(
        self,
        device_id: str,
        temperature: int,
        humidity: int,
        *,
        profile_index: int | None = None,
    ) -> None:
        """Optimistically update cached target values after a successful command."""
        state = self._ensure_device_shadow_state(device_id)
        state["targetTemp"] = temperature
        state["targetHum"] = humidity

        if profile_index is not None:
            profiles = state.get("profiles")
            if isinstance(profiles, dict):
                profile = profiles.get(str(profile_index))
                if isinstance(profile, dict):
                    profile["targetTemp"] = temperature
                    profile["targetHum"] = humidity

        self._notify_optimistic_update()

    def optimistic_set_accessory_state(self, device_id: str, key: str, turn_on: bool) -> None:
        """Optimistically update cached fan or light state after a successful command."""
        state = self._ensure_device_shadow_state(device_id)
        accessory = state.get(key)
        if not isinstance(accessory, dict):
            accessory = {}
            state[key] = accessory
        accessory["on"] = int(turn_on)
        self._notify_optimistic_update()

    @staticmethod
    def _resolve_device_name(
        device_id: str,
        device: dict[str, Any],
        state_response: dict[str, Any],
    ) -> str:
        """Return the display name for a device.

        Looks in every plausible location across controller flat ``state`` envelope and
        sensor-only AWS IoT shadow shape (``desired``/``reported``).
        """

        def _from(container: Any) -> str | None:
            if not isinstance(container, dict):
                return None
            value = container.get("displayName")
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        raw_state = state_response.get("state")
        state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
        for container in (
            state_response,
            state_response.get("desired"),
            state_response.get("reported"),
            state,
            state.get("desired"),
            state.get("reported"),
        ):
            name = _from(container)
            if name:
                return name

        fallback_name = device.get("name") or device_id
        return str(fallback_name).strip()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch devices, state, and latest data."""
        try:
            device_list = await self.api.get_devices()
        except EosAuthenticationError as err:
            raise ConfigEntryAuthFailed(f"Authentication error: {err}") from err
        except EosAPIError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        # EOS's /devices payload uses the field "name" to carry the stable device UUID.
        # This matches the "deviceId" field returned by /devices/state.
        self.devices = {}
        for device in device_list:
            device_id = device.get("name")
            if device_id:
                self.devices[device_id] = device
            else:
                _LOGGER.warning("Device entry has no 'name' field, skipping: %s", device)

        self.device_states = {}
        self.latest_data = {}

        async def fetch_device_data(device_id: str) -> None:
            device_type = self.device_type(device_id)
            try:
                self.device_states[device_id] = await self.api.get_device_state(device_id)
            except EosAuthenticationError as err:
                raise ConfigEntryAuthFailed(f"Authentication error: {err}") from err
            except EosAPIError as err:
                _LOGGER.warning("Failed to fetch device state for device %s: %s", device_id, err)
                self.device_states[device_id] = {}
            _LOGGER.debug(
                "EOS /devices/state response for %s (type=%s): %s",
                device_id,
                device_type,
                self.device_states[device_id],
            )
            try:
                self.latest_data[device_id] = await self.api.get_latest_data(device_id)
            except EosAuthenticationError as err:
                raise ConfigEntryAuthFailed(f"Authentication error: {err}") from err
            except EosAPIError as err:
                _LOGGER.warning("Failed to fetch latest data for device %s: %s", device_id, err)
                self.latest_data[device_id] = {}
            _LOGGER.debug(
                "EOS /data/latest-data response for %s (type=%s): %s",
                device_id,
                device_type,
                self.latest_data[device_id],
            )

        await asyncio.gather(*(fetch_device_data(device_id) for device_id in self.devices))

        self.device_names = {
            device_id: self._resolve_device_name(
                device_id,
                device,
                self.device_states.get(device_id, {}),
            )
            for device_id, device in self.devices.items()
        }

        controller_device_ids = [device_id for device_id in self.devices if self.is_controller(device_id)]
        for device_id in controller_device_ids:
            heat_on = self.sauna_reports_heat_on(device_id)
            self.sessions.tick_local_for_device(device_id, heat_on=heat_on)

        await self.sessions.refresh_session_counts_for_devices(controller_device_ids)

        return {
            "devices": self.devices,
            "device_names": self.device_names,
            "telemetry_session_counts": self.sessions.telemetry_counts_for_coordinator_data(),
        }

    def device_type(self, device_id: str) -> str | None:
        """Return the device type string (e.g. "EOS_Command") from /devices."""
        device = self.devices.get(device_id)
        if not isinstance(device, dict):
            return None
        device_type = device.get("type")
        return device_type if isinstance(device_type, str) else None

    def get_device_shadow_state(self, device_id: str) -> dict[str, Any]:
        """Return the flattened shadow state for a device.

        Controller types expose a flat ``state`` wrapper. Sensor-only types use the AWS IoT
        shadow shape with separate ``desired`` and ``reported`` sub-objects. We merge them
        with desired winning so callers see config fields at the top level.
        """
        payload = self.device_states.get(device_id)
        if not isinstance(payload, dict):
            return {}

        if self.is_sensor_only(device_id):
            for container in (payload.get("state"), payload):
                if not isinstance(container, dict):
                    continue
                reported = container.get("reported")
                desired = container.get("desired")
                if isinstance(reported, dict) or isinstance(desired, dict):
                    _LOGGER.debug("Flattening desired+reported shadow for sensor device %s", device_id)
                    return {
                        **(reported if isinstance(reported, dict) else {}),
                        **(desired if isinstance(desired, dict) else {}),
                    }

        inner = payload.get("state")
        return inner if isinstance(inner, dict) else {}

    def get_latest_measurement(self, device_id: str) -> dict[str, Any]:
        """Return the telemetry ``data`` object from latest-data for a device."""
        device_payload = self.latest_data.get(device_id)
        if not isinstance(device_payload, dict):
            return {}
        inner = device_payload.get("data")
        return inner if isinstance(inner, dict) else {}

    def latest_data_timestamp_ms(self, device_id: str) -> int | None:
        """Return the latest-data payload timestamp in epoch ms, or None if absent."""
        payload = self.latest_data.get(device_id)
        if not isinstance(payload, dict):
            return None
        inner = payload.get("data") if isinstance(payload.get("data"), dict) else None
        for container in (payload, inner):
            if container is None:
                continue
            for key in ("timestamp", "ts", "time", "updatedAt"):
                ts = parse_measurement_timestamp_ms(container.get(key))
                if ts is not None:
                    # Heuristic: anything earlier than year 2001 in ms is almost certainly seconds.
                    return ts * 1000 if ts < 1_000_000_000_000 else ts
        return None

    def device_connected(self, device_id: str) -> bool | None:
        """Return whether the device is currently connected to the cloud."""
        payload = self.device_states.get(device_id)
        if not isinstance(payload, dict):
            return None
        conn = payload.get("connectionState")
        if not isinstance(conn, dict):
            return None
        connected = conn.get("connected")
        return bool(connected) if connected is not None else None

    def sauna_reports_heat_on(self, device_id: str) -> bool:
        """Return whether the device reports the sauna as active / heating."""
        state = self.get_device_shadow_state(device_id)
        latest = self.get_latest_measurement(device_id)

        sauna_status = state.get("saunaStatus")
        if sauna_status is not None:
            return bool(sauna_status)

        heat_on = latest.get("heatOn")
        if heat_on is not None:
            return bool(heat_on)

        return False

    def accessory_reports_on(self, device_id: str, key: str) -> bool:
        """Return whether accessory is on."""
        block = self.get_device_shadow_state(device_id).get(key)
        if isinstance(block, dict):
            if block.get("on") is not None:
                return bool(block["on"])
            if block.get("state") is not None:
                return bool(block["state"])
            _LOGGER.debug("Device %s shadow %r has no on/state keys: %s", device_id, key, block)
            return False
        if block is not None:
            _LOGGER.debug("Device %s shadow %r is not a dict: %r", device_id, key, block)
        return False

    def fan_reports_on(self, device_id: str) -> bool:
        """Return whether the fan is reported as on."""
        return self.accessory_reports_on(device_id, "fan")

    def light_reports_on(self, device_id: str) -> bool:
        """Return whether the light is reported as on."""
        return self.accessory_reports_on(device_id, "light")

    def steamer_reports_on(self, device_id: str) -> bool:
        """Return whether the steamer is reported as on."""
        return self.accessory_reports_on(device_id, "steamer")

    def get_sessions_this_week_telemetry(self, device_id: str) -> int | None:
        """Distinct sessionId count since local Monday 00:00."""
        return self.sessions.get_sessions_this_week_telemetry(device_id)

    def get_last_session_duration_minutes(self, device_id: str) -> float | None:
        """Return last completed session length in minutes, if any."""
        return self.sessions.get_last_session_duration_minutes(device_id)

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        await self.api.close()
        await super().async_shutdown()
