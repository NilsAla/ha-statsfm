"""Config flow for stats.fm."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StatsFmClient, StatsFmError, StatsFmNotFoundError
from .const import CONF_USER_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema({vol.Required(CONF_USER_ID): str})


def _normalize(raw: str) -> str:
    """Accept 'name', '@name' or a profile URL like https://stats.fm/user/name."""
    value = raw.strip().rstrip("/")
    if "stats.fm/" in value:
        value = value.split("?", 1)[0].rsplit("/", 1)[-1]
    return value.lstrip("@")


class StatsFmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for stats.fm."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the stats.fm user and validate it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_id = _normalize(user_input[CONF_USER_ID])
            client = StatsFmClient(async_get_clientsession(self.hass), user_id)
            try:
                user = await client.user()
            except StatsFmNotFoundError:
                errors["base"] = "user_not_found"
            except StatsFmError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating stats.fm user")
                errors["base"] = "unknown"
            else:
                privacy = user.get("privacySettings") or {}
                if privacy.get("streamStats") is False:
                    errors["base"] = "stats_private"
                else:
                    # Immutable ID as unique_id; customId can be renamed by the user.
                    await self.async_set_unique_id(user["id"])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"stats.fm ({user.get('displayName') or user_id})",
                        data={CONF_USER_ID: user["id"]},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input),
            errors=errors,
        )
