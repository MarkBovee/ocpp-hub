"""Turn the payload of a MeterValues request into a MeterSample with fixed units."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .model import PHASE_TOTAL, MeterSample

MEASURAND_ENERGY = "Energy.Active.Import.Register"
MEASURAND_POWER = "Power.Active.Import"
MEASURAND_CURRENT = "Current.Import"
MEASURAND_VOLTAGE = "Voltage"
MEASURAND_OFFERED = "Current.Offered"

# Factor to the base unit for the units chargers actually send; an unknown unit is skipped, never guessed.
_TO_BASE = {"W": 1.0, "kW": 1000.0, "Wh": 1.0, "kWh": 1000.0, "A": 1.0, "V": 1.0}
_DEFAULT_UNIT = {
    MEASURAND_ENERGY: "Wh",
    MEASURAND_POWER: "W",
    MEASURAND_CURRENT: "A",
    MEASURAND_VOLTAGE: "V",
    MEASURAND_OFFERED: "A",
}


# Reduce an OCPP phase label to the key used in a sample: L1, L2, L3, or "total" when there is no phase.
# Voltage is often reported as L1-N while current is L1; both must land under the same key.
def phase_key(phase: str | None) -> str | None:
    if not phase:
        return PHASE_TOTAL
    if phase == "N":
        return None
    if phase.endswith("-N"):
        return phase[:-2]
    return phase


# Parse the timestamp of a meter value, falling back to the receive time.
# Some chargers omit or garble the timestamp; the sample still needs a time to be useful.
def parse_timestamp(raw: object, fallback: datetime) -> datetime:
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return fallback
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return fallback


# Convert one sampledValue to a number in the base unit, or None when it cannot be used.
# Signed-data and unknown-unit values must be skipped instead of corrupting a reading.
def _number(sampled: dict[str, Any], measurand: str) -> float | None:
    unit = sampled.get("unit") or _DEFAULT_UNIT.get(measurand)
    factor = _TO_BASE.get(str(unit))
    if factor is None or sampled.get("format", "Raw") != "Raw":
        return None
    try:
        return float(sampled["value"]) * factor
    except KeyError, TypeError, ValueError:
        return None


# Build a MeterSample from the latest entry of a MeterValues payload.
# The hub shows the most recent reading; per-phase currents matter most because the mains limit is per phase.
def parse_meter_values(payload: dict[str, Any], now: datetime) -> MeterSample | None:
    entries = [entry for entry in payload.get("meterValue", []) if isinstance(entry, dict)]
    if not entries:
        return None
    latest = max(entries, key=lambda entry: parse_timestamp(entry.get("timestamp"), now))
    power: dict[str, float] = {}
    currents: dict[str, float] = {}
    voltages: dict[str, float] = {}
    energy: float | None = None
    offered: float | None = None
    for sampled in latest.get("sampledValue", []):
        if not isinstance(sampled, dict):
            continue
        measurand = str(sampled.get("measurand", MEASURAND_ENERGY))
        value = _number(sampled, measurand)
        key = phase_key(sampled.get("phase"))
        if value is None or key is None:
            continue
        if measurand == MEASURAND_ENERGY and key == PHASE_TOTAL:
            energy = value
        elif measurand == MEASURAND_POWER:
            power[key] = value
        elif measurand == MEASURAND_CURRENT:
            currents[key] = value
        elif measurand == MEASURAND_VOLTAGE:
            voltages[key] = value
        elif measurand == MEASURAND_OFFERED:
            offered = value if offered is None else max(offered, value)
    total_power = power.get(PHASE_TOTAL, sum(power.values()) if power else None)
    return MeterSample(
        sampled_at=parse_timestamp(latest.get("timestamp"), now),
        power_w=total_power,
        energy_wh=energy,
        current_a=currents,
        voltage_v=voltages,
        current_offered_a=offered,
    )


# Combine a new reading with the previous one. A reading with nothing but the energy register keeps everything else
# from the one before; any other reading replaces it as it is, apart from the energy register, which is kept when the
# new reading has none.
# The NEO sends such an energy-only reading on the clock (12:00, 12:30 and so on); replacing the whole reading with it
# made power, currents and the offered current vanish for fifteen seconds at a time. A full reading must replace the
# phases, though: a phase that is no longer reported (a one-phase car after a three-phase one) must not keep its value.
def merge_samples(previous: MeterSample | None, new: MeterSample) -> MeterSample:
    if previous is None:
        return new
    energy = new.energy_wh if new.energy_wh is not None else previous.energy_wh
    energy_only = new.power_w is None and not new.current_a and not new.voltage_v and new.current_offered_a is None
    if not energy_only:
        return replace(new, energy_wh=energy)
    return replace(previous, sampled_at=new.sampled_at, energy_wh=energy)
