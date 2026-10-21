"""Charge sessions in Home Assistant: live sensors, the last-session sensor, the stored history and the service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ocpp_hub.const import DOMAIN
from custom_components.ocpp_hub.core.model import ChargeSession
from tests_ha.conftest import state_of, wait_for

SESSION_ENERGY = "sensor.ocpp_hub_test0001_session_energy"
SESSION_STARTED = "sensor.ocpp_hub_test0001_session_started"
LAST_SESSION = "sensor.ocpp_hub_test0001_last_session"


# A MeterValues payload with the energy register in Wh and the power in W.
def _meter(timestamp: str, energy_wh: int, power_w: int) -> dict[str, Any]:
    return {
        "connectorId": 1,
        "meterValue": [
            {
                "timestamp": timestamp,
                "sampledValue": [
                    {"measurand": "Energy.Active.Import.Register", "value": str(energy_wh), "unit": "Wh"},
                    {"measurand": "Power.Active.Import", "value": str(power_w), "unit": "W"},
                ],
            }
        ],
    }


# One charge on a booted fake charger, from start to stop.
async def _charge(charger: Any) -> None:
    started = await charger.call(
        "StartTransaction",
        {"connectorId": 1, "idTag": "TAG1", "meterStart": 1000, "timestamp": "2026-10-21T08:00:00Z"},
    )
    await charger.call("MeterValues", _meter("2026-10-21T08:30:00Z", 3000, 6000))
    await charger.call(
        "StopTransaction",
        {
            "transactionId": started[2]["transactionId"],
            "meterStop": 5000,
            "timestamp": "2026-10-21T09:00:00Z",
            "reason": "Local",
        },
    )


# Intent: while a charge runs the session sensors show its energy and start, afterwards they clear and the last-session
# sensor shows the finished charge with its numbers and the list of recent charges.
# Why: this is what a dashboard shows; the live card and the history table must agree with the charger.
async def test_a_charge_shows_live_and_then_in_the_history(
    hass: HomeAssistant, setup_hub: Callable[..., Awaitable[MockConfigEntry]], connect_charger: Any
) -> None:
    entry = await setup_hub()
    charger = await connect_charger(entry)
    await charger.boot()
    await wait_for(hass, lambda: state_of(hass, LAST_SESSION) is not None, "no entities")

    started = await charger.call(
        "StartTransaction",
        {"connectorId": 1, "idTag": "TAG1", "meterStart": 1000, "timestamp": "2026-10-21T08:00:00Z"},
    )
    await charger.call("MeterValues", _meter("2026-10-21T08:30:00Z", 3000, 6000))
    await wait_for(hass, lambda: state_of(hass, SESSION_ENERGY) == "2.0", "live energy not shown")
    assert state_of(hass, SESSION_STARTED) == "2026-10-21T08:00:00+00:00"

    await charger.call(
        "StopTransaction",
        {"transactionId": started[2]["transactionId"], "meterStop": 5000, "timestamp": "2026-10-21T09:00:00Z"},
    )
    await wait_for(hass, lambda: state_of(hass, SESSION_ENERGY) == "unknown", "live energy not cleared")
    await wait_for(hass, lambda: state_of(hass, LAST_SESSION) == "2026-10-21T09:00:00+00:00", "no last session")

    attributes = hass.states.get(LAST_SESSION).attributes
    assert attributes["energy_kwh"] == 4.0 and attributes["duration_s"] == 3600 and attributes["session_count"] == 1
    assert attributes["max_power_w"] == 6000.0 and attributes["id_tag"] == "TAG1"
    assert len(attributes["recent_sessions"]) == 1 and attributes["recent_sessions"][0]["energy_kwh"] == 4.0


# Intent: the history is unbounded, survives a reload of the entry, and the service returns all of it newest first,
# optionally limited.
# Why: the owner asked for unlimited sessions; a hidden cap or a lost history after a restart would break that promise.
async def test_the_history_is_unbounded_persistent_and_served(
    hass: HomeAssistant, setup_hub: Callable[..., Awaitable[MockConfigEntry]], connect_charger: Any
) -> None:
    entry = await setup_hub()
    charger = await connect_charger(entry)
    await charger.boot()
    await wait_for(hass, lambda: state_of(hass, LAST_SESSION) is not None, "no entities")
    await _charge(charger)
    await wait_for(hass, lambda: entry.runtime_data.sessions.count() == 1, "charge not stored")
    base = datetime(2026, 9, 1, tzinfo=UTC)
    for day in range(300):
        started = base + timedelta(days=day)
        entry.runtime_data.sessions.add(
            ChargeSession("TEST0001", 1, 1000 + day, None, started, started + timedelta(hours=1), energy_wh=1000.0)
        )

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    everything = await hass.services.async_call(DOMAIN, "get_sessions", {}, blocking=True, return_response=True)
    newest = await hass.services.async_call(
        DOMAIN, "get_sessions", {"charger_id": "TEST0001", "limit": 2}, blocking=True, return_response=True
    )
    other = await hass.services.async_call(
        DOMAIN, "get_sessions", {"charger_id": "NOBODY"}, blocking=True, return_response=True
    )

    assert len(everything["sessions"]) == 301 and other["sessions"] == []
    assert [s["transaction_id"] for s in newest["sessions"]] == [1299, 1298]
