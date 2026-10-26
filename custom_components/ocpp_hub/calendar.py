"""A calendar of the charging plan: the windows in which the charger may charge, as repeating events."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import plan_signal
from .entity import OcppHubEntity, async_add_charger_entities
from .plan_sync import shown_windows
from .runtime import HubRuntime, OcppHubConfigEntry

# How far ahead the "next event" looks, in days; a weekly plan always has its next window within a week.
LOOK_AHEAD_DAYS = 8


# Create the plan calendar of every charger that boots.
async def async_setup_entry(
    hass: HomeAssistant, entry: OcppHubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    # The calendar of one charger.
    def build(cp_id: str) -> list[OcppHubEntity]:
        return [PlanCalendar(runtime, entry.entry_id, cp_id)]

    async_add_charger_entities(hass, entry, async_add_entities, build)


class PlanCalendar(OcppHubEntity, CalendarEntity):
    """The charging plan as a calendar. Read-only: the plan is changed in the app or with `ocpp_hub.set_plan`."""

    _attr_translation_key = "charging_plan"

    # Build the calendar for one charger.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str) -> None:
        super().__init__(runtime, entry_id, cp_id, "calendar", "charging_plan")

    # Also follow the plan: a new plan changes the calendar, not only a new snapshot.
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, plan_signal(self._entry_id, self._cp_id), self._plan_changed)
        )

    # Show the new plan.
    @callback
    def _plan_changed(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """The plan stays readable while the charger is away: it is the hub's."""
        return True

    @property
    def event(self) -> CalendarEvent | None:
        """The window that is open now, or the next one."""
        now = dt_util.now()
        events = self._events_between(now - timedelta(days=1), now + timedelta(days=LOOK_AHEAD_DAYS))
        upcoming = sorted((e for e in events if e.end > now), key=lambda e: e.start)
        return upcoming[0] if upcoming else None

    # The windows that overlap the given period, for the calendar view.
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return self._events_between(start_date, end_date)

    # Turn the weekly windows into the events that overlap the period. A window that ends before it starts runs into
    # the next day, so the day before the period is looked at as well.
    def _events_between(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        windows = shown_windows(self._runtime.plans, self._snapshot) or []
        events: list[CalendarEvent] = []
        day: date = dt_util.as_local(start).date() - timedelta(days=1)
        last: date = dt_util.as_local(end).date()
        while day <= last:
            for weekday, start_s, end_s in windows:
                if day.isoweekday() != weekday:
                    continue
                opens = _at(day, start_s)
                closes = _at(day + timedelta(days=1 if end_s <= start_s else 0), end_s)
                if opens < end and closes > start:
                    events.append(CalendarEvent(opens, closes, "Charging allowed"))
            day += timedelta(days=1)
        return sorted(events, key=lambda e: e.start)


# The local time that is the given seconds after midnight on a day.
def _at(day: date, seconds: int) -> datetime:
    midnight = dt_util.start_of_local_day(day)
    return dt_util.as_local(midnight + timedelta(seconds=seconds))
