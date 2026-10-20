"""The base of every OCPP Hub entity: one charger, one device, and the snapshots that arrive for it."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .const import DOMAIN, new_charger_signal, snapshot_signal
from .core.model import ChargerSnapshot
from .runtime import HubRuntime, OcppHubConfigEntry


class OcppHubEntity(Entity):
    """An entity of one charger that follows that charger's snapshots."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    # Set the identity once. The entity id is built from the stable key, because Home Assistant would otherwise derive
    # it from the name, which depends on the language of the instance, and an id must never change with that.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str, platform: str, key: str) -> None:
        self._runtime = runtime
        self._entry_id = entry_id
        self._cp_id = cp_id
        self._snapshot: ChargerSnapshot = runtime.listener.snapshots[cp_id]
        self._attr_unique_id = f"{cp_id}_{key}"
        self.entity_id = f"{platform}.{DOMAIN}_{slugify(cp_id)}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """One device per charger, described by what its BootNotification said."""
        info = self._snapshot.info
        return DeviceInfo(
            identifiers={(DOMAIN, self._cp_id)},
            name=f"Charger {self._cp_id}",
            manufacturer=info.vendor,
            model=info.model,
            sw_version=info.firmware,
            serial_number=info.serial,
        )

    # Follow the snapshots of this charger for as long as the entity exists.
    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, snapshot_signal(self._entry_id, self._cp_id), self._handle_snapshot)
        )

    # Store the new snapshot and show it.
    @callback
    def _handle_snapshot(self, snapshot: ChargerSnapshot) -> None:
        self._snapshot = snapshot
        self.async_write_ha_state()


# Create the entities of every charger that has booted, now and later.
# `build` makes the entities of one charger; a charger is only ever built once, however often it reconnects.
@callback
def async_add_charger_entities(
    hass: HomeAssistant,
    entry: OcppHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[str], list[OcppHubEntity]],
) -> None:
    built: set[str] = set()

    # Build the entities of one charger unless that already happened.
    @callback
    def add(cp_id: str) -> None:
        if cp_id not in built:
            built.add(cp_id)
            async_add_entities(build(cp_id))

    for cp_id in entry.runtime_data.listener.announced:
        add(cp_id)
    entry.async_on_unload(async_dispatcher_connect(hass, new_charger_signal(entry.entry_id), add))
