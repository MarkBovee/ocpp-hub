"""A button that removes the hub's charge limit from the charger."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .control import run_write
from .entity import OcppHubEntity, async_add_charger_entities
from .runtime import HubRuntime, OcppHubConfigEntry


# Create the clear button of every charger that boots.
async def async_setup_entry(
    hass: HomeAssistant, entry: OcppHubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    # The buttons of one charger.
    def build(cp_id: str) -> list[OcppHubEntity]:
        return [ClearLimitButton(runtime, entry.entry_id, cp_id)]

    async_add_charger_entities(hass, entry, async_add_entities, build)


class ClearLimitButton(OcppHubEntity, ButtonEntity):
    """Removes the profile the hub installed, so the charger goes back to its own limits.

    It is the way out of a pause that the hub set, for example when the number shows nothing after a restart.
    """

    _attr_translation_key = "clear_charge_limit"

    # Build the button for one charger.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str) -> None:
        super().__init__(runtime, entry_id, cp_id, "button", "clear_charge_limit")

    @property
    def available(self) -> bool:
        """Only a connected charger can be written to."""
        return self._snapshot.connected

    # Ask the charger to drop the hub's profile.
    async def async_press(self) -> None:
        await run_write(self._runtime, self._cp_id, lambda control: control.clear_current_limit())
