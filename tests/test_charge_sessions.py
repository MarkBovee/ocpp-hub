"""Finished charges as the engine reports them, driven by a fake charger over a real websocket."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ocpp_hub_core import readings
from ocpp_hub_core.model import ChargeSession


# A MeterValues payload with the energy register in Wh and the power in W.
def _meter(timestamp: str, energy_wh: float, power_w: float) -> dict[str, Any]:
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


# Intent: a charge from StartTransaction to StopTransaction is reported once, with energy, peak power, tag, times and
# the stop reason, and the connector is clear again afterwards.
# Why: this record is the whole point of the session history; a wrong energy or a missing report would be unnoticed.
async def test_a_finished_charge_is_reported_with_its_numbers(start_hub) -> None:
    hub = await start_hub()
    charger = await hub.charger()

    started = await charger.call(
        "StartTransaction",
        {"connectorId": 1, "idTag": "TAG1", "meterStart": 1000, "timestamp": "2026-10-21T08:00:00Z"},
    )
    await charger.call("MeterValues", _meter("2026-10-21T08:10:00Z", 2000, 7000))
    await charger.call("MeterValues", _meter("2026-10-21T08:20:00Z", 3500, 4000))
    running = hub.listener.latest
    await charger.call(
        "StopTransaction",
        {
            "transactionId": started[2]["transactionId"],
            "meterStop": 4000,
            "timestamp": "2026-10-21T09:00:00Z",
            "reason": "EVDisconnected",
        },
    )

    assert readings.session_energy_kwh(running) == 2.5 and readings.session_duration_s(running) == 1200
    assert readings.session_energy_kwh(hub.listener.latest) is None
    assert hub.listener.latest.connectors[1].transaction_id is None
    assert hub.listener.finished == [
        ChargeSession(
            cp_id="TEST0001",
            connector_id=1,
            transaction_id=started[2]["transactionId"],
            id_tag="TAG1",
            started_at=datetime(2026, 10, 21, 8, 0, tzinfo=UTC),
            ended_at=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
            meter_start_wh=1000.0,
            meter_stop_wh=4000.0,
            energy_wh=3000.0,
            max_power_w=7000.0,
            stop_reason="EVDisconnected",
        )
    ]


# Intent: a stop for a transaction the hub never saw start produces no charge, and a stop without a stop reading falls
# back to the last meter value.
# Why: after a restart of the hub a charger can end a charge that began earlier; inventing a record for it would
# show a wrong start and energy. The fallback keeps the energy right for chargers that omit meterStop.
async def test_unknown_stops_are_ignored_and_a_missing_stop_reading_uses_the_last_meter_value(start_hub) -> None:
    hub = await start_hub()
    charger = await hub.charger()

    await charger.call("StopTransaction", {"transactionId": 4242, "meterStop": 10, "timestamp": "2026-10-21T08:00:00Z"})
    started = await charger.call(
        "StartTransaction", {"connectorId": 1, "idTag": "TAG1", "meterStart": 500, "timestamp": "2026-10-21T08:00:00Z"}
    )
    await charger.call("MeterValues", _meter("2026-10-21T08:30:00Z", 1700, 3000))
    await charger.call("StopTransaction", {"transactionId": started[2]["transactionId"], "timestamp": "garbled"})

    assert len(hub.listener.finished) == 1
    assert hub.listener.finished[0].energy_wh == 1200.0 and hub.listener.finished[0].meter_stop_wh is None


# Intent: a charge survives a trip through to_dict and from_dict unchanged, and a damaged record raises.
# Why: sessions are stored as JSON and read back after every restart; a lossy round trip would corrupt the history.
def test_a_session_round_trips_through_its_dictionary() -> None:
    session = ChargeSession(
        cp_id="TEST0001",
        connector_id=1,
        transaction_id=7,
        id_tag=None,
        started_at=datetime(2026, 10, 21, 8, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 21, 9, 30, tzinfo=UTC),
        energy_wh=11000.0,
    )

    assert ChargeSession.from_dict(session.to_dict()) == session
    assert session.duration_s == 5400.0
    try:
        ChargeSession.from_dict({"cp_id": "x"})
    except KeyError:
        pass
    else:
        raise AssertionError("a damaged record must raise")
