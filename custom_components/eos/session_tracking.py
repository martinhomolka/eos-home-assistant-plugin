"""Local sauna session duration and this-week session count from telemetry-history."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .api import EosAPI, EosAPIError, EosAuthenticationError
from .const import SESSION_MIN_DURATION_SEC

_LOGGER = logging.getLogger(__name__)


@dataclass
class EosDeviceSessionRuntime:
    """Local session timing for one device (duration from on/off edges)."""

    session_active: bool = False
    session_start_monotonic: float | None = None
    last_session_duration_min: float = 0.0


def parse_measurement_timestamp_ms(value: Any) -> int | None:
    """Parse telemetry row timestamp to epoch milliseconds."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def normalize_session_id(raw: Any) -> str | None:
    """Return a canonical session id string so int/float/string JSON values dedupe correctly."""
    try:
        f = float(str(raw).strip())
    except (TypeError, ValueError):
        s = str(raw).strip()
        return s or None
    if not f.is_integer() or f < 0:
        s = str(raw).strip()
        return s or None
    return str(int(f))


def measurements_from_history_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize telemetry-history JSON to a list of measurement dicts."""
    raw = payload.get("measurements")
    if raw is None and isinstance(payload.get("data"), dict):
        raw = payload["data"].get("measurements")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def count_unique_sessions_this_week(
    measurements: list[dict[str, Any]],
    *,
    week_start_ms: int,
) -> int:
    """Count distinct sessionId values with timestamp on or after Monday 00:00 local."""
    week_ids: set[str] = set()
    for row in measurements:
        sid_s = normalize_session_id(row.get("sessionId"))
        if not sid_s:
            continue
        timestamp = parse_measurement_timestamp_ms(row.get("timestamp"))
        if timestamp is None or timestamp < week_start_ms:
            continue
        week_ids.add(sid_s)
    return len(week_ids)


class EosSessionTracking:
    """Last-session duration from heat edges; weekly session count from telemetry-history."""

    def __init__(self, api: EosAPI) -> None:
        self._api = api
        self._session_runtime: dict[str, EosDeviceSessionRuntime] = {}
        self._telemetry_session_counts: dict[str, dict[str, int]] = {}

    def _session_state(self, device_id: str) -> EosDeviceSessionRuntime:
        if device_id not in self._session_runtime:
            self._session_runtime[device_id] = EosDeviceSessionRuntime()
        return self._session_runtime[device_id]

    def tick_local_for_device(self, device_id: str, *, heat_on: bool) -> None:
        """Update last session duration from sauna on/off edges."""
        rt = self._session_state(device_id)
        now = time.monotonic()
        min_sec = float(SESSION_MIN_DURATION_SEC)

        if heat_on and not rt.session_active:
            rt.session_active = True
            rt.session_start_monotonic = now

        elif heat_on and rt.session_active:
            pass

        elif not heat_on and rt.session_active:
            rt.session_active = False
            start = rt.session_start_monotonic
            rt.session_start_monotonic = None
            if start is not None:
                duration_sec = now - start
                duration_min = duration_sec / 60.0
                if duration_sec >= min_sec:
                    rt.last_session_duration_min = round(duration_min, 1)

    async def refresh_telemetry_for_device(self, device_id: str, api: EosAPI) -> None:
        """Fetch telemetry history since week start and set this week's unique session count."""
        now = dt_util.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())

        now_ms = int(now.timestamp() * 1000)
        week_start_ms = int(week_start.timestamp() * 1000)
        end_ms = now_ms + 300_000

        payload = await api.get_telemetry_history(
            device_id,
            start_timestamp=week_start_ms,
            end_timestamp=end_ms,
        )
        measurements = measurements_from_history_payload(payload)

        week_n = count_unique_sessions_this_week(
            measurements,
            week_start_ms=week_start_ms,
        )
        self._telemetry_session_counts[device_id] = {"week": week_n}

    async def refresh_session_counts_for_devices(self, device_ids: list[str]) -> None:
        """Refresh weekly session counts from the API (runs every coordinator poll)."""

        async def refresh_one(device_id: str) -> None:
            try:
                await self.refresh_telemetry_for_device(device_id, self._api)
            except EosAuthenticationError:
                _LOGGER.warning(
                    "Session history refresh failed for device %s: authentication error",
                    device_id,
                )
            except EosAPIError as err:
                _LOGGER.warning(
                    "Session history refresh failed for device %s: %s",
                    device_id,
                    err,
                )

        await asyncio.gather(*(refresh_one(device_id) for device_id in device_ids))

    def telemetry_counts_for_coordinator_data(self) -> dict[str, dict[str, int]]:
        """Shallow copy for coordinator ``data`` payload."""
        return {k: dict(v) for k, v in self._telemetry_session_counts.items()}

    def get_sessions_this_week_telemetry(self, device_id: str) -> int | None:
        data = self._telemetry_session_counts.get(device_id)
        if data is None:
            return None
        return int(data["week"])

    def get_last_session_duration_minutes(self, device_id: str) -> float | None:
        rt = self._session_runtime.get(device_id)
        if rt is None or rt.last_session_duration_min <= 0:
            return None
        return rt.last_session_duration_min
