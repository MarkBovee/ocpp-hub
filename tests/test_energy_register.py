"""The absolute energy register: periodic readings that count from the start of a charge mixed with clock readings
that carry the real register, as the Raedian NEO sends them, must give one register that never goes back."""

from __future__ import annotations

from typing import Any

from ocpp_hub_core import readings
from ocpp_hub_core.energy_register import (
    KIND_ABSOLUTE,
    KIND_RELATIVE,
    as_absolute,
    charge_start_register,
    next_register,
    rebased_start,
)
from ocpp_hub_core.model import ChargeMemory

# The register of the charger before the charge, as in the NEO capture of 9 November 2026 (106.803 kWh).
REGISTER_WH = 106803.0


# A periodic MeterValues payload of the running charge: energy (counting from the start, as the NEO sends it) and power.
def _periodic(transaction_id: int, timestamp: str, energy_wh: float) -> dict[str, Any]:
    return {
        "connectorId": 1,
        "transactionId": transaction_id,
        "meterValue": [
            {
                "timestamp": timestamp,
                "sampledValue": [
                    {"measurand": "Energy.Active.Import.Register", "value": str(energy_wh), "unit": "Wh"},
                    {"measurand": "Power.Active.Import", "value": "10300", "unit": "W"},
                ],
            }
        ],
    }


# A clock-aligned MeterValues payload at the quarter: only the absolute register, in kWh to prove the unit conversion.
def _clock(timestamp: str, register_kwh: float) -> dict[str, Any]:
    return {
        "connectorId": 1,
        "meterValue": [
            {
                "timestamp": timestamp,
                "sampledValue": [
                    {
                        "measurand": "Energy.Active.Import.Register",
                        "context": "Sample.Clock",
                        "value": str(register_kwh),
                        "unit": "kWh",
                    }
                ],
            }
        ],
    }


# Intent: a reading below the register at the start of the running charge is read as energy since that start; any
# other reading is taken as it is.
# Why: this is the one rule that turns the NEO's session-relative samples into the register.
def test_a_reading_below_the_charge_start_counts_from_that_start() -> None:
    assert as_absolute(2217.0, REGISTER_WH) == (REGISTER_WH + 2217.0, KIND_RELATIVE)
    assert as_absolute(109012.0, REGISTER_WH) == (109012.0, KIND_ABSOLUTE)
    assert as_absolute(500.0, None) == (500.0, KIND_ABSOLUTE)
    assert as_absolute(500.0, 0.0) == (500.0, KIND_ABSOLUTE)


# Intent: a reading that would make the register go back is not taken; an equal or higher one is.
# Why: Home Assistant reads any drop of a total_increasing sensor as a meter reset and the next rise as new energy.
def test_the_register_never_goes_back() -> None:
    dropped = next_register(147445.0, 40642.0, None)
    taken = next_register(147445.0, 147445.0, None)

    assert not dropped.accepted and dropped.register_wh == 147445.0
    assert taken.accepted and taken.register_wh == 147445.0
    assert next_register(None, 40642.0, None).register_wh == 40642.0


# Intent: the register at the start of a charge is meterStart when it is a register, otherwise the known register.
# Why: the NEO sends meterStart 0; taking that as the start made the charge show the whole register as its energy.
def test_the_start_of_a_charge_is_the_register_at_that_moment() -> None:
    assert charge_start_register(0.0, REGISTER_WH) == REGISTER_WH
    assert charge_start_register(107000.0, REGISTER_WH) == 107000.0
    assert charge_start_register(None, REGISTER_WH) == REGISTER_WH
    assert charge_start_register(1000.0, None) == 1000.0


