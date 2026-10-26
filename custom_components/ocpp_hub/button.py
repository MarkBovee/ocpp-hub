"""A button that removes the hub's charge limit from the charger."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SCHEDULE_ENTITY, CONF_SCHEDULE_INSIDE, CONF_SCHEDULE_OUTSIDE, DOMAIN
from .control import run_write
from .core.schedule_plan import SchedulePlanError, plan_week
from .entity import OcppHubEntity, async_add_charger_entities
from .runtime import HubRuntime, OcppHubConfigEntry
from .schedule_helper import anchor_for, read_blocks


# Create the clear button of every charger that boots, and the apply-schedule button when a schedule helper is chosen.
async def async_setup_entry(
    hass: HomeAssistant, entry: OcppHubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    # The buttons of one charger.
    def build(cp_id: str) -> list[OcppHubEntity]:
        buttons: list[OcppHubEntity] = [ClearLimitButton(runtime, entry.entry_id, cp_id)]
        if entry.options.get(CONF_SCHEDULE_ENTITY):
            buttons.append(ApplyScheduleButton(runtime, entry, cp_id))
        return buttons

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


class ApplyScheduleButton(OcppHubEntity, ButtonEntity):
    """Writes the schedule helper to the charger when pressed: the one moment the schedule reaches the charger.

    The helper's blocks are the times in which the charger may charge (at the inside limit, or the limit in the block's
    own data); outside them it is held at the outside limit, which is a pause unless set otherwise. Changing the helper
    writes nothing by itself.
    """

    _attr_translation_key = "apply_schedule"

    # Build the button for one charger, keeping the options that say which helper and which limits.
    def __init__(self, runtime: HubRuntime, entry: OcppHubConfigEntry, cp_id: str) -> None:
        super().__init__(runtime, entry.entry_id, cp_id, "button", "apply_schedule")
        self._options = entry.options

    @property
    def available(self) -> bool:
        """Only a connected charger can be written to."""
        return self._snapshot.connected

    # Read the helper, plan it under the ceiling and write it. A helper that is missing, unavailable or malformed is
    # reported to the owner instead of being guessed at.
    async def async_press(self) -> None:
        entity_id = self._options[CONF_SCHEDULE_ENTITY]
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="schedule_helper_unavailable")
        inside = self._options.get(CONF_SCHEDULE_INSIDE)
        outside = self._options.get(CONF_SCHEDULE_OUTSIDE)
        try:
            plan = plan_week(
                await read_blocks(self.hass, entity_id),
                float(self._runtime.central.policy.max_current_a if inside is None else inside),
                float(0 if outside is None else outside),
            )
        except SchedulePlanError as error:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="control_invalid_schedule",
                translation_placeholders={"detail": str(error)},
            ) from error
        anchor = anchor_for(plan)
        periods, kind = plan.periods, plan.kind
        await run_write(self._runtime, self._cp_id, lambda control: control.set_schedule(anchor, periods, kind))
