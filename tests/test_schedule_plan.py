"""The planner that turns blocks per weekday into the periods of one recurring profile."""

from __future__ import annotations

import pytest
from ocpp_hub_core.schedule_plan import KIND_DAILY, KIND_WEEKLY, Block, SchedulePlanError, plan_week

H = 3600


# Intent: the same blocks every day give a daily profile: the outside limit from midnight, the inside limit from the
# start of a block, the outside limit again at its end, and nothing at a block that ends at midnight.
# Why: a daily profile is the smallest thing a charger has to understand, so it is used whenever it says the same.
def test_the_same_blocks_every_day_make_a_daily_plan() -> None:
    blocks = {day: [Block(7 * H, 9 * H), Block(22 * H, 24 * H)] for day in range(7)}

    plan = plan_week(blocks, inside_a=16.0, outside_a=0.0)

    assert plan.kind == KIND_DAILY and plan.span_s == 86400
    assert plan.periods == ((0, 0.0), (7 * H, 16.0), (9 * H, 0.0), (22 * H, 16.0))


# Intent: days that differ make a weekly profile with offsets from Monday, and a limit that continues over midnight is
# not repeated; a block's own limit wins over the inside limit.
# Why: the offsets of a week are what the charger counts from its start, and a repeated limit only uses up periods the
# charger may have few of.
def test_different_days_make_a_weekly_plan_with_merged_neighbours() -> None:
    blocks = {
        0: [Block(0, 24 * H)],
        1: [Block(0, 6 * H, limit_a=10.0)],
    }

    plan = plan_week(blocks, inside_a=16.0, outside_a=0.0)

    assert plan.kind == KIND_WEEKLY and plan.span_s == 7 * 86400
    assert plan.periods == ((0, 16.0), (86400, 10.0), (86400 + 6 * H, 0.0))


# Intent: a day with no blocks everywhere is one period of the outside limit, and blocks that overlap, run backwards or
# leave the day are refused.
# Why: a schedule that cannot be understood must stop here, not at the charger.
@pytest.mark.parametrize(
    "blocks",
    [
        {0: [Block(7 * H, 9 * H), Block(8 * H, 10 * H)]},
        {0: [Block(9 * H, 7 * H)]},
        {0: [Block(0, 25 * H)]},
        {0: [Block(-1, H)]},
    ],
)
def test_blocks_that_make_no_sense_are_refused(blocks: dict[int, list[Block]]) -> None:
    assert plan_week({}, inside_a=16.0, outside_a=0.0).periods == ((0, 0.0),)
    with pytest.raises(SchedulePlanError):
        plan_week(blocks, inside_a=16.0, outside_a=0.0)
