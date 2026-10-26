"""The plan of the hub: windows per weekday that the app shows, Home Assistant sets, and the charger obeys.

The plan is the master. When the app writes a planning, or Home Assistant sets one, it is turned into a recurring OCPP
profile with a limit inside the windows and a limit outside them, under the owner's ceiling, and written to the charger;
the plan is kept only when that worked. The app is answered from the plan when it asks for the planning.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_SCHEDULE_INSIDE, CONF_SCHEDULE_OUTSIDE, plan_signal
from .core.central import CentralSystem
from .core.control import ChargerControl, ControlError
from .core.model import ChargerSnapshot
from .core.plan_bridge import windows_to_blocks
from .core.raedian import PlanWindow
from .core.schedule_plan import SchedulePlanError, plan_week
from .schedule_helper import anchor_for
from .session_store import PlanStore

_LOGGER = logging.getLogger(__name__)


class PlanSync:
    """Holds the plan of each charger and writes it to the charger. Implements the mirror's PlanHandler."""

    # Keep the store, the options that say the limits inside and outside the windows, and what is needed to signal.
    def __init__(self, hass: HomeAssistant, entry_id: str, store: PlanStore, options: Mapping[str, object]) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store = store
        self._options = options
        self._central: CentralSystem | None = None
        # How many profile writes the plan has seen per charger, and the chargers the plan is writing to right now.
        self._seen: dict[str, int] = {}
        self._writing: set[str] = set()

    # Give the plan the central system it writes through; it is made after the mirror that needs the plan.
    def bind(self, central: CentralSystem) -> None:
        self._central = central

    # The plan of a charger, or None when the hub has none yet.
    def plan(self, cp_id: str) -> list[PlanWindow] | None:
        return self._store.get(cp_id)

    # Take over the plan the charger reported on the first question of the app, without writing anything to the charger:
    # it is what the charger already has.
    def imported(self, cp_id: str, windows: list[PlanWindow]) -> None:
        self._keep(cp_id, windows)

    # The app wrote a planning: write it to the charger as a profile, and keep it as the plan when that worked.
    async def app_wrote(self, cp_id: str, windows: list[PlanWindow]) -> bool:
        try:
            await self.set_plan(cp_id, windows)
        except (ControlError, SchedulePlanError) as error:
            _LOGGER.warning("[plan] The planning of the app for %s could not be applied: %s", cp_id, error)
            return False
        return True

    # Set the plan: turn the windows into one recurring profile and write it, then keep the plan. An empty plan clears
    # the hub's profile instead, because a profile of nothing but the outside limit would hold the charger at that limit
    # all week. Raises ControlError or SchedulePlanError when it cannot be written, and keeps the old plan then.
    async def set_plan(self, cp_id: str, windows: list[PlanWindow]) -> None:
        control = self._central.control(cp_id) if self._central is not None else None
        if control is None:
            raise ControlError("not_connected", "the charger is not connected")
        self._writing.add(cp_id)
        try:
            await self._write(control, windows)
        finally:
            self._writing.discard(cp_id)
            session = self._central.session(cp_id) if self._central is not None else None
            if session is not None:
                self._seen[cp_id] = session.snapshot.profile_writes
        self._keep(cp_id, windows)

    # Write the windows to the charger as one profile, or clear the hub's profile when there are none.
    async def _write(self, control: ChargerControl, windows: list[PlanWindow]) -> None:
        if windows:
            inside = self._options.get(CONF_SCHEDULE_INSIDE)
            outside = self._options.get(CONF_SCHEDULE_OUTSIDE)
            plan = plan_week(
                windows_to_blocks(windows),
                float(control.policy.max_current_a if inside is None else inside),  # type: ignore[arg-type]
                float(0 if outside is None else outside),  # type: ignore[arg-type]
            )
            await control.set_schedule(anchor_for(plan), plan.periods, plan.kind)
        else:
            await control.clear_current_limit()

    # Save the plans now; used when the entry unloads.
    async def async_flush(self) -> None:
        await self._store.async_flush()

    # Called with every snapshot. The plan rides on the hub's own profile, which a single limit, a schedule or a clear
    # also use; when such a write happens that the plan did not make, the profile is no longer the plan, so the plan is
    # emptied and the app is told so, instead of both going on to claim what the charger no longer follows.
    def snapshot_changed(self, snapshot: ChargerSnapshot) -> None:
        cp_id = snapshot.cp_id
        if cp_id in self._writing or not snapshot.connected:
            return
        seen = self._seen.get(cp_id, snapshot.profile_writes)
        self._seen[cp_id] = snapshot.profile_writes
        if snapshot.profile_writes > seen and self._store.get(cp_id):
            _LOGGER.info("[plan] The profile of %s was replaced by another write; the plan is now empty", cp_id)
            self._keep(cp_id, [])

    # Keep a plan and tell the entities that show it.
    def _keep(self, cp_id: str, windows: list[PlanWindow]) -> None:
        self._store.set(cp_id, windows)
        async_dispatcher_send(self._hass, plan_signal(self._entry_id, cp_id))
