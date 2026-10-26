"""Read the blocks of a Home Assistant schedule helper and plan them as the periods of a charging profile."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .core.schedule_plan import KIND_WEEKLY, Block, SchedulePlan, SchedulePlanError

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


# A time of day such as "07:30:00" (or "24:00:00" for the end of the day) as seconds after midnight.
def _seconds(text: object) -> int:
    parts = str(text).split(":")
    if not 2 <= len(parts) <= 3 or not all(part.isascii() and part.isdigit() and len(part) <= 2 for part in parts):
        raise SchedulePlanError(f"{text!r} is not a time of day")
    hours, minutes = int(parts[0]), int(parts[1])
    seconds = int(parts[2]) if len(parts) == 3 else 0
    return hours * 3600 + minutes * 60 + seconds


# The blocks of a schedule helper per weekday (0 is Monday), from the answer of `schedule.get_schedule`: a mapping of
# weekday names to lists of blocks with a start and an end, and optionally a `data` with a `limit` of their own. The
# helper does not show its blocks in its state, only that it is on or off, so they have to be asked for. Anything that
# is not a list of blocks is refused, because the answer comes from a helper that can be edited by hand.
def blocks_from_schedule(schedule: Mapping[str, Any]) -> dict[int, list[Block]]:
    blocks: dict[int, list[Block]] = {}
    for day, name in enumerate(WEEKDAYS):
        raw = schedule.get(name, [])
        if not isinstance(raw, list):
            raise SchedulePlanError(f"{name} is not a list of blocks")
        day_blocks: list[Block] = []
        for entry in raw:
            if not isinstance(entry, dict) or "from" not in entry or "to" not in entry:
                raise SchedulePlanError(f"a block of {name} has no start or end")
            data: Any = entry.get("data")
            limit = data.get("limit") if isinstance(data, dict) else None
            if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float))):
                raise SchedulePlanError(f"a block of {name} has a limit that is not a number")
            try:
                own = None if limit is None else float(limit)
            except OverflowError as error:
                raise SchedulePlanError(f"a block of {name} has a limit that is too large") from error
            day_blocks.append(Block(_seconds(entry["from"]), _seconds(entry["to"]), own))
        blocks[day] = day_blocks
    return blocks


# Ask Home Assistant for the blocks of a schedule helper. A helper that does not exist, or that answers with nothing,
# is an error for the caller to report.
async def read_blocks(hass: HomeAssistant, entity_id: str) -> dict[int, list[Block]]:
    try:
        answer = await hass.services.async_call(
            "schedule", "get_schedule", {"entity_id": entity_id}, blocking=True, return_response=True
        )
    except HomeAssistantError as error:
        raise SchedulePlanError(f"the schedule {entity_id} could not be read: {error}") from error
    schedule = answer.get(entity_id) if isinstance(answer, dict) else None
    if not isinstance(schedule, dict):
        raise SchedulePlanError(f"the schedule {entity_id} returned no blocks")
    return blocks_from_schedule(schedule)


# Where the schedule starts, as a moment: the start of today for a daily plan, and the start of this week's Monday for a
# weekly one, in local time. A recurring profile repeats from here.
def anchor_for(plan: SchedulePlan) -> datetime:
    today = dt_util.start_of_local_day()
    if plan.kind != KIND_WEEKLY:
        return today
    return dt_util.start_of_local_day(today.date() - timedelta(days=today.weekday()))
