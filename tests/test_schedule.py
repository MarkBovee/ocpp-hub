"""Daily schedules of charge limits: the rules, what is sent to the charger, and reading the charger's own schedule."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from ocpp_hub_core.control import ControlError, ControlPolicy, checked_schedule

from tests.fake_vendor import eventually

POLICY = ControlPolicy(max_current_a=16.0)
ANCHOR = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)


# A booted fake charger behind a hub, with the control of its session.
async def _booted(start_hub: Any) -> tuple[Any, Any, Any]:
    hub = await start_hub()
    charger = await hub.charger()
    await charger.boot()
    return hub, charger, hub.central.control("TEST0001")


# Intent: a schedule must start at 00:00, have increasing starts within the day, at most 24 periods, and every limit
# obeys the single-limit rules: the ceiling holds and a value under 6 A becomes a pause.
# Why: the schedule is a write to the charger with many limits in it; one period above the ceiling would put the whole
# thing past the owner's safety limit, and nothing is repaired silently.
@pytest.mark.parametrize(
    ("periods", "problem"),
    [
        ([(0, 6), (25200, 16)], None),
        ([(0, 4), (3600, 16)], None),
        ([], "invalid_schedule"),
        ([(3600, 6)], "invalid_schedule"),
        ([(0, 6), (0, 10)], "invalid_schedule"),
        ([(0, 6), (7200, 10), (3600, 12)], "invalid_schedule"),
        ([(0, 6), (86400, 10)], "invalid_schedule"),
        ([(0, 6), (True, 10)], "invalid_schedule"),
        ([(0, 6), (3600, 17)], "above_ceiling"),
        ([(0, float("nan"))], "invalid_value"),
        ([(n * 3600, 6) for n in range(25)], "invalid_schedule"),
    ],
)
def test_the_rules_of_a_schedule(periods: list[tuple[Any, Any]], problem: str | None) -> None:
    if problem is None:
        checked = checked_schedule(periods, POLICY)
        assert [start for start, _ in checked] == [start for start, _ in periods]
        return
    with pytest.raises(ControlError) as refused:
        checked_schedule(periods, POLICY)
    assert refused.value.code == problem


# Intent: a schedule is sent as a recurring daily profile on the station, with the hub's own profile id, the anchor in
# UTC, the periods as sent, and a pause as 0 A; the charger's acceptance is recorded as the purpose.
# Why: this is the exact shape a charger needs to run a schedule; the id is shared with the single limit so each
# replaces the other and the hub never leaves two of its own profiles behind.
async def test_a_schedule_is_sent_as_a_recurring_daily_profile(start_hub) -> None:
    hub, charger, control = await _booted(start_hub)

    task = asyncio.create_task(control.set_daily_schedule(ANCHOR, [(0, 4), (25200, 16)]))
    call = await charger.next_hub_call()
    await charger.answer(call, {"status": "Accepted"})
    sent = await task

    profile = call[3]["csChargingProfiles"]
    assert call[2] == "SetChargingProfile" and call[3]["connectorId"] == 0 and sent == [(0, 0.0), (25200, 16.0)]
    assert profile["chargingProfileId"] == 7001 and profile["chargingProfilePurpose"] == "ChargePointMaxProfile"
    assert profile["chargingProfileKind"] == "Recurring" and profile["recurrencyKind"] == "Daily"
    schedule = profile["chargingSchedule"]
    assert schedule["startSchedule"] == "2026-10-21T22:00:00.000Z" and schedule["chargingRateUnit"] == "A"
    assert schedule["chargingSchedulePeriod"] == [
        {"startPeriod": 0, "limit": 0.0},
        {"startPeriod": 25200, "limit": 16.0},
    ]
    assert hub.listener.latest.limit_purpose == "ChargePointMaxProfile" and hub.listener.latest.charge_limit_a is None


# Intent: when the station profile is refused the default profile is tried, a charge profile is never used for a
# schedule, and when both are refused the write fails with the answers listed.
# Why: a charge profile cannot recur, so offering it would install something other than what was asked.
async def test_a_schedule_falls_back_to_the_default_profile_and_never_to_a_charge_profile(start_hub) -> None:
    _, charger, control = await _booted(start_hub)

    task = asyncio.create_task(control.set_daily_schedule(ANCHOR, [(0, 6)]))
    first = await charger.next_hub_call()
    await charger.answer(first, {"status": "Rejected"})
    second = await charger.next_hub_call()
    await charger.answer(second, {"status": "Rejected"})
    with pytest.raises(ControlError) as refused:
        await task

    purposes = [c[3]["csChargingProfiles"]["chargingProfilePurpose"] for c in (first, second)]
    assert purposes == ["ChargePointMaxProfile", "TxDefaultProfile"] and refused.value.code == "rejected"
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(charger.next_hub_call(), 0.2)


# Intent: the hub reads the charger's own schedule for the next day and keeps it with its start; a charger that cannot
# answer the long question leaves the schedule empty without costing the answer about the limit in effect.
# Why: the schedule shown must be what the charger reports, not what the hub believes it sent; and the two questions are
# separate so that a charger that refuses the long one still tells its present limit.
async def test_the_schedule_the_charger_reports_is_read_and_kept(start_hub) -> None:
    hub, charger, control = await _booted(start_hub)
    charger.composite_limit = 6.0
    charger.composite_periods = [(0, 6.0), (25200, 16.0)]

    task = asyncio.create_task(control.set_current_limit(8))
    call = await charger.next_hub_call()
    await charger.answer(call, {"status": "Accepted"})
    await task
    await eventually(lambda: hub.listener.latest.schedule_periods, message="schedule not read")

    shown = hub.listener.latest
    assert shown.schedule_periods == ((0, 6.0), (25200, 16.0))
    assert shown.schedule_start == datetime(2026, 10, 22, 0, 0, tzinfo=UTC) and shown.composite_limit_a == 6.0

    charger.refuse_long_composite = True
    task = asyncio.create_task(control.set_current_limit(8))
    call = await charger.next_hub_call()
    await charger.answer(call, {"status": "Accepted"})
    await task
    await eventually(lambda: not hub.listener.latest.schedule_periods, message="schedule not cleared")

    assert hub.listener.latest.composite_limit_a == 6.0
