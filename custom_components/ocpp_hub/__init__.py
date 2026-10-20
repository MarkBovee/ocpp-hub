"""OCPP Hub: a local OCPP 1.6 central system for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# Set up one config entry of OCPP Hub.
# The OCPP server arrives with the next change; this shell lets the integration be added, deployed and tested now.
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}
    _LOGGER.info("OCPP Hub is set up without a server yet (scaffold release)")
    return True


# Unload one config entry and drop its runtime data.
# Home Assistant calls this on reload and removal; leaving data behind would leak state into the next setup.
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
