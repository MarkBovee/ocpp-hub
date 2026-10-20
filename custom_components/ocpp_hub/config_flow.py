"""Config flow: one OCPP Hub per Home Assistant, configured with the port the chargers connect to."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_PORT, DEFAULT_PORT, DOMAIN


class OcppHubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup form."""

    VERSION = 1

    # Intent: show the port form and create the entry once it is submitted.
    # Why: the port is the only setting the charger side needs; everything else is configured per charger later.
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="OCPP Hub", data=user_input)
        schema = vol.Schema(
            {vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(int, vol.Range(min=1024, max=65535))}
        )
        return self.async_show_form(step_id="user", data_schema=schema)
