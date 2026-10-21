"""OCPP Hub: a local OCPP 1.6 central system for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers.typing import ConfigType

from .const import (
    BIND_HOST,
    BUILD_FILE,
    CONF_MAX_CURRENT,
    CONF_PORT,
    CONF_VENDOR_PASSWORD,
    CONF_VENDOR_URL,
    CONF_VENDOR_USER,
    DEFAULT_MAX_CURRENT,
    DOMAIN,
    PACKAGE_LOGGER,
    SERVICE_GET_SESSIONS,
)
from .core.central import CentralSystem
from .core.control import ControlPolicy
from .core.mirror import VendorMirror
from .core.mirror_model import MirrorSettings, MirrorSettingsError
from .runtime import HubListener, HubRuntime, OcppHubConfigEntry
from .session_store import SessionStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]


# The commit the running code comes from, as written by the deploy tool; "unknown" for a copy that was not deployed.
# A log line has to say which code wrote it, otherwise an old build and the current one look the same.
def _read_build_stamp() -> str:
    try:
        return (Path(__file__).parent / BUILD_FILE).read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


GET_SESSIONS_SCHEMA = vol.Schema(
    {vol.Optional("charger_id"): str, vol.Optional("limit"): vol.All(int, vol.Range(min=1))}
)


# Register the service that returns the charge history. It lives at the integration level, so it exists as soon as
# Home Assistant has loaded the integration, and it answers with data instead of changing anything.
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Return the stored charges, newest first, for one charger or all of them.
    async def get_sessions(call: ServiceCall) -> dict[str, object]:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_hub_loaded")
        sessions = entries[0].runtime_data.sessions.newest_first(call.data.get("charger_id"), call.data.get("limit"))
        return {"sessions": [session.to_dict() for session in sessions]}

    hass.services.async_register(
        DOMAIN, SERVICE_GET_SESSIONS, get_sessions, schema=GET_SESSIONS_SCHEMA, supports_response=SupportsResponse.ONLY
    )
    return True


# The ceiling for writes from the entry options. A value that cannot be right falls back to the default instead of
# widening what is allowed; the error says so.
def _control_policy(entry: OcppHubConfigEntry) -> ControlPolicy:
    wanted = entry.options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)
    try:
        return ControlPolicy(max_current_a=float(wanted))
    except TypeError, ValueError:
        _LOGGER.error("The current ceiling %r is not valid; using %s A", wanted, DEFAULT_MAX_CURRENT)
        return ControlPolicy(max_current_a=float(DEFAULT_MAX_CURRENT))


# The mirror settings from the entry options, or None when no vendor URL is set.
# Options are validated by the options flow, so an invalid combination here means the entry was edited by hand;
# the error says what is wrong and the hub runs without a mirror instead of not at all.
def _mirror_settings(entry: OcppHubConfigEntry) -> MirrorSettings | None:
    url = entry.options.get(CONF_VENDOR_URL)
    if not url:
        return None
    try:
        return MirrorSettings(
            url=url,
            user=entry.options.get(CONF_VENDOR_USER) or None,
            password=entry.options.get(CONF_VENDOR_PASSWORD) or None,
        )
    except MirrorSettingsError as error:
        _LOGGER.error("The vendor mirror is off, its options are invalid (%s): %s", error.code, error)
        return None


# Set up one config entry: start the OCPP server and, when a vendor URL is set, the mirror in front of it.
# A port that is taken is not fatal: the other OCPP integration may still be shutting down, so setup is retried.
async def async_setup_entry(hass: HomeAssistant, entry: OcppHubConfigEntry) -> bool:
    logging.getLogger(PACKAGE_LOGGER).setLevel(logging.INFO)
    build = await hass.async_add_executor_job(_read_build_stamp)
    settings = _mirror_settings(entry)
    sessions = SessionStore(hass)
    await sessions.async_load()
    listener = HubListener(hass, entry.entry_id, build, sessions, mirror_enabled=settings is not None)
    mirror = VendorMirror(settings) if settings is not None else None
    if mirror is not None:
        await mirror.start()
    central = CentralSystem([listener, *([mirror] if mirror is not None else [])], policy=_control_policy(entry))
    port = entry.data[CONF_PORT]
    try:
        await central.start(BIND_HOST, port)
    except OSError as error:
        if mirror is not None:
            await mirror.stop()
        raise ConfigEntryNotReady(f"cannot listen on port {port}: {error}") from error
    try:
        entry.runtime_data = HubRuntime(central, listener, mirror, sessions)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        # Home Assistant never unloads an entry whose setup failed or was cancelled, so nothing else would close the
        # server and the mirror, and the port would stay taken until a restart. Clean up, then let the error go on.
        await _stop_runtime(central, mirror)
        raise
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    _LOGGER.info(
        "[build %s] OCPP Hub listening on port %s, vendor mirror %s", build, central.port, "on" if mirror else "off"
    )
    return True


# Reload the entry when its options change, so a new vendor URL takes effect without a restart.
# Reloading drops the charger connection; it redials by itself.
async def _async_reload_entry(hass: HomeAssistant, entry: OcppHubConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


# Close the server, then the mirror, and close the mirror even when closing the server raised.
# No link to the vendor may outlive the entry. Frames still waiting for the vendor are lost; the README lists that.
async def _stop_runtime(central: CentralSystem, mirror: VendorMirror | None) -> None:
    try:
        await central.stop()
    finally:
        if mirror is not None:
            await mirror.stop()


# Unload one config entry: remove the entities, close the chargers, then the mirror.
async def async_unload_entry(hass: HomeAssistant, entry: OcppHubConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        try:
            await _stop_runtime(entry.runtime_data.central, entry.runtime_data.mirror)
        finally:
            await entry.runtime_data.sessions.async_flush()
    return unloaded
