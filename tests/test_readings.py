"""How a snapshot reads as the values Home Assistant shows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from ocpp_hub_core import readings
from ocpp_hub_core.model import ChargerSnapshot, ConnectorState, MeterSample


# A snapshot with a meter reading and a socket connector in the given status.
def _snapshot(status: str | None = "Charging", **meter: Any) -> ChargerSnapshot:
    connectors = {0: ConnectorState(0, "Available")}
    if status is not None:
        connectors[1] = ConnectorState(1, status)
    sample = MeterSample(datetime(2026, 10, 20, tzinfo=UTC), **meter) if meter else None
    return ChargerSnapshot("TEST0001", connectors=connectors, meter=sample)


# Intent: every OCPP 1.6 connector status maps to its own stable key, and anything else shows as no state.
# Why: translations and automations depend on these keys; a guessed key for an unknown status would show wrong data.
@pytest.mark.parametrize(
    ("status", "key"),
    [
        ("Available", "available"),
        ("Preparing", "preparing"),
        ("Charging", "charging"),
        ("SuspendedEVSE", "suspended_evse"),
        ("SuspendedEV", "suspended_ev"),
        ("Finishing", "finishing"),
        ("Reserved", "reserved"),
        ("Unavailable", "unavailable"),
        ("Faulted", "faulted"),
        ("SomethingNew", None),
        (None, None),
    ],
)
def test_connector_status_keys(status: str | None, key: str | None) -> None:
    assert readings.connector_status(_snapshot(status)) == key
    assert sorted(readings.CONNECTOR_STATUS_OPTIONS) == sorted(set(readings.CONNECTOR_STATUS_KEYS.values()))


# Intent: the status comes from the socket connector, never from connector 0 (the charger itself).
# Why: connector 0 reports Available while the car charges on connector 1.
def test_status_uses_the_socket_connector_only() -> None:
    snapshot = ChargerSnapshot("TEST0001", connectors={0: ConnectorState(0, "Faulted")})

    assert readings.connector_status(snapshot) is None


# Intent: readings are taken from the latest meter sample in the units Home Assistant shows, and are None without one.
# Why: an entity must show unknown, not zero, before the first sample.
def test_meter_readings_and_their_units() -> None:
    snapshot = _snapshot(
        power_w=7400.0,
        energy_wh=1234567.0,
        current_a={"L1": 10.0, "L2": 11.5},
        voltage_v={"L1": 231.0},
        current_offered_a=16.0,
    )
    empty = _snapshot()

    assert readings.power_w(snapshot) == 7400.0 and readings.energy_kwh(snapshot) == 1234.567
    assert readings.phase_current_a(snapshot, "L2") == 11.5 and readings.phase_current_a(snapshot, "L3") is None
    assert readings.phase_voltage_v(snapshot, "L1") == 231.0 and readings.phase_voltage_v(snapshot, "L2") is None
    assert readings.current_offered_a(snapshot) == 16.0
    assert [
        readings.power_w(empty),
        readings.energy_kwh(empty),
        readings.phase_current_a(empty, "L1"),
        readings.phase_voltage_v(empty, "L1"),
        readings.current_offered_a(empty),
    ] == [None] * 5


# Intent: an energy register without a value reads as None, and float noise is rounded away.
# Why: a total_increasing sensor that flickers by 1e-13 kWh would write a new statistic at every sample.
def test_energy_without_a_value_and_rounding() -> None:
    assert readings.energy_kwh(_snapshot(power_w=1.0)) is None
    assert readings.energy_kwh(_snapshot(energy_wh=1000.0000000001)) == 1.0
