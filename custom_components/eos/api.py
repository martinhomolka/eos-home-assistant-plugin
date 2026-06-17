"""API client for the EOS cloud API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import ClientError

from .const import API_TOKEN_TIMEOUT, DEFAULT_STAGE, endpoint_config_url

_LOGGER = logging.getLogger(__name__)


class EosAPIError(Exception):
    """Base exception for EOS API errors."""


class EosAuthenticationError(EosAPIError):
    """Authentication error."""


class EosAPI:
    """Client for the EOS cloud API."""

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
        stage: str = DEFAULT_STAGE,
    ) -> None:
        """Initialize the EOS API client."""
        self._email = email
        self._password = password
        self._session = session
        self._own_session = session is None
        self._stage = stage
        self._endpoint_config: dict[str, Any] | None = None
        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: datetime | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_endpoint_config(self) -> dict[str, Any]:
        """Fetch and cache API endpoint configuration."""
        if self._endpoint_config is None:
            session = self._get_session()
            async with session.get(endpoint_config_url(self._stage)) as response:
                if response.status != 200:
                    raise EosAPIError(f"Failed to fetch endpoint config: {response.status}")
                self._endpoint_config = await response.json()
        return self._endpoint_config

    async def _authenticate(self) -> None:
        """Authenticate against the API and store tokens."""
        config = await self._get_endpoint_config()
        auth_base = config.get("endpoints", {}).get("RestApi", {}).get("generics", {}).get("https")
        if not auth_base:
            raise EosAPIError("Authentication endpoint not found in config")

        session = self._get_session()

        payload = {
            "username": self._email,
            "password": self._password,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = await session.post(
                f"{auth_base}/auth/token",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            )
        except ClientError as err:
            raise EosAuthenticationError("network_error") from err

        if response.status != 200:
            _LOGGER.warning("EOS auth failed (%s)", response.status)
            raise EosAuthenticationError("invalid_auth")

        data: dict[str, Any] = await response.json()
        self._id_token = data.get("idToken")
        self._refresh_token = data.get("refreshToken")

        if not self._id_token:
            raise EosAuthenticationError("invalid_auth")

        self._token_expires_at = datetime.now() + timedelta(seconds=API_TOKEN_TIMEOUT)
        _LOGGER.info("EOS authentication succeeded for %s", self._email)

    async def _refresh_auth_token(self) -> None:
        """Refresh the authentication token."""
        if not self._refresh_token:
            await self._authenticate()
            return

        config = await self._get_endpoint_config()
        auth_base = config.get("endpoints", {}).get("RestApi", {}).get("generics", {}).get("https")
        if not auth_base:
            raise EosAPIError("Authentication endpoint not found in config")

        session = self._get_session()

        payload = {
            "email": self._email,
            "refreshToken": self._refresh_token,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = await session.post(
                f"{auth_base}/auth/refresh",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            )
        except ClientError as err:
            raise EosAuthenticationError("network_error") from err

        if response.status != 200:
            _LOGGER.warning("EOS token refresh failed (%s), re-authenticating", response.status)
            await self._authenticate()
            return

        data: dict[str, Any] = await response.json()
        self._id_token = data.get("idToken")
        if self._id_token:
            self._token_expires_at = datetime.now() + timedelta(seconds=API_TOKEN_TIMEOUT)
            _LOGGER.debug("EOS token refreshed successfully")
        else:
            await self._authenticate()
            if not self._id_token:
                raise EosAuthenticationError("invalid_auth")

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid authentication token."""
        if (
            self._id_token is None
            or self._token_expires_at is None
            or datetime.now() >= self._token_expires_at - timedelta(minutes=5)
        ):
            if self._refresh_token and self._token_expires_at and datetime.now() < self._token_expires_at:
                await self._refresh_auth_token()
            else:
                await self._authenticate()

    async def _make_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an authenticated API request."""
        await self._ensure_authenticated()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._id_token}"
        headers.setdefault("Accept", "application/json")

        session = self._get_session()
        try:
            async with session.request(
                method, url, headers=headers, timeout=aiohttp.ClientTimeout(total=15), **kwargs
            ) as response:
                if response.status == 401:
                    _LOGGER.debug("EOS token expired mid-request, re-authenticating")
                    await self._authenticate()
                    headers["Authorization"] = f"Bearer {self._id_token}"
                    async with session.request(
                        method, url, headers=headers, timeout=aiohttp.ClientTimeout(total=15), **kwargs
                    ) as retry_response:
                        if retry_response.status in (401, 403):
                            raise EosAuthenticationError("invalid_auth")
                        if retry_response.status != 200:
                            raise EosAPIError(f"API request failed: {retry_response.status}")
                        return dict(await retry_response.json())

                if response.status in (401, 403):
                    raise EosAuthenticationError("invalid_auth")
                if response.status != 200:
                    _LOGGER.warning("EOS API request failed (%s)", response.status)
                    raise EosAPIError("cannot_connect")

                return dict(await response.json())

        except ClientError as err:
            raise EosAPIError("network_error") from err

    async def get_devices(self) -> list[dict[str, Any]]:
        """Fetch devices for the authenticated account."""
        config = await self._get_endpoint_config()
        device_base = config.get("endpoints", {}).get("RestApi", {}).get("device", {}).get("https")
        if not device_base:
            raise EosAPIError("Device service endpoint not found in config")

        data = await self._make_request("GET", f"{device_base}/devices")
        devices = data.get("devices")
        device_list = [d for d in devices if isinstance(d, dict)] if isinstance(devices, list) else []
        device_labels = [f"{device.get('name')} ({device.get('type')})" for device in device_list]
        _LOGGER.info(
            "EOS discovered %s device(s): %s",
            len(device_list),
            ", ".join(device_labels) if device_labels else "none",
        )
        return device_list

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Fetch state data for a device."""
        config = await self._get_endpoint_config()
        device_base = config.get("endpoints", {}).get("RestApi", {}).get("device", {}).get("https")
        if not device_base:
            raise EosAPIError("Device service endpoint not found in config")

        return await self._make_request("GET", f"{device_base}/devices/state", params={"deviceId": device_id})

    async def get_latest_data(self, device_id: str) -> dict[str, Any]:
        """Fetch latest data for a device."""
        config = await self._get_endpoint_config()
        data_base = config.get("endpoints", {}).get("RestApi", {}).get("data", {}).get("https")
        if not data_base:
            raise EosAPIError("Data service endpoint not found in config")

        return await self._make_request("GET", f"{data_base}/data/latest-data", params={"deviceId": device_id})

    async def get_telemetry_history(
        self,
        device_id: str,
        *,
        start_timestamp: int | str,
        end_timestamp: int | str,
    ) -> dict[str, Any]:
        """Fetch historical telemetry between two instants (epoch ms or ISO8601 strings).

        Follows ``nextToken`` pagination until exhausted and merges all measurement rows
        into a single synthetic payload so callers don't need to handle pages.
        """
        config = await self._get_endpoint_config()
        data_base = config.get("endpoints", {}).get("RestApi", {}).get("data", {}).get("https")
        if not data_base:
            raise EosAPIError("Data service endpoint not found in config")

        all_measurements: list[dict[str, Any]] = []
        next_token: str | None = None
        last_payload: dict[str, Any] = {}
        request_index = 0

        while True:
            request_index += 1
            params: dict[str, str] = {
                "deviceId": device_id,
                "startTimestamp": str(start_timestamp),
                "endTimestamp": str(end_timestamp),
            }
            if next_token:
                params["nextToken"] = next_token

            last_payload = await self._make_request(
                "GET",
                f"{data_base}/data/telemetry-history",
                params=params,
            )

            page_measurements = last_payload.get("measurements")
            if isinstance(page_measurements, list):
                all_measurements.extend(m for m in page_measurements if isinstance(m, dict))

            next_token = last_payload.get("nextToken")
            if not next_token:
                break
            _LOGGER.debug(
                "EOS telemetry history request %d, following nextToken (device=%s)",
                request_index,
                device_id,
            )

        return {**last_payload, "measurements": all_measurements}

    async def set_profile(self, device_id: str, profile_index: int) -> None:
        """Select the active sauna program / profile slot (0-3)."""
        config = await self._get_endpoint_config()
        device_base = config.get("endpoints", {}).get("RestApi", {}).get("device", {}).get("https")
        if not device_base:
            raise EosAPIError("Device service endpoint not found in config")

        await self._make_request(
            "PATCH",
            f"{device_base}/devices/profile",
            json={"deviceId": device_id, "profile": str(profile_index)},
        )

    async def set_target(
        self,
        device_id: str,
        temperature: int,
        humidity: int,
        *,
        profile_index: int | None = None,
    ) -> None:
        """Set target temperature and humidity (optionally for a specific profile slot)."""
        config = await self._get_endpoint_config()
        device_base = config.get("endpoints", {}).get("RestApi", {}).get("device", {}).get("https")
        if not device_base:
            raise EosAPIError("Device service endpoint not found in config")

        body: dict[str, Any] = {
            "deviceId": device_id,
            "temperature": temperature,
            "humidity": humidity,
        }
        params: dict[str, str] = {"deviceId": device_id}
        if profile_index is not None:
            params["profileId"] = str(profile_index)

        await self._make_request(
            "PATCH",
            f"{device_base}/devices/target",
            json=body,
            params=params,
        )

    async def send_command(self, device_id: str, command: dict[str, Any]) -> dict[str, Any]:
        """POST a command payload to the device command endpoint."""
        config = await self._get_endpoint_config()
        device_base = config.get("endpoints", {}).get("RestApi", {}).get("device", {}).get("https")
        if not device_base:
            raise EosAPIError("Device service endpoint not found in config")

        return await self._make_request(
            "POST",
            f"{device_base}/devices/command",
            json={"deviceId": device_id, "command": command},
        )

    async def set_sauna_state(self, device_id: str, turn_on: bool) -> None:
        """Turn the sauna on or off."""
        await self.send_command(
            device_id,
            {"type": "SAUNA", "state": "on" if turn_on else "off"},
        )

    async def set_fan_state(self, device_id: str, turn_on: bool) -> None:
        """Turn the fan on or off."""
        await self.send_command(
            device_id,
            {"type": "FAN", "state": "on" if turn_on else "off"},
        )

    async def set_light_state(self, device_id: str, turn_on: bool) -> None:
        """Turn the sauna lights on or off."""
        await self.send_command(
            device_id,
            {"type": "LIGHTS", "state": "on" if turn_on else "off"},
        )

    async def close(self) -> None:
        """Close the API client session if owned by this instance."""
        if self._own_session and self._session:
            await self._session.close()
            self._session = None
