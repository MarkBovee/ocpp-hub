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
    max_power_w: float | None = None


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
            stop_reason=data.get("stop_reason"),
        )
