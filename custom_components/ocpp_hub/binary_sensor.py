"""Binary sensors: whether the charger is connected to the hub, and what its socket is doing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .core import readings
from .core.model import ChargerSnapshot
from .entity import OcppHubEntity, async_add_charger_entities
from .runtime import HubRuntime, OcppHubConfigEntry


@dataclass(frozen=True, kw_only=True)
class SocketSensorDescription(BinarySensorEntityDescription):
    """A binary sensor whose value is read from the charger snapshot."""

    value_fn: Callable[[ChargerSnapshot], bool | None]


# What the socket is doing, all derived from the status and error code the charger already reports.
SOCKET_SENSORS: tuple[SocketSensorDescription, ...] = (
    SocketSensorDescription(
        key="plugged_in",
        translation_key="plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=readings.plugged_in,
    ),
    SocketSensorDescription(
        key="car_charging",
        translation_key="car_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=readings.car_charging,
    ),
    SocketSensorDescription(
        key="problem",
        translation_key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=readings.problem,
    ),
)


# Create the connectivity sensor and the socket sensors of every charger that boots.
async def async_setup_entry(
    hass: HomeAssistant, entry: OcppHubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    # The binary sensors of one charger.
    def build(cp_id: str) -> list[OcppHubEntity]:
        return [
            ConnectedSensor(runtime, entry.entry_id, cp_id),
            *(SocketBinarySensor(runtime, entry.entry_id, cp_id, description) for description in SOCKET_SENSORS),
        ]

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


class SocketBinarySensor(OcppHubEntity, BinarySensorEntity):
    """One fact about the socket. Unavailable while the charger is not connected; unknown while the status is."""

    entity_description: SocketSensorDescription

    # Build the sensor for one charger from its description.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str, description: SocketSensorDescription) -> None:
        super().__init__(runtime, entry_id, cp_id, "binary_sensor", description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """The value from the latest snapshot."""
        return self.entity_description.value_fn(self._snapshot)

    @property
    def available(self) -> bool:
        """A charger that is gone has no socket to describe."""
        return self._snapshot.connected
