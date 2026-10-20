"""Config flow: one OCPP Hub per Home Assistant, set up with a port; the vendor mirror is set in the options."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import CONF_PORT, CONF_VENDOR_PASSWORD, CONF_VENDOR_URL, CONF_VENDOR_USER, DEFAULT_PORT, DOMAIN
from .core.mirror_model import MirrorSettings, MirrorSettingsError

# The form field each settings error belongs to; the others are shown on the form as a whole.
ERROR_FIELDS = {
    "invalid_url": CONF_VENDOR_URL,
    "credentials_in_url": CONF_VENDOR_URL,
    "credentials_incomplete": CONF_VENDOR_USER,
}


# Turn the submitted options form into the options to store, or raise MirrorSettingsError when they cannot work.
# An empty URL switches the mirror off and forgets the credentials. A password left empty keeps the stored one, because
# the form never shows it; that only applies while a user name is still set, so a password cannot linger without one.
def vendor_options(form: Mapping[str, Any], stored: Mapping[str, Any]) -> dict[str, str]:
    url = (form.get(CONF_VENDOR_URL) or "").strip()
    if not url:
        return {}
    user = (form.get(CONF_VENDOR_USER) or "").strip()
    password = form.get(CONF_VENDOR_PASSWORD) or (stored.get(CONF_VENDOR_PASSWORD, "") if user else "")
    MirrorSettings(url=url, user=user or None, password=password or None)
    options = {CONF_VENDOR_URL: url}
    if user:
        options[CONF_VENDOR_USER] = user
    if password:
        options[CONF_VENDOR_PASSWORD] = password
    return options


class OcppHubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup form."""

    VERSION = 1

    # Give the entry an options flow, where the vendor mirror is set.
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OcppHubOptionsFlow:
        return OcppHubOptionsFlow()

    # Show the port form and create the entry once it is submitted.
    # The port is the only setting the charger side needs; everything else is configured per charger later.
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="OCPP Hub", data=user_input)
        schema = vol.Schema(
            {vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(int, vol.Range(min=1024, max=65535))}
        )
        return self.async_show_form(step_id="user", data_schema=schema)


class OcppHubOptionsFlow(OptionsFlow):
    """The options form of the vendor mirror."""

    # Show the form, check what was submitted, and store the options when they can work.
    # The password is never filled in on the form; leaving it empty keeps the stored one.
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                return self.async_create_entry(data=vendor_options(user_input, self.config_entry.options))
            except MirrorSettingsError as error:
                errors[ERROR_FIELDS.get(error.code, "base")] = error.code
        shown = user_input if user_input is not None else self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_VENDOR_URL, description={"suggested_value": shown.get(CONF_VENDOR_URL, "")}): str,
                vol.Optional(CONF_VENDOR_USER, description={"suggested_value": shown.get(CONF_VENDOR_USER, "")}): str,
                vol.Optional(CONF_VENDOR_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
