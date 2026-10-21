"""Start and stop a charge from Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .control import run_write
from .core import readings
from .entity import OcppHubEntity, async_add_charger_entities
from .runtime import HubRuntime, OcppHubConfigEntry


# Create the charging switch of every charger that boots.
async def async_setup_entry(
    hass: HomeAssistant, entry: OcppHubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    # The switches of one charger.
    def build(cp_id: str) -> list[OcppHubEntity]:
        return [ChargingSwitch(runtime, entry.entry_id, cp_id)]

    async_add_charger_entities(hass, entry, async_add_entities, build)


class ChargingSwitch(OcppHubEntity, SwitchEntity):
    """On while a charge runs. Turning it on starts a charge with RemoteStartTransaction, off stops the running one.

    A start only works when a car is connected; the charger refuses otherwise and the owner is told so.
    """

    _attr_translation_key = "charging"

    # Build the switch for one charger.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str) -> None:
        super().__init__(runtime, entry_id, cp_id, "switch", "charging")

    @property
    def is_on(self) -> bool:
        """True while a charge is running on the socket."""
        return readings.charge_running(self._snapshot)

    @property
    def available(self) -> bool:
        """Only a connected charger can be written to."""
        return self._snapshot.connected

    # Ask the charger to start a charge.
    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_write(self._runtime, self._cp_id, lambda control: control.remote_start())

    # Ask the charger to stop the running charge.
    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_write(self._runtime, self._cp_id, lambda control: control.remote_stop())
