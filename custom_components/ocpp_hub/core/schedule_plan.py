"""Turn the blocks of a weekly schedule into the periods of one recurring charging profile. Pure rules, no I/O."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

SECONDS_PER_DAY = 86400
DAYS_PER_WEEK = 7
SECONDS_PER_WEEK = SECONDS_PER_DAY * DAYS_PER_WEEK
KIND_DAILY = "Daily"
KIND_WEEKLY = "Weekly"


class SchedulePlanError(ValueError):
    """The blocks cannot be turned into a schedule; the message says why."""


@dataclass(frozen=True, slots=True)
class Block:
    """One stretch of a day in which the schedule is on: seconds after midnight, and its own limit if it has one."""

    start_s: int
    end_s: int
    limit_a: float | None = None


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    """One recurring profile: daily when every day is the same, weekly otherwise, with (offset, limit) periods."""

    kind: str
    periods: tuple[tuple[int, float], ...]

    @property
    def span_s(self) -> int:
        """How long the profile runs before it repeats."""
        return SECONDS_PER_DAY if self.kind == KIND_DAILY else SECONDS_PER_WEEK


# The periods of one day: the outside limit from midnight, the inside limit (or the block's own) from the start of each
# block, and the outside limit again at its end. Blocks must be inside the day, in order and not overlapping.
def _day_periods(blocks: Sequence[Block], inside_a: float, outside_a: float) -> list[tuple[int, float]]:
    periods: list[tuple[int, float]] = [(0, outside_a)]
    last_end = 0
    for block in sorted(blocks, key=lambda b: b.start_s):
        if not 0 <= block.start_s < block.end_s <= SECONDS_PER_DAY:
            raise SchedulePlanError(f"a block from {block.start_s} to {block.end_s} seconds is not inside the day")
        if block.start_s < last_end:
            raise SchedulePlanError("two blocks of a day overlap")
        periods.append((block.start_s, inside_a if block.limit_a is None else block.limit_a))
        if block.end_s < SECONDS_PER_DAY:
            periods.append((block.end_s, outside_a))
        last_end = block.end_s
    return _merged(periods)


# Join neighbours with the same limit and drop a period that starts where the next one does, keeping the later one.
def _merged(periods: Sequence[tuple[int, float]]) -> list[tuple[int, float]]:
    merged: list[tuple[int, float]] = []
    for start, limit in periods:
        if merged and merged[-1][0] == start:
            merged.pop()
        if merged and merged[-1][1] == limit:
            continue
        merged.append((start, limit))
    return merged


# Plan the schedule for a week: blocks per weekday (0 is Monday), the limit inside a block unless it has its own, and
# the limit outside blocks. When every day comes out the same, the plan is a daily profile; otherwise a weekly one.
def plan_week(blocks: Mapping[int, Sequence[Block]], inside_a: float, outside_a: float) -> SchedulePlan:
    days = [_day_periods(blocks.get(day, ()), inside_a, outside_a) for day in range(DAYS_PER_WEEK)]
    if all(day == days[0] for day in days):
        return SchedulePlan(KIND_DAILY, tuple(days[0]))
    week: list[tuple[int, float]] = []
    for day, periods in enumerate(days):
        week.extend((day * SECONDS_PER_DAY + start, limit) for start, limit in periods)
    return SchedulePlan(KIND_WEEKLY, tuple(_merged(week)))
