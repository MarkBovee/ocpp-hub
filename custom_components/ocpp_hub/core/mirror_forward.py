"""Which commands of the vendor backend reach the charger, and what is changed or refused on the way. Pure rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .control import HUB_PROFILE_ID, ControlPolicy
from .mirror_queue import is_integer
from .txid_map import TransactionIdMap

# The commands that may reach the charger when the owner turned forwarding on. Reset, UpdateFirmware and GetDiagnostics
# are not in the list on purpose: they stay refused until the owner decides otherwise. GetConfiguration and
# TriggerMessage are answered by the mirror from what it knows, never by the charger.
FORWARDED = frozenset(
    {
        "SetChargingProfile",
        "ClearChargingProfile",
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "ChangeConfiguration",
        "ChangeAvailability",
        "UnlockConnector",
        "DataTransfer",
    }
)

# Configuration keys the vendor may not change. A key that moves the charger to another backend or changes how it
# authenticates would cut the hub off, and the two metering keys are what the hub's entities live on.
_BLOCKED_KEY_PARTS = (
    "url",
    "uri",
    "host",
    "server",
    "authorization",
    "security",
    "password",
    "websocket",
    "centralsystem",
    "chargepointid",
    "certificate",
)
_BLOCKED_KEYS = frozenset({"metervaluessampleddata", "metervaluesampleinterval"})
# Three phases at the nominal voltage, to compare a profile in watts with the ceiling in amperes.
_WATTS_PER_AMP = 230.0 * 3


@dataclass(frozen=True, slots=True)
class Forward:
    """Send this to the charger. The payload already carries the hub's transaction id."""

    action: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Refuse:
    """Do not send it: answer the vendor with this payload, and say why in the log."""

    answer: dict[str, Any]
    reason: str


# Decide what happens to one vendor command. Anything that is not in FORWARDED is not decided here.
# The checks are on the way in, so a refused command never reaches the charger.
def plan_forward(
    action: str, payload: dict[str, Any], policy: ControlPolicy, transactions: TransactionIdMap
) -> Forward | Refuse:
    match action:
        case "SetChargingProfile":
            return _plan_profile(payload, policy, transactions)
        case "ClearChargingProfile":
            return _plan_clear(payload)
        case "RemoteStopTransaction":
            return _plan_stop(payload, transactions)
        case "ChangeConfiguration":
            return _plan_configuration(payload)
        case "UnlockConnector":
            return Forward(action, payload)
        case _:
            return Forward(action, payload)


# The refusal answer a command uses when the mirror says no.
def _refusal(action: str) -> dict[str, Any]:
    return {"status": "UnlockFailed" if action == "UnlockConnector" else "Rejected"}


# A charging profile is passed on only when no period asks for more than the ceiling and it does not reuse the hub's
# own profile id. A profile for a transaction is translated to the hub's transaction id, or refused when it is unknown.
def _plan_profile(payload: dict[str, Any], policy: ControlPolicy, transactions: TransactionIdMap) -> Forward | Refuse:
    profile = payload.get("csChargingProfiles")
    schedule = profile.get("chargingSchedule") if isinstance(profile, dict) else None
    periods = schedule.get("chargingSchedulePeriod") if isinstance(schedule, dict) else None
    if not isinstance(profile, dict) or not isinstance(schedule, dict) or not isinstance(periods, list) or not periods:
        return Refuse(_refusal("SetChargingProfile"), "the profile has no schedule")
    if profile.get("chargingProfileId") == HUB_PROFILE_ID:
        return Refuse(_refusal("SetChargingProfile"), "the profile id belongs to the hub")
    ceiling = policy.max_current_a * (_WATTS_PER_AMP if schedule.get("chargingRateUnit") == "W" else 1.0)
    for period in periods:
        limit = period.get("limit") if isinstance(period, dict) else None
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit < 0:
            return Refuse(_refusal("SetChargingProfile"), "a period has no usable limit")
        if limit > ceiling:
            return Refuse(_refusal("SetChargingProfile"), f"a period asks for {limit:g}, above the ceiling {ceiling:g}")
    forwarded = dict(payload)
    vendor_transaction = profile.get("transactionId")
    if vendor_transaction is not None:
        hub_transaction = transactions.to_hub(vendor_transaction) if is_integer(vendor_transaction) else None
        if hub_transaction is None:
            return Refuse(_refusal("SetChargingProfile"), "the profile names a transaction the hub does not know")
        forwarded["csChargingProfiles"] = {**profile, "transactionId": hub_transaction}
    return Forward("SetChargingProfile", forwarded)


# Clearing profiles is passed on, except for the hub's own id, which only the hub removes. A clear without an id would
# also remove the hub's profile; the caller lets the snapshot forget the hub's limit when that is accepted.
def _plan_clear(payload: dict[str, Any]) -> Forward | Refuse:
    if payload.get("id") == HUB_PROFILE_ID:
        return Refuse({"status": "Unknown"}, "the profile id belongs to the hub")
    return Forward("ClearChargingProfile", payload)


# A stop names the vendor's transaction id; the charger knows the hub's. An id the hub does not know is refused,
# because passing it on could stop another charge.
def _plan_stop(payload: dict[str, Any], transactions: TransactionIdMap) -> Forward | Refuse:
    vendor_transaction = payload.get("transactionId")
    hub_transaction = transactions.to_hub(vendor_transaction) if is_integer(vendor_transaction) else None
    if hub_transaction is None:
        return Refuse(_refusal("RemoteStopTransaction"), "the transaction is not one the hub knows")
    return Forward("RemoteStopTransaction", {**payload, "transactionId": hub_transaction})


# A configuration change is passed on unless the key could cut the hub off or is one of the hub's metering keys.
def _plan_configuration(payload: dict[str, Any]) -> Forward | Refuse:
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        return Refuse(_refusal("ChangeConfiguration"), "the key is missing")
    lowered = key.lower()
    if lowered in _BLOCKED_KEYS or any(part in lowered for part in _BLOCKED_KEY_PARTS):
        return Refuse(_refusal("ChangeConfiguration"), f"the key {key} may not be changed from outside")
    return Forward("ChangeConfiguration", payload)
