"""Immutable data shapes describing a charger as the hub sees it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Key used for a measurement that has no phase (the total, or a single-phase charger).
PHASE_TOTAL = "total"


@dataclass(frozen=True, slots=True)
class ChargerInfo:
    """Identity reported in BootNotification."""

    vendor: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None


@dataclass(frozen=True, slots=True)
class MeterSample:
    """One reading of the charger's meter. Units are fixed: watt, watt-hour, ampere, volt."""

    sampled_at: datetime
    power_w: float | None = None
    energy_wh: float | None = None
    current_a: Mapping[str, float] = field(default_factory=dict)
    voltage_v: Mapping[str, float] = field(default_factory=dict)
    current_offered_a: float | None = None


@dataclass(frozen=True, slots=True)
class ChargeStats:
    """What the meter readings of a charge that is running add up to. A new charge starts from an empty one."""

    samples: int = 0
    power_sum_w: float = 0.0
    max_power_w: float | None = None
    max_current_a: Mapping[str, float] = field(default_factory=dict)
    offered_a: float | None = None

    @property
    def average_power_w(self) -> float | None:
        """The mean of the power readings, or None when there were none."""
        return self.power_sum_w / self.samples if self.samples else None

    # Add one reading. Only the values the reading carries count: a reading with just the energy register changes
    # nothing here, so a clock-aligned energy-only reading cannot weigh the average down.
    def add(self, sample: MeterSample) -> ChargeStats:
        power = sample.power_w
        currents = dict(self.max_current_a)
        for phase, amps in sample.current_a.items():
            currents[phase] = max(amps, currents.get(phase, amps))
        offered = sample.current_offered_a
        return ChargeStats(
            samples=self.samples + (1 if power is not None else 0),
            power_sum_w=self.power_sum_w + (power or 0.0),
            max_power_w=self.max_power_w if power is None else max(power, self.max_power_w or power),
            max_current_a=currents,
            offered_a=self.offered_a if offered is None else max(offered, self.offered_a or offered),
        )


@dataclass(frozen=True, slots=True)
class ConnectorState:
    """Status of one connector, plus the transaction running on it."""

    connector_id: int
    status: str = "Unavailable"
    error_code: str = "NoError"
    info: str | None = None
    transaction_id: int | None = None
    id_tag: str | None = None
    meter_start_wh: float | None = None
    started_at: datetime | None = None
    stats: ChargeStats = field(default_factory=ChargeStats)


@dataclass(frozen=True, slots=True)
class ChargerSnapshot:
    """Everything the Home Assistant layer needs to show about one charger."""

    cp_id: str
    connected: bool = False
    protocol: str | None = None
    info: ChargerInfo = field(default_factory=ChargerInfo)
    features: frozenset[str] = frozenset()
    configuration: Mapping[str, str] = field(default_factory=dict)
    connectors: Mapping[int, ConnectorState] = field(default_factory=dict)
    meter: MeterSample | None = None
    last_seen: datetime | None = None
    # The current limit the hub last set and the profile purpose the charger accepted for it; None when the hub has set
    # none on this connection. The charger is not asked what it holds, so this is what the hub knows it wrote.
    charge_limit_a: float | None = None
    limit_purpose: str | None = None
    # The limit the vendor backend set through a command that was passed on (the lowest limit of its profile), the id of
    # that profile and its purpose. A charge profile of the vendor ends with its charge.
    vendor_limit_a: float | None = None
    vendor_profile_id: int | None = None
    vendor_purpose: str | None = None
    # What the charger itself says is in effect right now, from GetCompositeSchedule; None when it cannot say.
    composite_limit_a: float | None = None


@dataclass(frozen=True, slots=True)
class ChargeSession:
    """One finished charge: from StartTransaction to StopTransaction, with what the charger reported about it."""

    cp_id: str
    connector_id: int
    transaction_id: int
    id_tag: str | None
    started_at: datetime
    ended_at: datetime
    meter_start_wh: float | None = None
    meter_stop_wh: float | None = None
    energy_wh: float | None = None
    max_power_w: float | None = None
    average_power_w: float | None = None
    max_current_a: Mapping[str, float] = field(default_factory=dict)
    offered_a: float | None = None
    # The limit the hub had set on the charger when the charge ended, if any.
    limit_a: float | None = None
    stop_reason: str | None = None

    @property
    def duration_s(self) -> float:
        """Seconds between start and end; never negative, because a charger clock can be wrong."""
        return max((self.ended_at - self.started_at).total_seconds(), 0.0)

    # The session as plain JSON values, for storage and for service responses.
    def to_dict(self) -> dict[str, object]:
        return {
            "cp_id": self.cp_id,
            "connector_id": self.connector_id,
            "transaction_id": self.transaction_id,
            "id_tag": self.id_tag,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "meter_start_wh": self.meter_start_wh,
            "meter_stop_wh": self.meter_stop_wh,
            "energy_wh": self.energy_wh,
            "max_power_w": self.max_power_w,
            "average_power_w": self.average_power_w,
            "max_current_a": dict(self.max_current_a),
            "offered_a": self.offered_a,
            "limit_a": self.limit_a,
            "stop_reason": self.stop_reason,
        }

    # Rebuild a session from what to_dict wrote; a record that is damaged raises, so the caller can skip it.
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChargeSession:
        return cls(
            cp_id=str(data["cp_id"]),
            connector_id=int(data["connector_id"]),
            transaction_id=int(data["transaction_id"]),
            id_tag=data.get("id_tag"),
            started_at=datetime.fromisoformat(data["started_at"]),
            ended_at=datetime.fromisoformat(data["ended_at"]),
            meter_start_wh=data.get("meter_start_wh"),
            meter_stop_wh=data.get("meter_stop_wh"),
            energy_wh=data.get("energy_wh"),
            max_power_w=data.get("max_power_w"),
            average_power_w=data.get("average_power_w"),
            max_current_a=dict(data.get("max_current_a") or {}),
            offered_a=data.get("offered_a"),
            limit_a=data.get("limit_a"),
            stop_reason=data.get("stop_reason"),
        )


@dataclass(frozen=True, slots=True)
class LimitMemory:
    """The limits that survive a reconnect or a restart: the hub's own and the vendor's, as far as the hub knows."""

    hub_a: float | None = None
    hub_purpose: str | None = None
    vendor_a: float | None = None
    vendor_profile_id: int | None = None
    vendor_purpose: str | None = None

    @property
    def empty(self) -> bool:
        """True when there is nothing to remember."""
        return self.hub_a is None and self.vendor_a is None

    # The memory as plain JSON values.
    def to_dict(self) -> dict[str, object]:
        return {
            "hub_a": self.hub_a,
            "hub_purpose": self.hub_purpose,
            "vendor_a": self.vendor_a,
            "vendor_profile_id": self.vendor_profile_id,
            "vendor_purpose": self.vendor_purpose,
        }

    # Rebuild the memory from what to_dict wrote; a record with the wrong types raises, so the caller can skip it.
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LimitMemory:
        # A stored number as a float, or None when it is missing.
        def number(key: str) -> float | None:
            value = data.get(key)
            return float(value) if value is not None else None

        profile_id = data.get("vendor_profile_id")
        return cls(
            hub_a=number("hub_a"),
            hub_purpose=data.get("hub_purpose"),
            vendor_a=number("vendor_a"),
            vendor_profile_id=int(profile_id) if profile_id is not None else None,
            vendor_purpose=data.get("vendor_purpose"),
        )
