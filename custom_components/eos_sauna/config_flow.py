"""Config flow for EOS integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .api import EosAPI, EosAPIError, EosAuthenticationError
from .const import AVAILABLE_STAGES, CONF_STAGE, DEFAULT_STAGE, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


def _build_credentials_schema(
    email_default: str = "",
    stage_default: str = DEFAULT_STAGE,
) -> vol.Schema:
    """Build the email/password/stage form schema, with optional prefilled defaults."""
    email_field = vol.Required(CONF_EMAIL, default=email_default) if email_default else vol.Required(CONF_EMAIL)
    return vol.Schema(
        {
            email_field: str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_STAGE, default=stage_default): SelectSelector(
                SelectSelectorConfig(
                    options=list(AVAILABLE_STAGES),
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_STAGE,
                )
            ),
        }
    )


STEP_USER_DATA_SCHEMA = _build_credentials_schema()


def _unique_id_for(email: str, stage: str) -> str:
    """Return the unique_id for a given email/stage pair.

    Prod keeps the bare email to preserve uniqueness checks against pre-stage-selector installs.
    """
    return email if stage == DEFAULT_STAGE else f"{stage}:{email}"


def _entry_title_for(email: str, stage: str) -> str:
    """Return the config entry display title for a given email/stage pair."""
    return email if stage == DEFAULT_STAGE else f"{email} ({stage})"


async def _validate_credentials(hass: Any, email: str, password: str, stage: str) -> dict[str, str]:
    """Try authenticating; return an `errors` dict (empty on success)."""
    errors: dict[str, str] = {}
    try:
        session = aiohttp_client.async_get_clientsession(hass)
        api = EosAPI(email=email, password=password, session=session, stage=stage)
        await api.get_devices()
    except EosAuthenticationError:
        errors["base"] = "invalid_auth"
    except EosAPIError:
        _LOGGER.exception("EOS API error while validating credentials")
        errors["base"] = "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected exception while validating credentials")
        errors["base"] = "unknown"
    return errors


def _build_options_schema(options: dict[str, Any]) -> vol.Schema:
    """Build the options schema."""
    return vol.Schema(
        {
            vol.Required(
                "poll_interval_seconds",
                default=options.get(
                    "poll_interval_seconds",
                    int(UPDATE_INTERVAL.total_seconds()),
                ),
            ): vol.Coerce(int)
        }
    )


class EosConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EOS."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EosOptionsFlow:
        """Return the options flow for this handler."""
        return EosOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        stage = user_input.get(CONF_STAGE, DEFAULT_STAGE)
        await self.async_set_unique_id(_unique_id_for(user_input[CONF_EMAIL], stage))
        self._abort_if_unique_id_configured()

        errors = await _validate_credentials(
            self.hass,
            email=user_input[CONF_EMAIL],
            password=user_input[CONF_PASSWORD],
            stage=stage,
        )

        if not errors:
            return self.async_create_entry(
                title=_entry_title_for(user_input[CONF_EMAIL], stage),
                data=user_input,
            )

        # Rebuild the schema with the submitted email and stage so a retry doesn't silently snap
        # the stage selector back to prod while the user thinks they're still on alpha/dev.
        return self.async_show_form(
            step_id="user",
            data_schema=_build_credentials_schema(user_input[CONF_EMAIL], stage),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Allow editing credentials and/or backend stage on an existing entry.

        Credentials are re-validated against the (possibly new) stage before the entry is updated.
        """
        reconfigure_entry = self._get_reconfigure_entry()
        existing_email = reconfigure_entry.data.get(CONF_EMAIL, "")
        existing_stage = reconfigure_entry.data.get(CONF_STAGE, DEFAULT_STAGE)

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_build_credentials_schema(existing_email, existing_stage),
            )

        stage = user_input.get(CONF_STAGE, DEFAULT_STAGE)
        new_unique_id = _unique_id_for(user_input[CONF_EMAIL], stage)

        # If the resulting unique_id would collide with a different existing entry, refuse.
        for entry in self._async_current_entries(include_ignore=False):
            if entry.entry_id != reconfigure_entry.entry_id and entry.unique_id == new_unique_id:
                return self.async_abort(reason="already_configured")

        errors = await _validate_credentials(
            self.hass,
            email=user_input[CONF_EMAIL],
            password=user_input[CONF_PASSWORD],
            stage=stage,
        )

        if errors:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_build_credentials_schema(user_input[CONF_EMAIL], stage),
                errors=errors,
            )

        return self.async_update_reload_and_abort(
            reconfigure_entry,
            unique_id=new_unique_id,
            title=_entry_title_for(user_input[CONF_EMAIL], stage),
            data_updates=user_input,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauth when the cloud rejects the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new password for the existing account and stage."""
        reauth_entry = self._get_reauth_entry()
        email = reauth_entry.data.get(CONF_EMAIL, "")
        stage = reauth_entry.data.get(CONF_STAGE, DEFAULT_STAGE)
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await _validate_credentials(
                self.hass,
                email=email,
                password=user_input[CONF_PASSWORD],
                stage=stage,
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": email},
            errors=errors,
        )


class EosOptionsFlow(config_entries.OptionsFlow):
    """Handle EOS options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the EOS options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_schema(dict(self.config_entry.options)),
        )
