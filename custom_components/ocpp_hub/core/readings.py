"""How a charger snapshot reads as plain values: the states and numbers the Home Assistant layer shows."""

from __future__ import annotations

from datetime import datetime

from .model import ChargerSnapshot, ConnectorState

# The connector that carries the socket; connector 0 is the charger itself.
PRIMARY_CONNECTOR = 1

# OCPP 1.6 ChargePointStatus values as stable lower-case keys, so translations and automations never depend on the
# spelling the protocol uses.
CONNECTOR_STATUS_KEYS = {
    "Available": "available",
    "Preparing": "preparing",
    "Charging": "charging",
    "SuspendedEVSE": "suspended_evse",
    "SuspendedEV": "suspended_ev",
    "Finishing": "finishing",
    "Reserved": "reserved",
    "Unavailable": "unavailable",
    "Faulted": "faulted",
}
CONNECTOR_STATUS_OPTIONS = list(CONNECTOR_STATUS_KEYS.values())


# The status of the socket connector as a stable key, or None before the first status or for an unknown status.
# A status this table does not know is not guessed at: showing nothing beats showing something wrong.
def connector_status(snapshot: ChargerSnapshot) -> str | None:
    connector = snapshot.connectors.get(PRIMARY_CONNECTOR)
    return CONNECTOR_STATUS_KEYS.get(connector.status) if connector is not None else None


# The latest power drawn, in watts.
def power_w(snapshot: ChargerSnapshot) -> float | None:
    return snapshot.meter.power_w if snapshot.meter is not None else None


# The charger's energy register in kilowatt-hours; rounded to the watt-hour so float noise does not show as a change.
def energy_kwh(snapshot: ChargerSnapshot) -> float | None:
    if snapshot.meter is None or snapshot.meter.energy_wh is None:
        return None
    return round(snapshot.meter.energy_wh / 1000, 3)


# The latest current of one phase (L1, L2 or L3), in amperes.
def phase_current_a(snapshot: ChargerSnapshot, phase: str) -> float | None:
    return snapshot.meter.current_a.get(phase) if snapshot.meter is not None else None


# The latest voltage of one phase (L1, L2 or L3), in volts.
def phase_voltage_v(snapshot: ChargerSnapshot, phase: str) -> float | None:
    return snapshot.meter.voltage_v.get(phase) if snapshot.meter is not None else None


# The current the charger says it offers the car, in amperes.
def current_offered_a(snapshot: ChargerSnapshot) -> float | None:
    return snapshot.meter.current_offered_a if snapshot.meter is not None else None


# The connector that has a charge running, or None. Only the socket connector counts; connector 0 is the charger.
def _running_charge(snapshot: ChargerSnapshot) -> ConnectorState | None:
    connector = snapshot.connectors.get(PRIMARY_CONNECTOR)
    return connector if connector is not None and connector.transaction_id is not None else None


# The energy delivered in the charge that is running, in kilowatt-hours; None when no charge runs or the start reading
# is missing. Never negative, because a meter that was reset must not show a negative charge.
def session_energy_kwh(snapshot: ChargerSnapshot) -> float | None:
    charge = _running_charge(snapshot)
    if charge is None or charge.meter_start_wh is None:
        return None
    if snapshot.meter is None or snapshot.meter.energy_wh is None:
        return None
    return round(max(snapshot.meter.energy_wh - charge.meter_start_wh, 0.0) / 1000, 3)


# When the charge that is running started, or None.
def session_started(snapshot: ChargerSnapshot) -> datetime | None:
    charge = _running_charge(snapshot)
    return charge.started_at if charge is not None else None


# How long the charge that is running has lasted, in seconds, up to the latest meter reading; None when none runs.
def session_duration_s(snapshot: ChargerSnapshot) -> int | None:
    charge = _running_charge(snapshot)
    if charge is None or charge.started_at is None or snapshot.meter is None:
        return None
    return max(int((snapshot.meter.sampled_at - charge.started_at).total_seconds()), 0)


# Whether a charge is running on the socket connector.
def charge_running(snapshot: ChargerSnapshot) -> bool:
    return _running_charge(snapshot) is not None