# Intent: the exact NEO pattern of 9-10 November 2026 (register before the charge, meterStart 0, periodic samples from
# zero, the register at every quarter, then the same alternation after the stop) gives a register that only rises, a
# session energy that counts from zero, and a finished charge with the energy of the charge.
# Why: this pattern made the energy sensor jump by 106.8 kWh four times an hour and the statistics grow by 480-700 kWh
# per hour.
async def test_the_neo_pattern_gives_one_rising_register(start_hub) -> None:
    hub = await start_hub()
    charger = await hub.charger()
    await charger.call("MeterValues", _clock("2026-11-09T23:15:00Z", 106.803))
    started = await charger.call(
        "StartTransaction", {"connectorId": 1, "idTag": "TAG1", "meterStart": 0, "timestamp": "2026-11-09T23:30:20Z"}
    )
    tx = started[2]["transactionId"]
    shown: list[float | None] = []
    session: list[float | None] = []
    sequence = [
        _periodic(tx, "2026-11-09T23:30:30Z", 0),
        _periodic(tx, "2026-11-09T23:30:45Z", 28),
        _periodic(tx, "2026-11-09T23:31:00Z", 52),
        _periodic(tx, "2026-11-09T23:44:45Z", 2177),
        _clock("2026-11-09T23:45:00Z", 109.012),
        _periodic(tx, "2026-11-09T23:45:00Z", 2217),
        _periodic(tx, "2026-11-09T23:59:45Z", 3843),
        _clock("2026-11-10T00:00:00Z", 110.655),
        _periodic(tx, "2026-11-10T00:00:00Z", 3870),
        _periodic(tx, "2026-11-10T00:59:59Z", 10546),
        _clock("2026-11-10T01:00:00Z", 117.372),
        _periodic(tx, "2026-11-10T01:00:00Z", 10589),
    ]
    for payload in sequence:
        await charger.call("MeterValues", payload)
        shown.append(readings.energy_kwh(hub.listener.latest))
        session.append(readings.session_energy_kwh(hub.listener.latest))
    await charger.call(
        "StopTransaction",
        {"transactionId": tx, "meterStop": 40642, "timestamp": "2026-11-10T13:06:16Z", "reason": "EVDisconnected"},
    )
    for payload in [_periodic(tx, "2026-11-10T13:10:00Z", 40642), _clock("2026-11-10T13:15:00Z", 147.445)] * 2:
        await charger.call("MeterValues", payload)
        shown.append(readings.energy_kwh(hub.listener.latest))

    assert all(later >= earlier for earlier, later in zip(shown, shown[1:], strict=False))
    assert shown[:4] == [106.803, 106.831, 106.855, 108.98]
    assert shown[4:12] == [109.012, 109.02, 110.646, 110.655, 110.673, 117.349, 117.372, 117.392]
    assert shown[-1] == 147.445
    assert session[:4] == [0.0, 0.028, 0.052, 2.177] and session[-1] == 10.589
    assert hub.listener.finished[0].energy_wh == 40642.0


# Intent: a charge that started before Home Assistant restarted is still reported when it stops after the restart,
# from the running charge and register that storage kept.
# Why: after the restarts of 10 November the StopTransaction found no start, so last_session kept the charge before.
async def test_a_charge_survives_a_restart_through_storage(start_hub) -> None:
    before = await start_hub()
    charger = await before.charger()
    await charger.call("MeterValues", _clock("2026-11-09T23:15:00Z", 106.803))
    started = await charger.call(
        "StartTransaction", {"connectorId": 1, "idTag": "TAG1", "meterStart": 0, "timestamp": "2026-11-09T23:30:20Z"}
    )
    tx = started[2]["transactionId"]
    await charger.call("MeterValues", _periodic(tx, "2026-11-10T06:00:00Z", 30000))
    snapshot = before.listener.latest
    running = {n: c for n, c in snapshot.connectors.items() if c.transaction_id is not None}
    stored = ChargeMemory(register_wh=snapshot.register_wh, charges=running).to_dict()
    await charger.close()
    await before.central.stop()

    after = await start_hub()
    after.central.restore_charges({"TEST0001": ChargeMemory.from_dict(stored)})
    again = await after.charger()
    hidden_until_read = readings.energy_kwh(after.listener.latest)
    await again.call("MeterValues", _periodic(tx, "2026-11-10T12:00:00Z", 1000))
    await again.call("MeterValues", _periodic(tx, "2026-11-10T13:00:00Z", 40000))
    await again.call(
        "StopTransaction",
        {"transactionId": tx, "meterStop": 40642, "timestamp": "2026-11-10T13:06:16Z", "reason": "EVDisconnected"},
    )

    assert hidden_until_read is None
    assert readings.energy_kwh(after.listener.latest) == 146.803
    assert len(after.listener.finished) == 1 and after.listener.finished[0].energy_wh == 40642.0


# Intent: a clock-aligned reading is always the register, and on a young charger a relative reading at or above the
# start register is still read as relative when only that fits the current register.
# Why: a charge can deliver more than the register held at its start; reading it as absolute froze the register.
def test_clock_readings_are_absolute_and_young_chargers_stay_relative() -> None:
    assert as_absolute(500.0, 800.0, 1000.0, clock=True) == (500.0, KIND_ABSOLUTE)
    assert as_absolute(6800.0, 800.0, 7500.0) == (7600.0, KIND_RELATIVE)
    assert as_absolute(7600.0, 800.0, 7500.0) == (7600.0, KIND_ABSOLUTE)


