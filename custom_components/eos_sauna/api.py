"""API client for the EOS cloud API."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import ClientError

from .const import (
    API_TOKEN_TIMEOUT,
    DEFAULT_STAGE,
    LOCAL_API_PREFIX,
    LOCAL_SETCLD_PATH,
    LOCAL_SWITCH_KEYS,
    LOCAL_TIMEOUT_SEC,
    endpoint_config_url,
)

_LOGGER = logging.getLogger(__name__)

# Only these statuses from the auth endpoints mean "the credentials were refused".
# Anything else (5xx, 429, a gateway error) is a transient service problem and must
# not be reported as an authentication failure: Home Assistant stops polling for good
# after ConfigEntryAuthFailed, so a network blip would otherwise kill the integration.
_AUTH_REJECTED_STATUSES = (400, 401, 403)
_REQUEST_TIMEOUT_SEC = 15


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
        local_host: str | None = None,
    ) -> None:
        """Initialize the EOS API client."""
        self._local_host = (local_host or "").strip().rstrip("/") or None
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
            try:
                async with session.get(
                    endpoint_config_url(self._stage),
                    timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SEC),
                ) as response:
                    if response.status != 200:
                        raise EosAPIError(f"Failed to fetch endpoint config: {response.status}")
                    self._endpoint_config = await response.json()
            except (ClientError, TimeoutError) as err:
                raise EosAPIError("network_error") from err
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
            async with session.post(
                f"{auth_base}/auth/token",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SEC),
            ) as response:
                if response.status in _AUTH_REJECTED_STATUSES:
                    _LOGGER.warning("EOS auth rejected (%s)", response.status)
                    raise EosAuthenticationError("invalid_auth")
                if response.status != 200:
                    _LOGGER.warning("EOS auth service error (%s)", response.status)
                    raise EosAPIError(f"auth_http_{response.status}")
                data: dict[str, Any] = await response.json()
        except (ClientError, TimeoutError) as err:
            # A network failure is NOT a credential failure — keep it an EosAPIError so
            # the coordinator raises UpdateFailed and keeps polling.
            raise EosAPIError("network_error") from err

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

        data: dict[str, Any] | None = None
        try:
            async with session.post(
                f"{auth_base}/auth/refresh",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SEC),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                else:
                    _LOGGER.warning("EOS token refresh failed (%s), re-authenticating", response.status)
        except (ClientError, TimeoutError) as err:
            raise EosAPIError("network_error") from err

        if data is None:
            await self._authenticate()
            return

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
                    body = (await response.text())[:200]
                    _LOGGER.warning(
                        "EOS API request failed (%s) %s %s: %s", response.status, method, url, body
                    )
                    raise EosAPIError(f"http_{response.status}: {body}")

                return dict(await response.json())

        except (ClientError, TimeoutError) as err:
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

    async def _local_setcld(self, payload: dict[str, int]) -> dict[str, Any]:
        """Write a command record to the controller over the LAN.

        Returns the record the controller echoes back, so the caller can confirm the
        value it asked for was accepted.
        """
        if not self._local_host:
            raise EosAPIError("No local host configured")

        url = f"http://{self._local_host}{LOCAL_API_PREFIX}{LOCAL_SETCLD_PATH}"
        session = self._get_session()
        # The controller's own page posts a bare JSON body terminated by CRLF, with no
        # JSON content type. It rejects a request without the trailing CRLF.
        async with session.post(
            url,
            data=json.dumps(payload) + "\r\n",
            headers={"Content-Type": "text/plain;charset=UTF-8"},
            timeout=aiohttp.ClientTimeout(total=LOCAL_TIMEOUT_SEC),
        ) as response:
            text = await response.text()
            if response.status != 200:
                raise EosAPIError(f"Local command failed: http_{response.status}: {text[:200]}")
            try:
                return json.loads(text)
            except ValueError as err:
                raise EosAPIError(f"Local command returned non-JSON: {text[:200]}") from err

    async def _set_switch(self, device_id: str, command_type: str, turn_on: bool) -> None:
        """Flip one of the controller's switches, over the LAN when that is possible.

        The LAN path is tried first because the cloud command endpoint is the one piece of
        this API that fails on some accounts; a failure there falls back to the cloud so a
        misconfigured or unplugged controller still behaves as it did before.
        """
        key = LOCAL_SWITCH_KEYS.get(command_type)
        if self._local_host and key:
            want = 1 if turn_on else 0
            try:
                echoed = await self._local_setcld({key: want})
                if echoed.get(key) == want:
                    return
                _LOGGER.warning(
                    "Local %s command was accepted but echoed %s=%s, falling back to cloud",
                    command_type,
                    key,
                    echoed.get(key),
                )
            except (EosAPIError, ClientError, TimeoutError) as err:
                _LOGGER.warning(
                    "Local %s command failed (%s), falling back to cloud", command_type, err
                )

        await self.send_command(
            device_id,
            {"type": command_type, "state": "on" if turn_on else "off"},
        )

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
        if self._local_host:
            # Locally the live setpoint is writable directly, so no profile slot is needed.
            try:
                echoed = await self._local_setcld({"Tc": int(temperature), "Hc": int(humidity)})
                if echoed.get("Tc") == int(temperature):
                    return
                _LOGGER.warning(
                    "Local target was accepted but echoed Tc=%s, falling back to cloud",
                    echoed.get("Tc"),
                )
            except (EosAPIError, ClientError, TimeoutError) as err:
                _LOGGER.warning("Local target write failed (%s), falling back to cloud", err)

        if profile_index is None:
            # The cloud endpoint rejects a target write without a profile slot, and some
            # controllers never report which slot is active -- see set_target's caller.
            raise EosAPIError(
                "The controller does not report which program is active, so Home Assistant "
                "cannot tell which program slot to write the target into. Pick a program "
                "(preset) on the thermostat card first, or set a local host in the "
                "integration's options to write the target directly."
            )

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
        await self._set_switch(device_id, "SAUNA", turn_on)

    async def set_fan_state(self, device_id: str, turn_on: bool) -> None:
        """Turn the fan on or off."""
        await self._set_switch(device_id, "FAN", turn_on)

    async def set_light_state(self, device_id: str, turn_on: bool) -> None:
        """Turn the sauna lights on or off."""
        await self._set_switch(device_id, "LIGHTS", turn_on)

    async def close(self) -> None:
        """Close the API client session if owned by this instance."""
        if self._own_session and self._session:
            await self._session.close()
            self._session = None
