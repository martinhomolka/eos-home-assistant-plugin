"""Constants for the EOS integration."""

from datetime import timedelta

DOMAIN = "eos_sauna"

API_TOKEN_TIMEOUT = 3600  # 1 hour in seconds

# Backend stages: prod is the public backend, alpha/dev are internal environments.
CONF_STAGE = "stage"
STAGE_PROD = "prod"
STAGE_ALPHA = "alpha"
STAGE_DEV = "dev"
DEFAULT_STAGE = STAGE_PROD
AVAILABLE_STAGES = (STAGE_PROD, STAGE_ALPHA, STAGE_DEV)


def endpoint_config_url(stage: str) -> str:
    """Return the endpoints discovery URL for the given backend stage.

    Prod historically uses the unprefixed hostname; alpha and dev use a stage prefix.
    """
    if stage == STAGE_PROD:
        return "https://api.harvia.io/endpoints"
    return f"https://{stage}.api.harvia.io/endpoints"


UPDATE_INTERVAL = timedelta(seconds=60)

# Minimum seconds a "heat on" interval must last to count as one session.
SESSION_MIN_DURATION_SEC = 30

# Device type strings as reported by the /devices API.
DEVICE_TYPE_COMMAND = "EOS_Command"
DEVICE_TYPE_H2N = "EOS_H2N"
DEVICE_TYPE_DONGLE = "EOS_Dongle"
DEVICE_TYPE_SAUNA_SENSOR = "SaunaSensor"

# Device types that expose full sauna control (climate, light, fan, profiles).
CONTROLLER_DEVICE_TYPES = {DEVICE_TYPE_COMMAND, DEVICE_TYPE_H2N, DEVICE_TYPE_DONGLE}

# Device types that are sensor-only (temperature, humidity, status).
SENSOR_DEVICE_TYPES = {DEVICE_TYPE_SAUNA_SENSOR}

# SaunaSensor `saunaStatus` integer values mapped to human-readable strings.
SAUNA_SENSOR_STATUS_BY_CODE = {
    -1: "error",
    0: "off",
    1: "heating",
    2: "ready",
    3: "cooling",
}
SAUNA_SENSOR_STATUS_OPTIONS = list(SAUNA_SENSOR_STATUS_BY_CODE.values())
