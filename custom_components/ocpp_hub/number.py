"""The charge limit: a number that sets the current per phase the charger may use."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .control import run_write
from .core.control import CHARGING_MIN_A
from .entity import OcppHubEntity, async_add_charger_entities
from .runtime import HubRuntime, OcppHubConfigEntry


# Create the charge limit of every charger that boots.
async def async_setup_entry(
    hass: HomeAssistant, entry: OcppHubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    # The numbers of one charger.
    def build(cp_id: str) -> list[OcppHubEntity]:
        return [ChargeLimitNumber(runtime, entry.entry_id, cp_id)]

    async_add_charger_entities(hass, entry, async_add_entities, build)


class ChargeLimitNumber(OcppHubEntity, NumberEntity):
    """The current per phase the hub asks the charger to stay under. Below 6 A it pauses the charger.

    The value shown is what the charger last accepted from the hub on this connection; the charger is not asked what it
    holds, so after a reconnect it shows nothing until the limit is set again.
    """

    _attr_translation_key = "charge_limit"
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    # Build the number for one charger. The range ends at the owner's ceiling.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str) -> None:
        super().__init__(runtime, entry_id, cp_id, "number", "charge_limit")
        self._attr_native_min_value = 0
        self._attr_native_max_value = runtime.central.policy.max_current_a

    @property
    def native_value(self) -> float | None:
        """The limit the charger accepted, or None while the hub has set none on this connection."""
        return self._snapshot.charge_limit_a

    @property
    def available(self) -> bool:
        """Only a connected charger can be written to."""
        return self._snapshot.connected

    # Send the limit to the charger. A value under 6 A pauses it; the control refuses anything above the ceiling.
    async def async_set_native_value(self, value: float) -> None:
        await run_write(self._runtime, self._cp_id, lambda control: control.set_current_limit(value))

    @property
    def extra_state_attributes(self) -> dict[str, float | str | None]:
        """The pause threshold and the profile purpose the charger accepted, for someone reading the state."""
        return {
            "pauses_below_a": CHARGING_MIN_A,
            "profile_purpose": self._snapshot.limit_purpose,
            "vendor_limit_a": self._snapshot.vendor_limit_a,
        }