# Intent: without a known register, meterStart 0 (or one below the stored register) leaves the start unknown, and the
# start is found from the first clock reading minus the energy counted so far.
# Why: taking 0 as the start made the session show the whole register and froze the register for the rest of the charge.
def test_an_unknown_start_is_found_from_the_first_clock_reading() -> None:
    assert charge_start_register(0.0, None) is None
    assert charge_start_register(100000.0, None, floor_wh=106803.0) is None
    assert charge_start_register(107000.0, None, floor_wh=106803.0) == 107000.0
    assert rebased_start(109012.0, 2209.0) == 106803.0 and rebased_start(10.0, 50.0) == 0.0


# Intent: a charge that starts before any register is known shows no register until the first clock reading, then a
# register that only rises and a session energy counted from the real start, and the finished charge has its energy.
# Why: the first charge after a fresh install, or after Home Assistant was down, must not break the statistics either.
async def test_a_charge_without_a_known_register_waits_for_the_clock(start_hub) -> None:
    hub = await start_hub()
    charger = await hub.charger()
    started = await charger.call(
        "StartTransaction", {"connectorId": 1, "idTag": "TAG1", "meterStart": 0, "timestamp": "2026-11-09T23:30:20Z"}
    )
    tx = started[2]["transactionId"]
    shown: list[float | None] = []
    for payload in [
        _periodic(tx, "2026-11-09T23:30:30Z", 0),
        _periodic(tx, "2026-11-09T23:44:45Z", 2177),
        _clock("2026-11-09T23:45:00Z", 108.98),
        _periodic(tx, "2026-11-09T23:45:15Z", 2217),
    ]:
        await charger.call("MeterValues", payload)
        shown.append(readings.energy_kwh(hub.listener.latest))
    session = readings.session_energy_kwh(hub.listener.latest)
    await charger.call(
        "StopTransaction",
        {"transactionId": tx, "meterStop": 40642, "timestamp": "2026-11-10T13:06:16Z", "reason": "EVDisconnected"},
    )

    assert shown == [None, None, 108.98, 109.02] and session == 2.217
    assert hub.listener.finished[0].energy_wh == 40642.0


# Intent: a stop without meterStop after a restart, before the charger sent a reading the register could show, still
# gets the energy of the charge from the last reading counted from its start.
# Why: the register is hidden after a restart until a fresh reading; the charge must not lose its energy because of it.
async def test_a_stop_without_reading_after_a_restart_keeps_its_energy(start_hub) -> None:
    hub = await start_hub()
    stored = {
        "register_wh": 136000.0,
        "charges": [
            {
                "connector_id": 1,
                "transaction_id": 77,
                "id_tag": "TAG1",
                "meter_start_wh": 0,
                "register_start_wh": 106803.0,
                "started_at": "2026-11-09T23:30:20+00:00",
            }
        ],
    }
    hub.central.restore_charges({"TEST0001": ChargeMemory.from_dict(stored)})
    charger = await hub.charger()
    # A delayed sample: 106803 + 29000 is just below the stored register, so it is not shown.
    await charger.call("MeterValues", _periodic(77, "2026-11-10T13:00:00Z", 29000))
    await charger.call("StopTransaction", {"transactionId": 77, "timestamp": "2026-11-10T13:06:16Z"})

    assert readings.energy_kwh(hub.listener.latest) is None
    assert hub.listener.finished[0].energy_wh == 29000.0


# Intent: after a reset the next reading is taken as it is, even when it is far below the old register.
# Why: a replaced or reset meter would otherwise hold the energy sensor at the old register for ever.
async def test_a_reset_lets_a_new_meter_through(start_hub) -> None:
    hub = await start_hub()
    charger = await hub.charger()
    await charger.call("MeterValues", _clock("2026-11-10T13:15:00Z", 147.445))
    await charger.call("MeterValues", _clock("2026-11-10T13:30:00Z", 0.5))
    held = readings.energy_kwh(hub.listener.latest)
    hub.central.reset_register("TEST0001")
    cleared = readings.energy_kwh(hub.listener.latest)
    await charger.call("MeterValues", _clock("2026-11-10T13:45:00Z", 0.6))

    assert held == 147.445 and cleared is None
    assert readings.energy_kwh(hub.listener.latest) == 0.6


# Intent: after a restart with no charge running, the stored register shows at once instead of waiting for a reading.
# Why: an unknown energy sensor after every restart made the Energy dashboard report the entity as not available.
async def test_an_idle_register_shows_at_once_after_a_restart(start_hub) -> None:
    hub = await start_hub()
    hub.central.restore_charges({"TEST0001": ChargeMemory.from_dict({"register_wh": 147445.0, "charges": []})})
    charger = await hub.charger()
    shown = readings.energy_kwh(hub.listener.latest)
    await charger.call("MeterValues", _clock("2026-11-10T21:15:00Z", 40.642))

    assert shown == 147.445
    assert readings.energy_kwh(hub.listener.latest) == 147.445
