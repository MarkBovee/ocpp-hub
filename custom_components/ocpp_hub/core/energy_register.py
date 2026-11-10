"""Keep one absolute, never-decreasing energy register per charger, whatever mix of readings the charger sends.

The Raedian NEO reports `Energy.Active.Import.Register` in two ways in the same stream: its periodic samples during a
charge count from zero at the start of that charge (and `meterStart` is 0), while its clock-aligned samples at the
quarter carry the real register. Shown as they arrive, the energy sensor jumped by the whole register four times an
hour, and Home Assistant counted every jump as new energy. This module turns both kinds into the absolute register.
"""

from __future__ import annotations

from dataclasses import dataclass

# How a reading was understood: as the absolute register, or as energy since the start of the running charge.
KIND_ABSOLUTE = "absolute"
KIND_RELATIVE = "relative"


@dataclass(frozen=True, slots=True)
class RegisterStep:
    """The outcome of one energy reading: the register to show, and whether the reading moved it."""

    register_wh: float | None
    accepted: bool
    kind: str
    # The reading as an absolute register, before the never-backwards check.
    absolute_wh: float


# The reading as an absolute register. A reading below the register at the start of the running charge cannot be the
# register (a register never goes back), so it is energy since that start. Without a charge, or with a start at zero,
# every reading is taken as it is.
def as_absolute(value_wh: float, charge_start_wh: float | None) -> tuple[float, str]:
    if charge_start_wh is not None and charge_start_wh > 0 and value_wh < charge_start_wh:
        return charge_start_wh + value_wh, KIND_RELATIVE
    return value_wh, KIND_ABSOLUTE


# The register after one reading. A reading that would make the register go back is not taken: Home Assistant reads a
# drop of a total_increasing sensor as a meter reset and the rise after it as new energy.
def next_register(current_wh: float | None, value_wh: float, charge_start_wh: float | None) -> RegisterStep:
    absolute, kind = as_absolute(value_wh, charge_start_wh)
    if current_wh is not None and absolute < current_wh:
        return RegisterStep(register_wh=current_wh, accepted=False, kind=kind, absolute_wh=absolute)
    return RegisterStep(register_wh=absolute, accepted=True, kind=kind, absolute_wh=absolute)


# The register at the start of a charge. A meterStart below the known register is energy since the start (the NEO sends
# 0), so the known register is the start; without a usable meterStart the known register is the best there is.
def charge_start_register(meter_start_wh: float | None, register_wh: float | None) -> float | None:
    if meter_start_wh is None:
        return register_wh
    if register_wh is not None and meter_start_wh < register_wh:
        return register_wh
    return meter_start_wh
