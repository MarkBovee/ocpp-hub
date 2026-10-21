"""Binary sensors: whether the charger is connected to the hub."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import OcppHubEntity, async_add_charger_entities
from .runtime import HubRuntime, OcppHubConfigEntry


# Create the connectivity sensor of every charger that boots.
async def async_setup_entry(
    hass: HomeAssistant, entry: OcppHubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    # The binary sensors of one charger.
    def build(cp_id: str) -> list[OcppHubEntity]:
        return [ConnectedSensor(runtime, entry.entry_id, cp_id)]

    async_add_charger_entities(hass, entry, async_add_entities, build)


class ConnectedSensor(OcppHubEntity, BinarySensorEntity):
    """On while the charger has an open connection to the hub. Always available: off is a valid answer."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    # Build the sensor for one charger.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str) -> None:
        super().__init__(runtime, entry_id, cp_id, "binary_sensor", "connected")

    @property
    def is_on(self) -> bool:
        """True while the charger is connected."""
        return self._snapshot.connected
