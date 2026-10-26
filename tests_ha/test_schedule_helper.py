"""The apply-schedule button: a Home Assistant schedule helper turned into a charging profile, only when pressed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ocpp_hub.const import CONF_SCHEDULE_ENTITY, CONF_SCHEDULE_OUTSIDE
from tests_ha.conftest import state_of, wait_for

APPLY = "button.ocpp_hub_test0001_apply_schedule"
EVERY_DAY = {
    day: [{"from": "07:00", "to": "09:00"}]
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
}


# Set up a schedule helper called "night" with the given blocks per weekday.
async def _helper(hass: HomeAssistant, days: dict[str, list[dict[str, str]]]) -> None:
    assert await async_setup_component(hass, "schedule", {"schedule": {"night": {"name": "Night", **days}}})
    await hass.async_block_till_done()


# A booted charger of an entry whose options name the helper.
async def _booted(
    hass: HomeAssistant,
    setup_hub: Callable[..., Awaitable[MockConfigEntry]],
    connect_charger: Any,
    **options: Any,
) -> Any:
    entry = await setup_hub({CONF_SCHEDULE_ENTITY: "schedule.night", **options})
    charger = await connect_charger(entry)
    await charger.boot()
    await wait_for(hass, lambda: state_of(hass, "sensor.ocpp_hub_test0001_status") is not None, "no entities")
    return charger


# Press the button while the fake charger answers the one write it causes, and return that write.
async def _press(hass: HomeAssistant, charger: Any) -> list[Any]:
    call = asyncio.create_task(hass.services.async_call("button", "press", {"entity_id": APPLY}, blocking=True))
    request = await charger.next_hub_call()
    await charger.answer(request, {"status": "Accepted"})
    await call
    return request


# Intent: pressing the button turns the same blocks on every day into a daily profile: paused outside the blocks, the
# ceiling inside, and the helper changing alone sends nothing.
# Why: this is the whole point of the helper; the profile must be the smallest one that says it, and nothing may be
# written until the owner presses the button.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_same_blocks_every_day_are_sent_as_a_daily_profile(
    hass: HomeAssistant, setup_hub: Callable[..., Awaitable[MockConfigEntry]], connect_charger: Any
) -> None:
    await _helper(hass, EVERY_DAY)
    charger = await _booted(hass, setup_hub, connect_charger)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(charger.next_hub_call(), 0.2)

    request = await _press(hass, charger)

    profile = request[3]["csChargingProfiles"]
    assert profile["recurrencyKind"] == "Daily"
    periods = profile["chargingSchedule"]["chargingSchedulePeriod"]
    assert periods == [
        {"startPeriod": 0, "limit": 0.0},
        {"startPeriod": 7 * 3600, "limit": 16.0},
        {"startPeriod": 9 * 3600, "limit": 0.0},
    ]


# Intent: blocks on one weekday only make a weekly profile that starts on a Monday, with the limit outside the blocks
# taken from the options.
# Why: a charger counts a weekly profile from its start, so the anchor must be a Monday for the offsets to mean the
# weekdays the helper names.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_blocks_on_one_day_are_sent_as_a_weekly_profile_from_a_monday(
    hass: HomeAssistant, setup_hub: Callable[..., Awaitable[MockConfigEntry]], connect_charger: Any
) -> None:
    await _helper(hass, {"wednesday": [{"from": "00:00", "to": "06:00"}]})
    charger = await _booted(hass, setup_hub, connect_charger, **{CONF_SCHEDULE_OUTSIDE: 6.0})

    request = await _press(hass, charger)

    profile = request[3]["csChargingProfiles"]
    schedule = profile["chargingSchedule"]
    assert profile["recurrencyKind"] == "Weekly" and schedule["duration"] == 604800
    assert [(p["startPeriod"], p["limit"]) for p in schedule["chargingSchedulePeriod"]] == [
        (0, 6.0),
        (2 * 86400, 16.0),
        (2 * 86400 + 6 * 3600, 6.0),
    ]
    anchor = dt_util.as_local(dt_util.parse_datetime(schedule["startSchedule"]))
    assert anchor.weekday() == 0 and (anchor.hour, anchor.minute) == (0, 0)


# Intent: a helper that is missing or unavailable is reported as an error and nothing is written.
# Why: guessing a schedule from a helper that is not there would put an unknown plan on the charger.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_a_missing_helper_is_reported_and_nothing_is_written(
    hass: HomeAssistant, setup_hub: Callable[..., Awaitable[MockConfigEntry]], connect_charger: Any
) -> None:
    charger = await _booted(hass, setup_hub, connect_charger)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": APPLY}, blocking=True)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(charger.next_hub_call(), 0.2)
