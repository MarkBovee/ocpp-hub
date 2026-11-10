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


# The reading as an absolute register. A clock-aligned reading is the register itself. Otherwise, during a charge with a
# known start above zero, a reading below that start cannot be the register (a register never goes back), so it is
# energy since the start. A reading at or above the start could be either; the smaller interpretation that does not go
# below the current register wins, because a relative reading only reaches the current register when the start is tiny
# compared to the charge (a young charger), and then the relative reading is the one that fits.
def as_absolute(
    value_wh: float, charge_start_wh: float | None, current_wh: float | None = None, clock: bool = False
) -> tuple[float, str]:
    if clock or charge_start_wh is None or charge_start_wh <= 0:
        return value_wh, KIND_ABSOLUTE
    relative = charge_start_wh + value_wh
    if value_wh < charge_start_wh:
        return relative, KIND_RELATIVE
    if current_wh is not None and value_wh < current_wh <= relative:
        return relative, KIND_RELATIVE
    return value_wh, KIND_ABSOLUTE


# The register after one reading. A reading that would make the register go back is not taken: Home Assistant reads a
# drop of a total_increasing sensor as a meter reset and the rise after it as new energy.
def next_register(
    current_wh: float | None, value_wh: float, charge_start_wh: float | None, clock: bool = False
) -> RegisterStep:
    absolute, kind = as_absolute(value_wh, charge_start_wh, current_wh, clock)
    if current_wh is not None and absolute < current_wh:
        return RegisterStep(register_wh=current_wh, accepted=False, kind=kind, absolute_wh=absolute)
    return RegisterStep(register_wh=absolute, accepted=True, kind=kind, absolute_wh=absolute)


# The register at the start of a charge, or None when it cannot be known yet. With the register known, a meterStart
# below it is energy since the start (the NEO sends 0), so the register is the start. Without it (a first connection,
# or only a stored register that may be behind after Home Assistant was down) only a meterStart above zero and not below
# the stored register is trusted; otherwise the start is found at the first clock-aligned reading (see rebased_start).
def charge_start_register(
    meter_start_wh: float | None, register_wh: float | None, floor_wh: float | None = None
) -> float | None:
    if register_wh is not None:
        if meter_start_wh is None or meter_start_wh < register_wh:
            return register_wh
        return meter_start_wh
    if meter_start_wh is not None and meter_start_wh > 0 and (floor_wh is None or meter_start_wh >= floor_wh):
        return meter_start_wh
    return None


# The register at the start of a charge whose start was unknown, from the first clock-aligned reading (the register)
# and the last reading counted from the start just before it. Never below zero.
def rebased_start(clock_wh: float, since_start_wh: float) -> float:
    return max(clock_wh - since_start_wh, 0.0)
